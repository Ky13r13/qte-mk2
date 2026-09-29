"""Offline GET-only broker contract tests: no credentials or network required."""

import base64
from copy import deepcopy
import hashlib
import io
import json
import traceback
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit

import pytest

import qte.brokers.readonly as broker
from qte.brokers.readonly import (
    BrokerConnectionError, ReadRequest, ReadResponse,
    RobinhoodCryptoReadOnlyClient, SchwabReadOnlyClient,
)


SECRET = "offline-test-secret-never-print"
SIGNATURE = bytes(range(64))


class RecordingTransport:
    def __init__(self, response=None):
        self.requests = []
        self.response = ReadResponse(200, b'{"results":[]}') if response is None else response

    def __call__(self, request):
        self.requests.append(request)
        return self.response


def rh(transport=None, signer=None, clock=lambda: 1_700_000_000.75):
    return RobinhoodCryptoReadOnlyClient(
        SECRET, (lambda message: SIGNATURE) if signer is None else signer,
        transport=RecordingTransport() if transport is None else transport, clock=clock)


def contract(base="https://api.schwabapi.com/trader/v1"):
    return {
        "openapi": "3.0.0", "servers": [{"url": base}],
        "paths": {
            "/accounts/{accountHash}": {
                "parameters": [{"name": "accountHash", "in": "path", "required": True}],
                "get": {"operationId": "getAccount", "parameters": [
                    {"name": "fields", "in": "query", "required": True},
                    {"name": "includePositions", "in": "query", "required": False},
                    {"name": "count", "in": "query"},
                ]},
                "post": {"operationId": "writeAccount"},
            },
            "/quotes": {"get": {"operationId": "getQuotes"}},
            "/orders": {"post": {"operationId": "placeOrder"}},
        },
    }


def test_robinhood_signs_exact_get_target_timestamp_and_encoded_query():
    messages = []
    transport = RecordingTransport()
    client = rh(transport, signer=lambda message: messages.append(message) or SIGNATURE)
    assert client.trading_pairs(symbols=["BTC-USD", "ETH-USD"], cursor="a/b?x=1&y=2", limit=5) == {"results": []}
    assert len(messages) == len(transport.requests) == 1
    request = transport.requests[0]
    target = "/api/v1/crypto/trading/trading_pairs/?symbol=BTC-USD&symbol=ETH-USD&limit=5&cursor=a%2Fb%3Fx%3D1%26y%3D2"
    assert request.url == "https://trading.robinhood.com" + target
    assert messages[0] == (SECRET + "1700000000" + target + "GET").encode()
    assert dict(request.headers) == {
        "x-api-key": SECRET, "x-timestamp": "1700000000",
        "x-signature": base64.b64encode(SIGNATURE).decode(), "Accept": "application/json",
    }


def test_robinhood_named_reads_and_pagination_do_not_follow_returned_links():
    transport = RecordingTransport(ReadResponse(200, b'{"next":"https://evil.example/steal","results":[]}'))
    client = rh(transport)
    assert client.accounts()["next"].startswith("https://evil")
    client.accounts(version=1)
    client.holdings(asset_codes=["BTC", "ETH"], limit=2)
    client.orders(cursor="opaque-token", limit=7)
    assert len(transport.requests) == 4
    assert [urlsplit(request.url).path for request in transport.requests] == [
        "/api/v2/crypto/trading/accounts/", "/api/v1/crypto/trading/accounts/",
        "/api/v1/crypto/trading/holdings/", "/api/v1/crypto/trading/orders/",
    ]
    assert parse_qsl(urlsplit(transport.requests[2].url).query) == [
        ("asset_code", "BTC"), ("asset_code", "ETH"), ("limit", "2")]


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 101}, {"limit": True},
                                   {"limit": 2.0}, {"cursor": ""}, {"cursor": "\nsecret"},
                                   {"cursor": "x" * 2049}])
def test_invalid_paging_is_rejected_before_signing_or_transport(kwargs):
    transport, messages = RecordingTransport(), []
    client = rh(transport, signer=lambda message: messages.append(message) or SIGNATURE)
    with pytest.raises(ValueError):
        client.orders(**kwargs)
    assert not messages and not transport.requests


@pytest.mark.parametrize("symbols", ["BTC-USD", ["btc-usd"], ["BTC/USDT"], ["BTC\n"],
                                    [""], [False], ["A" * 33], ["BTC-USD"] * 101])
def test_invalid_symbols_are_rejected_before_transport(symbols):
    transport = RecordingTransport()
    with pytest.raises(ValueError):
        rh(transport).trading_pairs(symbols=symbols)
    assert not transport.requests


