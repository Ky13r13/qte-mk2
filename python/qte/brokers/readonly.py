"""Minimal GET-only clients, isolated from QTE accounting and order submission.

Provider JSON stays at this boundary. Tests inject transports/signers; creating
a client neither makes a request nor reads, persists or refreshes credentials.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from types import MappingProxyType
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 4 * 1024 * 1024
HOSTS = frozenset(("trading.robinhood.com", "api.schwabapi.com"))


class BrokerConnectionError(RuntimeError):
    pass


def _text(value, label, *, maximum=4096):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(c) < 32 or ord(c) > 126 for c in value)):
        raise ValueError(f"invalid {label}")
    return value


def _url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc not in HOSTS or parsed.fragment
            or parsed.username or parsed.password or len(value) > 16384
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError("request must use a pinned HTTPS broker host")
    return value


@dataclass(frozen=True)
class ReadRequest:
    url: str = field(repr=False)
    headers: dict[str, str] = field(repr=False)

    def __post_init__(self):
        _url(self.url)
        values = dict(self.headers)
        for key, value in values.items():
            _text(key, "header name", maximum=128)
            if re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None:
                raise ValueError("invalid header name")
            _text(value, "header value", maximum=8192)
        object.__setattr__(self, "headers", MappingProxyType(values))


@dataclass(frozen=True)
class ReadResponse:
    status: int
    body: bytes = field(repr=False)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _transport(request):
    # urllib's default redirect handler would forward sensitive request headers.
    opener = build_opener(_NoRedirect())
    req = Request(_url(request.url), headers=dict(request.headers), method="GET")
    try:
        with opener.open(req, timeout=20) as response:
            return ReadResponse(response.status, response.read(MAX_BYTES + 1))
    except HTTPError as error:
        status = error.code
        error.close()
        return ReadResponse(status, b"")


def _get(request, transport):
    try:
        response = transport(request)
    except Exception:
        raise BrokerConnectionError("broker read transport failed; no response body logged") from None
    if not isinstance(response, ReadResponse) or type(response.status) is not int:
        raise BrokerConnectionError("invalid broker transport response")
    if response.status != 200:
        raise BrokerConnectionError(f"broker read returned HTTP {response.status}; redirects are forbidden")
    if not isinstance(response.body, bytes) or len(response.body) > MAX_BYTES:
        raise BrokerConnectionError("broker response exceeds size limit or is not bytes")
    try:
        result = json.loads(response.body, parse_float=_finite_json_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise BrokerConnectionError("broker response is not valid finite JSON") from None
    if not isinstance(result, (dict, list)):
        raise BrokerConnectionError("broker response must be a JSON object or array")
    return result


def _finite_json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _paging(cursor, limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer in [1, 100]")
    query = [("limit", str(limit))]
    if cursor is not None:
        query.append(("cursor", _text(cursor, "cursor", maximum=2048)))
    return query


def _symbols(values, key):
    if not isinstance(values, (tuple, list)) or len(values) > 100:
        raise ValueError("symbols must be a bounded sequence")
    if any(not isinstance(v, str) or re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,31}", v) is None for v in values):
        raise ValueError("symbols must be normalized uppercase identifiers")
    return [(key, value) for value in values]


class RobinhoodCryptoReadOnlyClient:
    """Official crypto reads only. signer(message)->detached 64-byte Ed25519 signature."""
    def __init__(self, api_key, signer, *, transport=None, clock=time.time):
        self._api_key = _text(api_key, "API key")
        if not callable(signer) or not callable(clock) or (transport is not None and not callable(transport)):
            raise ValueError("signer, clock and transport must be callable")
        self._signer, self._clock = signer, clock
        self._transport = _transport if transport is None else transport

    def _read(self, path, query=()):
        suffix = urlencode(query)
        target = path + ("?" + suffix if suffix else "")
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < 2**53:
            raise ValueError("clock must return finite nonnegative Unix seconds")
        timestamp = str(int(now))
        message = (self._api_key + timestamp + target + "GET").encode("utf-8")
        try:
            signature = self._signer(message)
        except Exception:
            raise BrokerConnectionError("Ed25519 signer failed") from None
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise BrokerConnectionError("signer must return a detached 64-byte Ed25519 signature")
        return _get(ReadRequest("https://trading.robinhood.com" + target, {
            "x-api-key": self._api_key, "x-timestamp": timestamp,
            "x-signature": base64.b64encode(signature).decode("ascii"),
            "Accept": "application/json",
        }), self._transport)

    def accounts(self, *, version=2):
        if type(version) is not int or version not in (1, 2):
            raise ValueError("supported account API versions are 1 and 2")
        return self._read(f"/api/v{version}/crypto/trading/accounts/")

    def trading_pairs(self, *, symbols=(), cursor=None, limit=100):
        return self._read("/api/v1/crypto/trading/trading_pairs/", _symbols(symbols, "symbol") + _paging(cursor, limit))

    def holdings(self, *, asset_codes=(), cursor=None, limit=100):
        return self._read("/api/v1/crypto/trading/holdings/", _symbols(asset_codes, "asset_code") + _paging(cursor, limit))

    def orders(self, *, cursor=None, limit=100):
        return self._read("/api/v1/crypto/trading/orders/", _paging(cursor, limit))


class SchwabReadOnlyClient:
    """Resolve GET endpoints from a caller-supplied official OpenAPI contract.

    The portal's detailed schemas require sign-in. No executable endpoint paths
    are guessed. A supplied export is a caller declaration, not authentication
    of its provenance. Only the two production API base paths are allowed.
    """
    def __init__(self, access_token, *, openapi=None, transport=None):
        self._token = _text(access_token, "access token", maximum=8192)
        if transport is not None and not callable(transport):
            raise ValueError("transport must be callable")
        self._transport = _transport if transport is None else transport
        self._operations = {}
        self.contract_sha256 = None
        if openapi is None:
            return
        if not isinstance(openapi, dict):
            raise ValueError("official OpenAPI export must be an object")
        raw = json.dumps(openapi, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(raw) > MAX_BYTES:
            raise ValueError("OpenAPI export exceeds size limit")
        doc = json.loads(raw)  # Own a deep snapshot, never depend on later caller edits.
        servers = doc.get("servers")
        if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict):
            raise ValueError("one explicit OpenAPI server is required")
        base = _text(servers[0].get("url"), "OpenAPI server URL").rstrip("/")
        if base not in ("https://api.schwabapi.com/trader/v1", "https://api.schwabapi.com/marketdata/v1"):
            raise ValueError("OpenAPI server must be a pinned Schwab production API base")
        paths = doc.get("paths")
        if not isinstance(paths, dict):
            raise ValueError("OpenAPI paths object is required")
        for path, item in paths.items():
            if not isinstance(item, dict) or "get" not in item:
                continue
            operation = item["get"]
            if (not isinstance(path, str) or re.fullmatch(r"/[A-Za-z0-9_/{\}.\-]+", path) is None
                    or any(p in (".", "..") for p in path.split("/"))
                    or "//" in path or not isinstance(operation, dict)
                    or "servers" in operation or "servers" in item or "$ref" in item):
                raise ValueError("unsupported OpenAPI path or server override")
            if any(re.fullmatch(r"(?:[A-Za-z0-9_.\-]+|\{[A-Za-z_][A-Za-z0-9_]*\})", part) is None
                   for part in path.strip("/").split("/")):
                raise ValueError("unsupported OpenAPI path template")
            operation_id = _text(operation.get("operationId"), "operationId", maximum=160)
            if operation_id in self._operations:
                raise ValueError("ambiguous OpenAPI operationId")
            parameters = {}
            inherited, local = item.get("parameters", []), operation.get("parameters", [])
            if not isinstance(inherited, list) or not isinstance(local, list):
                raise ValueError("OpenAPI parameters must be arrays")
            for parameter in inherited + local:
                if not isinstance(parameter, dict) or "$ref" in parameter:
                    raise ValueError("resolve OpenAPI parameter references before supplying the export")
                name, location = parameter.get("name"), parameter.get("in")
                if location not in ("path", "query"):
                    raise ValueError("only path/query OpenAPI parameters are supported")
                _text(name, "parameter name", maximum=128)
                required = parameter.get("required", False)
                if type(required) is not bool or (location == "path" and not required):
                    raise ValueError("OpenAPI required flags must be boolean and path parameters required")
                parameters[(location, name)] = required
            path_names = set(re.findall(r"\{([^{}]+)\}", path))
            if path_names != {name for location, name in parameters if location == "path"}:
                raise ValueError("path template and declared parameters must match")
            self._operations[operation_id] = (base, path, parameters)
        if not self._operations:
            raise ValueError("OpenAPI export contains no supported named GET operations")
        self.contract_sha256 = hashlib.sha256(raw.encode()).hexdigest()

    @property
    def operations(self):
        return tuple(sorted(self._operations))

    def get(self, operation_id, *, path_parameters=None, query_parameters=None):
        if not self._operations:
            raise BrokerConnectionError("Schwab endpoint verification blocked: supply the official OpenAPI export from your developer portal")
        if operation_id not in self._operations:
            raise ValueError("operation is not an allowed contract GET endpoint")
        base, path, declared = self._operations[operation_id]
        path_parameters = {} if path_parameters is None else dict(path_parameters)
        query_parameters = {} if query_parameters is None else dict(query_parameters)
        if set(path_parameters) != {name for location, name in declared if location == "path"}:
            raise ValueError("path parameters must exactly match the contract")
        for name, value in path_parameters.items():
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.\-]{1,160}", value) is None or value in (".", ".."):
                raise ValueError("invalid path parameter")
            path = path.replace("{" + name + "}", quote(value, safe=""))
        query_names = {name for location, name in declared if location == "query"}
        required = {name for (location, name), needed in declared.items() if location == "query" and needed}
        if set(query_parameters) - query_names or not required <= set(query_parameters):
            raise ValueError("query parameters must satisfy the contract")
        query = []
        for name, value in sorted(query_parameters.items()):
            if type(value) is bool:
                value = "true" if value else "false"
            elif type(value) in (int, float):
                if not math.isfinite(value):
                    raise ValueError("query values must be finite")
                value = str(value)
            query.append((name, _text(value, "query value")))
        suffix = urlencode(query)
        target = base + path + ("?" + suffix if suffix else "")
        return _get(ReadRequest(target, {"Authorization": "Bearer " + self._token,
                                         "Accept": "application/json"}), self._transport)
