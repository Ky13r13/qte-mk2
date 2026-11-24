"""OAuth protocol/storage regressions: fake grants, no Schwab calls or credentials."""
import base64
from contextlib import closing
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import ssl
import threading
import traceback
from urllib.parse import parse_qs, urlsplit

import pytest

from qte.brokers import ReadResponse
from qte.brokers import schwab_auth as auth

KEY, SECRET, CODE = 'test-app-key', 'test-app-secret', 'one+code/with=symbols'
TOKEN = {'access_token': 'test-access', 'refresh_token': 'test-refresh',
         'token_type': 'Bearer', 'expires_in': 1800}


def grant(**kwargs):
    return auth.exchange(KEY, SECRET, code=CODE,
                         transport=lambda _: ReadResponse(200, json.dumps(TOKEN).encode()),
                         clock=lambda: 1000, **kwargs)


@pytest.fixture
def store(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="qte"\n')
    (tmp_path / 'AGENTS.md').write_text('test fixture')
    return auth.TokenStore(tmp_path)


def test_authorization_url_and_callback_exact_once_decode():
    url = auth.authorization_url(KEY, 'expected-state')
    assert urlsplit(url).netloc == 'api.schwabapi.com'
    fields = parse_qs(urlsplit(url).query)
    assert fields == {'response_type': ['code'], 'client_id': [KEY],
                      'redirect_uri': [auth.CALLBACK], 'state': ['expected-state']}
    assert auth.callback_code('/?code=one%2Bcode%2Fwith%3Dsymbols&state=expected-state',
                              '127.0.0.1:8182', 'expected-state') == CODE


@pytest.mark.parametrize('target,host', [
    ('/?code=x&state=bad', '127.0.0.1:8182'),
    ('/?code=x', '127.0.0.1:8182'),
    ('/?code=x&state=s&state=s', '127.0.0.1:8182'),
    ('/?code=x&code=y&state=s', '127.0.0.1:8182'),
    ('/?error=access_denied&state=s', '127.0.0.1:8182'),
    ('/?code=x&state=s', 'attacker.example'),
    ('https://127.0.0.1:8182/?code=x&state=s', '127.0.0.1:8182'),
    ('//attacker.example/?code=x&state=s', '127.0.0.1:8182'),
    ('/other?code=x&state=s', '127.0.0.1:8182'),
    ('/?code=&state=s', '127.0.0.1:8182'),
    ('/?code=%0A&state=s', '127.0.0.1:8182'),
])
def test_callback_rejects_wrong_state_host_path_denial_and_duplicates(target, host):
    with pytest.raises(auth.AuthError):
        auth.callback_code(target, host, 's')


def test_code_exchange_and_refresh_rotation():
    requests = []
    def transport(request):
        requests.append(request)
        return ReadResponse(200, json.dumps(TOKEN).encode())
    doc = auth.exchange(KEY, SECRET, code=CODE, transport=transport, clock=lambda: 1000)
    assert parse_qs(requests[0].body.decode()) == {
        'grant_type': ['authorization_code'], 'code': [CODE], 'redirect_uri': [auth.CALLBACK]}
    assert requests[0].headers['Authorization'] == 'Basic ' + base64.b64encode(f'{KEY}:{SECRET}'.encode()).decode()
    assert SECRET not in repr(requests[0]) and CODE not in repr(requests[0])
    assert doc['expires_at'] == 2800
    assert doc['app_sha256'] == auth.app_fingerprint(KEY)
    rotated = auth.exchange(KEY, SECRET, refresh_token='old-token', transport=transport)
    assert parse_qs(requests[1].body.decode()) == {'grant_type': ['refresh_token'], 'refresh_token': ['old-token']}
    assert rotated['token']['refresh_token'] == 'test-refresh'
    no_rotation = {k: v for k, v in TOKEN.items() if k != 'refresh_token'}
    unchanged = auth.exchange(KEY, SECRET, refresh_token='old-token',
                             transport=lambda _: ReadResponse(200, json.dumps(no_rotation).encode()))
    assert unchanged['token']['refresh_token'] == 'old-token'


@pytest.mark.parametrize('response', [ReadResponse(302, SECRET.encode()), ReadResponse(401, SECRET.encode()),
    ReadResponse(200, b'not json'), ReadResponse(200, b'[]'), ReadResponse(200, b'{}'),
    ReadResponse(200, b'x' * (auth.MAX_TOKEN_BYTES + 1)), ReadResponse(True, b'{}')])
def test_failed_grant_is_redacted(response):
    with pytest.raises(auth.AuthError) as error:
        auth.exchange(KEY, SECRET, code=CODE, transport=lambda _: response)
    formatted = ''.join(traceback.format_exception(error.value))
    assert SECRET not in formatted and CODE not in formatted


def test_transport_failure_redacts_provider_exception():
    def broken(_):
        raise RuntimeError(SECRET + CODE)
    with pytest.raises(auth.AuthError) as error:
        auth.exchange(KEY, SECRET, code=CODE, transport=broken)
    formatted = ''.join(traceback.format_exception(error.value))
    assert SECRET not in formatted and CODE not in formatted


def test_http_token_transport_pins_post_url_and_redirect_policy(monkeypatch):
    seen = []
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self, size):
            assert size == auth.MAX_TOKEN_BYTES + 1
            return json.dumps(TOKEN).encode()
    class Opener:
        def open(self, request, *, timeout):
            seen.append(request)
            assert timeout == 20
            return Response()
    def opener(handler):
        assert isinstance(handler, auth._NoRedirect)
        assert handler.redirect_request(None, None, 302, '', {}, 'https://attacker.example') is None
        return Opener()
    monkeypatch.setattr(auth, 'build_opener', opener)
    auth.exchange(KEY, SECRET, code=CODE)
    assert seen[0].full_url == auth.TOKEN
    assert seen[0].get_method() == 'POST'
    assert CODE not in seen[0].full_url and SECRET not in seen[0].full_url