@pytest.mark.parametrize("version", [0, 3, True, "2"])
def test_robinhood_rejects_unknown_account_versions(version):
    with pytest.raises(ValueError):
        rh().accounts(version=version)


@pytest.mark.parametrize("now", [-1, float("nan"), float("inf"), True, "1", 2**53])
def test_robinhood_requires_bounded_finite_clock(now):
    transport = RecordingTransport()
    with pytest.raises(ValueError):
        rh(transport, clock=lambda: now).accounts()
    assert not transport.requests


@pytest.mark.parametrize("signature", [b"", b"x" * 63, b"x" * 65, "not bytes", bytearray(64)])
def test_robinhood_signer_requires_detached_64_byte_signature(signature):
    transport = RecordingTransport()
    with pytest.raises(BrokerConnectionError, match="64-byte"):
        rh(transport, signer=lambda message: signature).accounts()
    assert not transport.requests


def test_credential_request_and_signer_failures_are_redacted():
    def fail_signer(message):
        raise ValueError(SECRET + " " + message.decode())

    with pytest.raises(BrokerConnectionError) as error:
        rh(signer=fail_signer).accounts()
    assert SECRET not in str(error.value)
    assert SECRET not in "".join(traceback.format_exception(error.value))
    request = ReadRequest("https://api.schwabapi.com/trader/v1/accounts", {"Authorization": "Bearer " + SECRET})
    for value in (request, ReadResponse(200, SECRET.encode()), rh(), SchwabReadOnlyClient(SECRET)):
        assert SECRET not in repr(value)
    with pytest.raises(ValueError) as invalid:
        RobinhoodCryptoReadOnlyClient(SECRET + "\n", lambda message: SIGNATURE)
    assert SECRET not in str(invalid.value)


def test_transport_exceptions_cannot_leak_request_or_response_secrets():
    def fail(request):
        raise RuntimeError(SECRET + " " + request.url)

    with pytest.raises(BrokerConnectionError, match="transport failed") as error:
        rh(fail).accounts()
    assert SECRET not in str(error.value)
    assert SECRET not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("response", [
    ReadResponse(301, SECRET.encode()), ReadResponse(302, SECRET.encode()),
    ReadResponse(307, SECRET.encode()), ReadResponse(401, SECRET.encode()),
    ReadResponse(429, SECRET.encode()), ReadResponse(500, SECRET.encode()),
    ReadResponse(True, b"{}"), ReadResponse("200", b"{}"), object(),
    ReadResponse(200, "{}"), ReadResponse(200, b"x" * (broker.MAX_BYTES + 1)),
    ReadResponse(200, b"not-json"), ReadResponse(200, b"\xff"),
    ReadResponse(200, b'{"x":NaN}'), ReadResponse(200, b'{"x":Infinity}'),
    ReadResponse(200, b'{"x":-Infinity}'), ReadResponse(200, b'{"x":1e999}'),
    ReadResponse(200, b'{"nested":[{"x":-1e999}]}'),
    ReadResponse(200, b"1"), ReadResponse(200, b"null"), ReadResponse(200, b'"secret"'),
])
def test_read_response_rejects_redirect_errors_nonfinite_and_invalid_payloads(response):
    with pytest.raises(BrokerConnectionError) as error:
        rh(RecordingTransport(response)).accounts()
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("payload", [b"{}", b"[]", b'{"x":1.25,"a":[true,null,2]}'])
def test_read_response_preserves_valid_provider_json(payload):
    assert rh(RecordingTransport(ReadResponse(200, payload))).accounts() == json.loads(payload)


@pytest.mark.parametrize("url", [
    "http://api.schwabapi.com/trader/v1/accounts",
    "https://evil.example/trader/v1/accounts",
    "https://api.schwabapi.com.evil.example/trader/v1/accounts",
    "https://api.schwabapi.com:443/trader/v1/accounts",
    "https://secret@api.schwabapi.com/trader/v1/accounts",
    "https://api.schwabapi.com/trader/v1/accounts#fragment",
    "https://api.schwabapi.com/trader/v1/accounts\n",
])
def test_requests_pin_exact_https_hosts_without_credentials_fragments_or_controls(url):
    with pytest.raises(ValueError):
        ReadRequest(url, {})


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer secret\r\nX-Injected: bad"},
    {"Bad\nName": "x"}, {"Authorization": "non-ascii-\u2603"},
    {"": "x"}, {"Bad Name": "x"}, {"Name:Injected": "x"},
])
def test_headers_are_valid_noninjectable_http_names_and_values(headers):
    with pytest.raises(ValueError):
        ReadRequest("https://api.schwabapi.com/trader/v1/accounts", headers)


