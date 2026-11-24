"""Explicit local Schwab OAuth commands, separate from the GET-only broker client.

Run with ``python -m qte.brokers.schwab_auth login`` from the checkout.
No import-time IO, background refresh, order operations or GUI credential inputs.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import getpass
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener
import webbrowser

from .readonly import BrokerConnectionError, ReadResponse, SchwabReadOnlyClient, _NoRedirect, _text
from .schwab_capture import capture_read

CALLBACK = 'https://127.0.0.1:8182'
AUTHORIZE = 'https://api.schwabapi.com/v1/oauth/authorize'
TOKEN = 'https://api.schwabapi.com/v1/oauth/token'
MAX_TOKEN_BYTES = 65536


class AuthError(RuntimeError):
    """Messages contain no provider bodies, credentials or callback queries."""


def app_fingerprint(app_key):
    return hashlib.sha256(_text(app_key, 'app key', maximum=1024).encode()).hexdigest()


def authorization_url(app_key, state):
    _text(state, 'OAuth state', maximum=256)
    return AUTHORIZE + '?' + urlencode({'response_type': 'code', 'client_id':
                                       _text(app_key, 'app key', maximum=1024),
                                       'redirect_uri': CALLBACK, 'state': state})


def callback_code(target, host, expected_state):
    """Parse once: preserve literal '+' encoded as %2B; reject replay ambiguities."""
    if host != '127.0.0.1:8182' or not isinstance(target, str) or len(target) > 16384:
        raise AuthError('Invalid callback destination.')
    p = urlsplit(target)
    if p.scheme or p.netloc or p.path != '/' or p.fragment:
        raise AuthError('Invalid callback path.')
    try:
        query = parse_qs(p.query, keep_blank_values=True, strict_parsing=True,
                         max_num_fields=12, errors='strict')
    except (ValueError, UnicodeError):
        raise AuthError('Invalid callback query.') from None
    state = query.get('state', [])
    if len(state) != 1 or not secrets.compare_digest(state[0].encode(), expected_state.encode()):
        raise AuthError('OAuth state mismatch; callback ignored.')
    if 'error' in query:
        raise AuthError('Schwab authorization was denied; restart login.')
    code = query.get('code', [])
    if len(code) != 1:
        raise AuthError('Missing or duplicated authorization code.')
    try:
        return _text(code[0], 'authorization code', maximum=8192)
    except ValueError:
        raise AuthError('Invalid authorization code.') from None


@dataclass(frozen=True)
class TokenRequest:
    headers: dict = field(repr=False)
    body: bytes = field(repr=False)


def token_transport(request):
    # Only OAuth grants can POST, to this one pinned URL. Never follow redirects.
    req = Request(TOKEN, headers=request.headers, data=request.body, method='POST')
    try:
        with build_opener(_NoRedirect()).open(req, timeout=20) as response:
            return ReadResponse(response.status, response.read(MAX_TOKEN_BYTES + 1))
    except HTTPError as error:
        status = error.code
        error.close()
        return ReadResponse(status, b'')


def validate_token(token):
    if not isinstance(token, dict):
        raise AuthError('Invalid token response.')
    try:
        for name in ('access_token', 'refresh_token'):
            _text(token.get(name), name, maximum=16384)
        if token.get('token_type', '').lower() != 'bearer':
            raise ValueError()
        lifetime = token.get('expires_in')
        if type(lifetime) not in (int, float) or not math.isfinite(lifetime) or not 0 < lifetime <= 86400:
            raise ValueError()
    except (ValueError, AttributeError, OverflowError):
        raise AuthError('Invalid token response fields.') from None
    return {k: token[k] for k in ('access_token', 'refresh_token', 'token_type', 'expires_in')}


def exchange(app_key, app_secret, *, code=None, refresh_token=None, transport=None, clock=time.time):
    if (code is None) == (refresh_token is None):
        raise ValueError('Specify exactly one OAuth grant.')
    _text(app_key, 'app key', maximum=1024)
    _text(app_secret, 'app secret', maximum=4096)
    if ':' in app_key:
        raise ValueError('Invalid app key.')
    fields = ({'grant_type': 'authorization_code', 'code': _text(code, 'code', maximum=8192),
               'redirect_uri': CALLBACK} if code is not None else
              {'grant_type': 'refresh_token', 'refresh_token': _text(refresh_token, 'refresh token', maximum=16384)})
    headers = {'Authorization': 'Basic ' + base64.b64encode((app_key + ':' + app_secret).encode()).decode(),
               'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
    started = clock()  # Conservative expiry: request start, not response receipt.
    try:
        response = (transport or token_transport)(TokenRequest(headers, urlencode(fields).encode()))
    except Exception:
        raise AuthError('Schwab token transport failed; diagnostics withheld.') from None
    if not isinstance(response, ReadResponse) or type(response.status) is not int:
        raise AuthError('Invalid token transport response.')
    if response.status != 200:
        raise AuthError(f'Schwab token request returned HTTP {response.status}; restart login if authorization expired.')
    if not isinstance(response.body, bytes) or len(response.body) > MAX_TOKEN_BYTES:
        raise AuthError('Token response exceeded size limit.')
    try:
        token = json.loads(response.body)
    except (ValueError, UnicodeError, RecursionError):
        raise AuthError('Invalid token JSON.') from None
    # OAuth permits refresh responses to omit the unchanged refresh token.
    if isinstance(token, dict) and refresh_token is not None and 'refresh_token' not in token:
        token['refresh_token'] = refresh_token
    token = validate_token(token)
    return {'schema_version': 1, 'app_sha256': app_fingerprint(app_key), 'callback': CALLBACK,
            'obtained_at': started, 'expires_at': started + token['expires_in'], 'token': token}


class TokenStore:
    """POSIX owner-only local storage; refuse unsupported ACL protection explicitly."""
    def __init__(self, repo):
        if os.name != 'posix':
            raise AuthError('Schwab token storage currently requires POSIX permissions; Windows ACL support is pending.')
        repo = Path(repo).resolve()
        if not (repo / 'pyproject.toml').is_file() or not (repo / 'AGENTS.md').is_file():
            raise AuthError('Run from the QTE repository root.')
        cache = repo / '.cache'
        if cache.is_symlink():
            raise AuthError('Refusing symlinked credential directory.')
        cache.mkdir(mode=0o700, exist_ok=True)
        self.directory = cache / 'schwab'
        if self.directory.is_symlink():
            raise AuthError('Refusing symlinked credential directory.')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AuthError('Credential directory must be owned by you with mode 0700.')
        self.path = self.directory / 'token.json'

    @contextmanager
    def lock(self):
        import fcntl
        fd = os.open(self.directory / 'session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                raise AuthError('Unsafe credential lock file.')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AuthError('Another Schwab command is active; finish it first.') from None
            yield
        finally:
            os.close(fd)

    def load(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise AuthError('Unsafe token file permissions or file type.')
            raw = stream.read(MAX_TOKEN_BYTES + 1)
        if len(raw) > MAX_TOKEN_BYTES:
            raise AuthError('Token file exceeds size limit.')
        try:
            result = json.loads(raw)
            if result['schema_version'] != 1 or result['callback'] != CALLBACK:
                raise ValueError()
            validate_token(result['token'])
            for key in ('obtained_at', 'expires_at'):
                if type(result[key]) not in (int, float) or not math.isfinite(result[key]):
                    raise ValueError()
            if result['expires_at'] <= result['obtained_at']:
                raise ValueError()
            if not isinstance(result['app_sha256'], str) or len(result['app_sha256']) != 64:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AuthError, OverflowError):
            raise AuthError('Invalid local token file; authorize again.') from None
        return result

    def save(self, document):
        raw = json.dumps(document, allow_nan=False).encode()
        if len(raw) > MAX_TOKEN_BYTES:
            raise AuthError('Token document exceeds size limit.')
        if self.path.is_symlink():
            raise AuthError('Refusing symlinked token file.')
        fd, name = tempfile.mkstemp(prefix='token-', suffix='.tmp', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


@contextmanager
def local_tls(directory):
    openssl = shutil.which('openssl')
    if not openssl:
        raise AuthError('OpenSSL is required for local TLS; nothing was installed.')
    with tempfile.TemporaryDirectory(prefix='oauth-', dir=directory) as temporary:
        cert, key = Path(temporary) / 'cert.pem', Path(temporary) / 'key.pem'
        command = [openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes',
                   '-days', '1', '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
                   '-keyout', str(key), '-out', str(cert)]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise AuthError('Unable to generate local TLS certificate.') from None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        der = ssl.PEM_cert_to_DER_cert(cert.read_text())
        yield context, hashlib.sha256(der).hexdigest()


def callback_server(context, state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback query contains the authorization code.

        def do_GET(self):
            try:
                if self.server.code is not None:
                    raise AuthError('Callback already consumed.')
                code = callback_code(self.path, self.headers.get('Host'), state)
            except (AuthError, ValueError):
                status, body = 400, b'Callback rejected. Check the QTE terminal or restart login.'
            else:
                self.server.code = code
                status, body = 200, b'Callback received. Check the QTE terminal for authorization result. You may close this tab.'
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

    class Server(HTTPServer):
        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(2)
            try:
                return context.wrap_socket(sock, server_side=True), address
            except Exception:
                sock.close()
                raise

        def handle_error(self, *args):
            pass  # Never log request/traceback containing authorization material.

    server = Server(('127.0.0.1', 8182), Handler)
    server.timeout, server.code = .5, None
    return server


def login(store, app_key, app_secret, *, timeout=300):
    state = secrets.token_urlsafe(32)
    url = authorization_url(app_key, state)
    with local_tls(store.directory) as (context, fingerprint):
        with callback_server(context, state) as server:
            print('Opening Schwab authorization. Sign in only on Schwab’s website.')
            print('The local return page uses a temporary self-signed certificate at ' + CALLBACK)
            print('Local certificate SHA-256: ' + fingerprint)
            print('If needed, permit this certificate only for that exact loopback address.')
            print('If the browser does not open, open this authorization URL locally:\n' + url)
            webbrowser.open(url)
            deadline = time.monotonic() + timeout
            while server.code is None and time.monotonic() < deadline:
                server.handle_request()
            if server.code is None:
                raise AuthError('Authorization timed out; restart login.')
            document = exchange(app_key, app_secret, code=server.code)
            store.save(document)


def credentials():
    if not sys.stdin.isatty():
        raise AuthError('Run interactively in your local terminal; credentials are not accepted on command lines.')
    return getpass.getpass('Schwab App Key (hidden): ').strip(), getpass.getpass('Schwab App Secret (hidden): ').strip()


def market_client(document, contract, *, clock=time.time):
    if document['expires_at'] <= clock() + 30:
        raise AuthError('Access token expired or nearly expired; run refresh first.')
    if contract.get('servers') != [{'url': 'https://api.schwabapi.com/marketdata/v1'}]:
        raise AuthError('This command requires a market-data-only OpenAPI contract.')
    return SchwabReadOnlyClient(document['token']['access_token'], openapi=contract)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('login', help='Authorize in browser; replace any existing local token after success')
    sub.add_parser('refresh', help='Explicitly refresh the stored token; app credentials prompted privately')
    sub.add_parser('status', help='Inspect local token expiry only; no network or secret output')
    read = sub.add_parser('read', help='One market-data GET from a verified local contract')
    read.add_argument('--contract', type=Path, required=True)
    read.add_argument('--operation', required=True)
    read.add_argument('--query', default='{}', help='JSON query parameters; no credentials')
    read.add_argument('--path-parameters', default='{}')
    read.add_argument('--output', type=Path, help='Save one new market-data capture with receipt time and hashes')
    args = parser.parse_args(argv)
    try:
        store = TokenStore(args.repo_root)
        with store.lock():
            if args.command == 'login':
                login(store, *credentials())
                print('Schwab authorization saved locally. No market/account request was made.')
            else:
                try:
                    doc = store.load()
                except FileNotFoundError:
                    raise AuthError('No local Schwab authorization saved. Run login in your local terminal.') from None
                if args.command == 'status':
                    print(json.dumps({'local_token_present': True, 'access_expires_at_unix': doc['expires_at'],
                                      'access_expired': doc['expires_at'] <= time.time(),
                                      'provider_validity': 'not_checked', 'callback': CALLBACK}))
                elif args.command == 'refresh':
                    key, secret = credentials()
                    if not secrets.compare_digest(doc['app_sha256'], app_fingerprint(key)):
                        raise AuthError('App key differs from the stored authorization.')
                    updated = exchange(key, secret, refresh_token=doc['token']['refresh_token'])
                    store.save(updated)
                    print('Schwab token refreshed and saved locally.')
                else:
                    try:
                        if args.contract.stat().st_size > 4 * 1024 * 1024:
                            raise AuthError('Contract exceeds size limit.')
                    except FileNotFoundError:
                        raise AuthError('Market Data OpenAPI file is missing. Supply the official export via --contract.') from None
                    client = market_client(doc, json.loads(args.contract.read_text()))
                    parameters = dict(query_parameters=json.loads(args.query),
                                      path_parameters=json.loads(args.path_parameters))
                    if args.output is None:
                        print(json.dumps(client.get(args.operation, **parameters), allow_nan=False))
                    else:
                        try:
                            response_hash = capture_read(client, args.operation, args.output, **parameters)
                        except FileExistsError:
                            raise AuthError('Capture output already exists. Choose a new path.') from None
                        except FileNotFoundError:
                            raise AuthError('Create the capture output directory before running read.') from None
                        print(json.dumps({'capture': str(args.output), 'response_sha256': response_hash}))
        return 0
    except AuthError as error:
        print('qte schwab: ' + str(error), file=sys.stderr)
    except (OSError, ValueError, TypeError, AttributeError, BrokerConnectionError):
        print('qte schwab: operation failed; check local prerequisites, contract and authorization. Sensitive diagnostics omitted.', file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print('qte schwab: canceled.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