def test_malformed_local_token_is_redacted(store):
    doc = grant()
    doc['expires_at'] = 'secret-corrupt-value'
    store.save(doc)
    with pytest.raises(auth.AuthError) as error:
        store.load()
    assert 'secret-corrupt-value' not in str(error.value)


@pytest.mark.parametrize('field,value', [('expires_in', True), ('expires_in', -1), ('expires_in', float('inf')),
                                       ('token_type', 'basic'), ('access_token', ''), ('refresh_token', None)])
def test_invalid_token_fields(field, value):
    token = dict(TOKEN, **{field: value})
    with pytest.raises(auth.AuthError):
        auth.validate_token(token)


def test_atomic_private_storage_and_exclusive_lock(store):
    doc = grant()
    with store.lock():
        store.save(doc)
        assert store.load() == doc
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.directory.stat().st_mode & 0o777 == 0o700
        with pytest.raises(auth.AuthError, match='active'):
            with store.lock():
                pass
    with store.lock():
        store.save({**doc, 'obtained_at': 1100, 'expires_at': 2900})
    assert store.load()['expires_at'] == 2900
    assert not list(store.directory.glob('token-*.tmp'))


def test_failed_refresh_does_not_destroy_previous_token(store, monkeypatch):
    store.save(grant())
    before = store.path.read_bytes()
    monkeypatch.setattr(auth, 'credentials', lambda: (KEY, SECRET))
    monkeypatch.setattr(auth, 'token_transport', lambda _: ReadResponse(401, b'secret-error'))
    assert auth.main(['--repo-root', str(store.directory.parent.parent), 'refresh']) == 2
    assert store.path.read_bytes() == before


def test_storage_rejects_symlink_and_permissive_file(store, tmp_path):
    outside = tmp_path / 'other'
    outside.write_text('user work')
    store.path.symlink_to(outside)
    with pytest.raises(auth.AuthError, match='symlink'):
        store.save(grant())
    assert outside.read_text() == 'user work'
    store.path.unlink()
    store.save(grant())
    store.path.chmod(0o644)
    with pytest.raises(auth.AuthError, match='permissions'):
        store.load()


def test_store_rejects_symlinked_cache_and_directory(tmp_path):
    (tmp_path / 'pyproject.toml').touch()
    (tmp_path / 'AGENTS.md').touch()
    destination = tmp_path / 'elsewhere'
    destination.mkdir()
    (tmp_path / '.cache').symlink_to(destination)
    with pytest.raises(auth.AuthError, match='symlink'):
        auth.TokenStore(tmp_path)


