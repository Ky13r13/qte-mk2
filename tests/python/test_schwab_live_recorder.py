import asyncio
from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from qte.adapters.schwab_live_stream import SpxStreamReplay, EQUITY, OPTION
from qte.brokers.live_recorder import Capture, collect, replay_journal
from qte.brokers.schwab_auth import AuthError, app_fingerprint
from qte.brokers.schwab_live_sdk import TokenAccess, QuoteStream

KEY = "SPXW  261127P06500000"
MS = 1_800_000_000_000


def event(kind, shift=0, **fields):
    return dict(event=kind, received_utc_ns=(MS+shift)*1_000_000, **fields)


def packet(service, item):
    return dict(service=service, command="SUBS", timestamp=MS, content=[item])


def quote(**changes):
    return packet(OPTION, dict({"key":KEY,"2":2,"3":2.2,"16":3,"17":4,"33":"Normal",
                                "38":MS,"delayed":False}, **changes))


def index(**changes):
    return packet(EQUITY, dict({"key":"$SPX","3":6543,"32":"Normal","35":MS,"delayed":False}, **changes))


def test_join_uses_receipt_order_clock_status_and_connection_epoch():
    replay = SpxStreamReplay([KEY], 100)
    apply = lambda record: list(replay.apply(record))
    apply(event("subscribed", contracts=[KEY]))
    rows = apply(event("data", message={"data":[quote(), index(), quote()]}))
    assert rows[0]["underlying_reason"] == "no_index_in_epoch"
    assert rows[1]["price"] == rows[2]["underlying_price"] == 6543
    assert rows[2]["underlying_source_ms"] == MS
    late = apply(event("data", 101, message={"data":[quote(**{"38": MS+101})]}))
    assert late[0]["underlying_price"] is None and late[0]["underlying_reason"] == "stale"
    invalid = packet(EQUITY, {"key":"$SPX", "3":6580})
    rows = apply(event("data", 102, message={"data":[invalid,quote(**{"38":MS+102})]}))
    assert rows[0]["event"] == "underlying_unavailable"
    assert rows[1]["underlying_reason"] == "incomplete"
    apply(event("disconnect", 103))
    apply(event("subscribed", 104, contracts=[KEY]))
    first = apply(event("data", 104, message={"data":[packet(OPTION, {"key":KEY,"2":2.4})]}))
    assert first[0]["event"] == "unavailable" and first[0]["epoch"] == 2
    later = apply(event("data", 105, message={"data":[quote(**{"38":MS+105})]}))
    assert later[0]["underlying_reason"] == "no_index_in_epoch"


def test_reconnect_creates_durable_replayable_journal(tmp_path):
    class Stream:
        def __init__(self):
            self.closed = False
            self.sent = False
        async def open(self, contracts, callback):
            assert contracts == [KEY]
            self.callback = callback
        async def read(self):
            if not self.sent:
                self.callback(index())
                self.callback(quote())
                self.sent = True
            raise ConnectionError("interruption")
        async def close(self):
            self.closed = True
    streams = [Stream(), Stream()]
    it = iter(streams)
    with Capture(tmp_path/"one", [KEY], 200) as capture:
        with pytest.raises(RuntimeError, match="budget exhausted"):
            asyncio.run(collect(lambda: next(it), capture, 30, 1, clock=lambda: MS*1_000_000,
                                sleep=lambda _: asyncio.sleep(0)))
    assert all(s.closed for s in streams)
    output = tmp_path/"one"
    status = json.loads((output/"status.json").read_bytes())
    assert status["epoch"] == 2 and not status["connected"] and status["quotes"] == []
    assert status["counts"] == dict(subscribed=2, disconnect=2, quote=2, underlying=2)
    replay_journal(output/"raw.ndjson", tmp_path/"two", [KEY], 200)
    assert (output/"quotes.ndjson").read_bytes() == (tmp_path/"two"/"quotes.ndjson").read_bytes()