def test_request_owns_an_immutable_copy_of_headers():
    headers = {"Authorization": "Bearer " + SECRET}
    request = ReadRequest("https://api.schwabapi.com/trader/v1/accounts", headers)
    headers["Authorization"] = "changed"
    assert request.headers["Authorization"] == "Bearer " + SECRET
    with pytest.raises(TypeError):
        request.headers["Authorization"] = "changed"


def test_real_transport_is_get_only_bounded_and_disables_redirects(monkeypatch):
    calls = {}

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls["closed"] = True
        def read(self, count):
            calls["read_count"] = count
            return b'{"ok":true}'

    class Opener:
        def open(self, request, timeout):
            calls["request"], calls["timeout"] = request, timeout
            return Response()

    def fake_build(handler):
        calls["handler"] = handler
        return Opener()

    monkeypatch.setattr(broker, "build_opener", fake_build)
    result = broker._transport(ReadRequest("https://api.schwabapi.com/trader/v1/accounts", {"Authorization": SECRET}))
    assert result == ReadResponse(200, b'{"ok":true}')
    assert calls["request"].get_method() == "GET"
    assert calls["request"].data is None
    assert calls["request"].full_url == "https://api.schwabapi.com/trader/v1/accounts"
    assert calls["timeout"] == 20
    assert calls["read_count"] == broker.MAX_BYTES + 1
    assert calls["closed"] is True
    assert calls["handler"].redirect_request(None, None, 302, "found", {}, "https://evil.example") is None


def test_real_transport_closes_http_error_and_discards_body(monkeypatch):
    body = io.BytesIO(SECRET.encode())
    error = HTTPError("https://api.schwabapi.com/trader/v1/accounts", 302, "redirect", {}, body)

    class Opener:
        def open(self, request, timeout):
            raise error

    monkeypatch.setattr(broker, "build_opener", lambda handler: Opener())
    response = broker._transport(ReadRequest("https://api.schwabapi.com/trader/v1/accounts", {}))
    assert response == ReadResponse(302, b"")
    assert body.closed


def test_schwab_without_contract_blocks_before_transport():
    transport = RecordingTransport()
    client = SchwabReadOnlyClient(SECRET, transport=transport)
    assert client.operations == () and client.contract_sha256 is None
    with pytest.raises(BrokerConnectionError, match="OpenAPI export"):
        client.get("getAccount")
    assert not transport.requests