def test_expiry_and_trading_contract_rejected_before_read():
    contract = {'servers': [{'url': 'https://api.schwabapi.com/marketdata/v1'}],
                'paths': {'/quotes': {'get': {'operationId': 'quotes'}}}}
    assert auth.market_client(grant(), contract, clock=lambda: 1000).operations == ('quotes',)
    with pytest.raises(auth.AuthError, match='expired'):
        auth.market_client(grant(), contract, clock=lambda: 2790)
    contract['servers'][0]['url'] = 'https://api.schwabapi.com/trader/v1'
    with pytest.raises(auth.AuthError, match='market-data-only'):
        auth.market_client(grant(), contract, clock=lambda: 1000)


def test_status_never_displays_token_or_makes_network_call(store, monkeypatch, capsys):
    store.save(grant())
    monkeypatch.setattr(auth, 'token_transport', lambda _: pytest.fail('Unexpected network'))
    assert auth.main(['--repo-root', str(store.directory.parent.parent), 'status']) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['provider_validity'] == 'not_checked'
    assert 'test-access' not in output and 'test-refresh' not in output


def test_missing_token_explains_local_login(store, capsys):
    assert auth.main(['--repo-root', str(store.directory.parent.parent), 'status']) == 2
    assert 'No local Schwab authorization saved' in capsys.readouterr().err


def test_market_capture_records_one_get_and_refuses_to_overwrite(store, monkeypatch, capsys, tmp_path):
    from qte.brokers import readonly

    store.save({**grant(), 'obtained_at': 1, 'expires_at': 2**40})
    contract = tmp_path / 'market.json'
    contract.write_text(json.dumps({
        'servers': [{'url': 'https://api.schwabapi.com/marketdata/v1'}],
        'paths': {'/quotes': {'get': {'operationId': 'testQuotes', 'parameters': [
            {'name': 'symbols', 'in': 'query', 'required': True},
        ]}}},
    }))
    response = b'{"SPY":{"ask":101,"bid":100}}'
    requests = []

    def transport(request):
        requests.append(request)
        return ReadResponse(200, response)

    monkeypatch.setattr(readonly, '_transport', transport)
    output = tmp_path / 'capture.json'
    args = ['--repo-root', str(tmp_path), 'read', '--contract', str(contract),
            '--operation', 'testQuotes', '--query', '{"symbols":"SPY"}', '--output', str(output)]
    assert auth.main(args) == 0
    capture = json.loads(output.read_bytes())
    assert capture['response'] == json.loads(response)
    assert capture['response_canonical_json_sha256'] == sha256(response).hexdigest()
    assert capture['response_received_utc_ns'] >= capture['request_started_utc_ns']
    assert capture['request']['query_parameters'] == {'symbols': 'SPY'}
    assert requests[0].url == 'https://api.schwabapi.com/marketdata/v1/quotes?symbols=SPY'
    assert 'test-access' not in output.read_text()
    original = output.read_bytes()
    assert auth.main(args) == 2
    assert 'already exists' in capsys.readouterr().err
    assert output.read_bytes() == original
    assert len(requests) == 1


def test_real_loopback_tls_callback_rejects_bad_state_and_replay(store, capsys):
    # Trust only this temporary local certificate, never disable TLS verification.
    with auth.local_tls(store.directory) as (context, fingerprint):
        cert = next(store.directory.glob('oauth-*/cert.pem'))
        client_context = ssl.create_default_context(cafile=str(cert))
        assert len(fingerprint) == 64
        with auth.callback_server(context, 's') as server:
            assert server.server_address == ('127.0.0.1', 8182)
            def requests():
                for _ in range(3):
                    server.handle_request()
            thread = threading.Thread(target=requests)
            thread.start()
            try:
                for target, expected in [('/?code=bad&state=no', 400), ('/?code=good&state=s', 200),
                                         ('/?code=replayed&state=s', 400)]:
                    with closing(http.client.HTTPSConnection('127.0.0.1', 8182, context=client_context, timeout=5)) as connection:
                        connection.request('GET', target)
                        response = connection.getresponse()
                        assert response.status == expected
                        assert response.getheader('Cache-Control') == 'no-store'
                        assert b'good' not in response.read()
            finally:
                thread.join(timeout=5)
            assert not thread.is_alive()
            assert server.code == 'good'
    assert not list(store.directory.glob('oauth-*'))
    assert 'code=' not in capsys.readouterr().err