def test_real_sdk_subscriptions_decode_numeric_wire_fields_and_filter_metadata(monkeypatch):
    streaming = pytest.importorskip("schwab.streaming")
    from websockets.exceptions import ConnectionClosed
    from unittest.mock import AsyncMock
    # The real SDK requests preferences, logs in, subscribes and dispatches its relabeled packets.
    # Replace only network I/O. Check that private response fields cannot reach the capture.
    inbox = []
    requests = []
    class Socket:
        async def recv(self):
            return json.dumps(inbox.pop(0))
        async def send(self, raw):
            request, = json.loads(raw)["requests"]
            requests.append(request)
            ack = {key:request[key] for key in ("service","command","requestid")}
            inbox.append({"response":[dict(ack, content={"code":0})]})
        close = AsyncMock()
    socket = Socket()
    monkeypatch.setattr(streaming.ws_client, "connect", AsyncMock(return_value=socket))
    prefs = dict(schwabClientCustomerId="user-id", schwabClientCorrelId="corr",
                 schwabClientChannel="ch", schwabClientFunctionId="function",
                 streamerSocketUrl="wss://unused.invalid")
    client = SimpleNamespace(get_user_preferences=lambda: SimpleNamespace(
        status_code=200, json=lambda: {"streamerInfo":[prefs]}),
        token_metadata=SimpleNamespace(token={"access_token":"secret-token"}))
    stream = QuoteStream(streaming.StreamClient(client), ConnectionClosed)
    markets = []
    async def run():
        await stream.open([KEY], markets.append)
        qpacket = quote()
        qpacket["content"][0]["unknownPrivateValue"] = "private"
        inbox.append({"data":[qpacket,index()]})
        await stream.read()
        await stream.close()
    asyncio.run(run())
    assert [r["service"] for r in requests] == ["ADMIN", EQUITY, OPTION]
    assert requests[1]["parameters"]["keys"] == "$SPX"
    assert requests[2]["parameters"]["keys"] == KEY
    assert markets == [quote(), index()]
    assert "secret-token" not in str(markets)
    socket.close.assert_awaited_once()


def test_token_bridge_preserves_qte_document_and_refuses_concurrent_replacement():
    token = dict(access_token="a", refresh_token="b", token_type="Bearer", expires_in=1800)
    document = dict(schema_version=1, app_sha256=app_fingerprint("key"), callback="callback",
                    obtained_at=MS/1000, expires_at=MS/1000+1800, token=token)
    store = SimpleNamespace(load=lambda:document.copy(), save=document.update, lock=nullcontext)
    bridge = TokenAccess(store, "key")
    sdk = bridge.read()
    assert sdk["token"]["expires_at"] == document["expires_at"]
    sdk["token"].update(access_token="c", expires_at=sdk["token"]["expires_at"]+100)
    bridge.write(sdk)
    assert document["token"]["access_token"] == "c"
    assert "expires_at" not in document["token"]
    document["token"] = dict(token, access_token="other")
    with pytest.raises(AuthError, match="changed"):
        bridge.write(sdk)


@pytest.mark.skipif(__import__("os").name != "posix", reason="Live publication uses POSIX token and lock permissions")
def test_single_desk_publisher_releases_lock_between_archives(tmp_path):
    desk = tmp_path/"desk"
    with Capture(tmp_path/"one", [KEY], 500, desk=desk) as capture:
        capture.append(event("subscribed", contracts=[KEY]))
        assert json.loads((desk/"status.json").read_bytes())["connected"]
        with pytest.raises(RuntimeError, match="active recorder"):
            with Capture(tmp_path/"two", [KEY], 500, desk=desk):
                pytest.fail("Concurrent recorder acquired shared GUI")
        capture.append(event("disconnect"))
    with Capture(tmp_path/"three", [KEY], 500, desk=desk):
        assert json.loads((desk/"status.json").read_bytes())["epoch"] == 0