@pytest.mark.parametrize("base", ["https://api.schwabapi.com/trader/v1", "https://api.schwabapi.com/marketdata/v1/"])
def test_schwab_contract_named_get_signals_bearer_header_and_sorted_encoded_query(base):
    transport = RecordingTransport()
    doc = contract(base)
    client = SchwabReadOnlyClient(SECRET, openapi=doc, transport=transport)
    assert client.operations == ("getAccount", "getQuotes")
    assert client.contract_sha256 == hashlib.sha256(json.dumps(doc, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    client.get("getAccount", path_parameters={"accountHash": "ABC-123_def"},
               query_parameters={"includePositions": True, "fields": "a/b & c", "count": 2.5})
    request = transport.requests[0]
    assert request.url == base.rstrip("/") + "/accounts/ABC-123_def?count=2.5&fields=a%2Fb+%26+c&includePositions=true"
    assert dict(request.headers) == {"Authorization": "Bearer " + SECRET, "Accept": "application/json"}
    assert "placeOrder" not in client.operations and "writeAccount" not in client.operations
    with pytest.raises(ValueError, match="allowed contract GET"):
        client.get("placeOrder")
    assert len(transport.requests) == 1


def test_schwab_deep_owns_contract_and_does_not_follow_response_links():
    doc = contract()
    transport = RecordingTransport(ReadResponse(200, b'{"next":"https://evil.example/credentials"}'))
    client = SchwabReadOnlyClient(SECRET, openapi=doc, transport=transport)
    identity = client.contract_sha256
    doc["servers"][0]["url"] = "https://evil.example"
    doc["paths"]["/accounts/{accountHash}"]["get"]["operationId"] = "changed"
    doc["paths"]["/quotes"]["get"]["parameters"] = [{"name": "secret", "in": "query", "required": True}]
    assert client.get("getQuotes")["next"].startswith("https://evil")
    assert len(transport.requests) == 1
    assert transport.requests[0].url == "https://api.schwabapi.com/trader/v1/quotes"
    assert client.contract_sha256 == identity


@pytest.mark.parametrize("kwargs", [
    {}, {"path_parameters": {"other": "x"}, "query_parameters": {"fields": "positions"}},
    {"path_parameters": {"accountHash": "x"}},
    {"path_parameters": {"accountHash": "x"}, "query_parameters": {"fields": "positions", "unknown": "x"}},
    {"path_parameters": {"accountHash": "x"}, "query_parameters": {"fields": float("inf")}},
    {"path_parameters": {"accountHash": "x"}, "query_parameters": {"fields": ["positions"]}},
])
def test_schwab_requires_exact_path_and_declared_required_query_parameters(kwargs):
    transport = RecordingTransport()
    client = SchwabReadOnlyClient(SECRET, openapi=contract(), transport=transport)
    with pytest.raises(ValueError):
        client.get("getAccount", **kwargs)
    assert not transport.requests


@pytest.mark.parametrize("value", ["..", ".", "../other", "a/b", "%2F", "x?secret=1", "x#y", "x\n", "", "x" * 161, 123])
def test_schwab_path_parameters_cannot_escape_declared_path(value):
    transport = RecordingTransport()
    client = SchwabReadOnlyClient(SECRET, openapi=contract(), transport=transport)
    with pytest.raises(ValueError, match="path parameter"):
        client.get("getAccount", path_parameters={"accountHash": value}, query_parameters={"fields": "positions"})
    assert not transport.requests


@pytest.mark.parametrize("base", [
    "http://api.schwabapi.com/trader/v1", "https://api.schwabapi.com/other/v1",
    "https://api.schwabapi.com/trader/v1/../marketdata/v1", "https://api.schwabapi.com:443/trader/v1",
    "https://evil.example/trader/v1", "https://api.schwabapi.com.evil.example/trader/v1",
])
def test_schwab_contract_rejects_unapproved_bases(base):
    with pytest.raises(ValueError):
        SchwabReadOnlyClient(SECRET, openapi=contract(base))


@pytest.mark.parametrize("path", ["/../accounts", "/x/./accounts", "/x//accounts", "/x?secret=1", "/{unterminated"])
def test_schwab_contract_rejects_traversal_query_or_malformed_path_template(path):
    doc = {"servers": [{"url": "https://api.schwabapi.com/trader/v1"}],
           "paths": {path: {"get": {"operationId": "getAccounts"}}}}
    with pytest.raises(ValueError):
        SchwabReadOnlyClient(SECRET, openapi=doc)


def test_schwab_rejects_ambiguous_contracts_and_server_overrides():
    cases = []
    doc = contract(); doc["paths"]["/quotes"]["get"]["operationId"] = "getAccount"; cases.append(doc)
    doc = contract(); doc["paths"]["/quotes"]["get"]["servers"] = [{"url": "https://evil.example"}]; cases.append(doc)
    doc = contract(); doc["paths"]["/quotes"]["servers"] = [{"url": "https://evil.example"}]; cases.append(doc)
    doc = contract(); doc["paths"]["/quotes"]["get"]["parameters"] = [{"$ref": "#/components/parameters/x"}]; cases.append(doc)
    doc = contract(); doc["paths"]["/accounts/{accountHash}"]["parameters"] = []; cases.append(doc)
    doc = contract(); doc["paths"]["/quotes"]["get"]["parameters"] = [{"name": "x", "in": "header"}]; cases.append(doc)
    doc = contract(); doc["servers"].append(deepcopy(doc["servers"][0])); cases.append(doc)
    doc = contract(); doc["paths"] = {"/orders": {"post": {"operationId": "placeOrder"}}}; cases.append(doc)
    for doc in cases:
        with pytest.raises(ValueError):
            SchwabReadOnlyClient(SECRET, openapi=doc)


def test_schwab_required_flags_are_boolean_not_truthiness_coercions():
    doc = contract()
    doc["paths"]["/quotes"]["get"]["parameters"] = [{"name": "x", "in": "query", "required": "false"}]
    with pytest.raises(ValueError):
        SchwabReadOnlyClient(SECRET, openapi=doc)


def test_no_client_exposes_order_submission_cancellation_or_credential_refresh():
    for client in (rh(), SchwabReadOnlyClient(SECRET)):
        for name in ("post", "put", "patch", "delete", "submit_order", "place_order", "cancel_order", "refresh_token"):
            assert not hasattr(client, name)
