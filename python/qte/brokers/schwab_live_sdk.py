"""Small read-only subscription facade over schwab-py. SDK secrets stay outside artifacts."""
from contextlib import asynccontextmanager
import asyncio
import hmac
import math

from qte.adapters.schwab_live_stream import INDEX, OPTION, EQUITY
from qte.brokers.schwab_auth import AuthError, app_fingerprint, credentials, TokenStore, validate_token

OPTION_FIELDS = (0, 2, 3, 16, 17, 33, 38)
INDEX_FIELDS = (0, 3, 32, 35)


class TokenAccess:
    """Bridge SDK accessors to QTE's locked, permission-checked token document."""
    def __init__(self, store, app_key):
        self.store = store
        self.fingerprint = app_fingerprint(app_key)
        self.original_token = None

    def _current(self):
        document = self.store.load()
        if not hmac.compare_digest(document["app_sha256"], self.fingerprint):
            raise AuthError("App key differs from stored authorization")
        return document

    def read(self):
        with self.store.lock():
            document = self._current()
        self.original_token = document["token"]
        return dict(creation_timestamp=int(document["obtained_at"]),
                    token=dict(self.original_token, expires_at=document["expires_at"]))

    def write(self, updated, *args, **kwargs):
        token = validate_token(updated["token"])
        expires = updated["token"].get("expires_at")
        if type(expires) not in (int, float) or not math.isfinite(expires) or expires <= token["expires_in"]:
            raise AuthError("SDK refresh is missing a valid absolute expiry")
        with self.store.lock():
            document = self._current()
            if document["token"] != self.original_token:
                raise AuthError("Local authorization changed during capture. Restart the recorder.")
            self.store.save(dict(document, token=token, expires_at=expires, obtained_at=expires-token["expires_in"]))
        self.original_token = token


def market_packet(packet, service, fields):
    """Undo SDK display labels. Keep only subscribed market fields and public key metadata."""
    if packet.get("service") != service or packet.get("command") != "SUBS":
        raise ValueError("Unexpected market data callback")
    content = packet.get("content")
    if type(content) is not list or not content:
        raise ValueError("Expected market data content")
    mapping = {field.name: str(field.value) for field in fields}
    normalized = []
    for item in content:
        if type(item) is not dict:
            raise ValueError("Malformed market update")
        update = {mapping[k]: v for k, v in item.items() if k in mapping}
        for key in ("key", "delayed"):
            if key in item:
                update[key] = item[key]
        normalized.append(update)
    return dict(service=service, timestamp=packet["timestamp"], command="SUBS", content=normalized)


class QuoteStream:
    def __init__(self, stream, *network_errors):
        self.stream = stream
        self.network_errors = (OSError, *network_errors)

    async def _network(self, fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except self.network_errors:
            raise ConnectionError("Schwab stream transport interrupted") from None

    async def open(self, contracts, callback):
        stream = self.stream
        options = [stream.LevelOneOptionFields(i) for i in OPTION_FIELDS]
        equities = [stream.LevelOneEquityFields(i) for i in INDEX_FIELDS]
        stream.add_level_one_option_handler(lambda msg: callback(market_packet(msg, OPTION, options)))
        stream.add_level_one_equity_handler(lambda msg: callback(market_packet(msg, EQUITY, equities)))
        await self._network(stream.login, websocket_connect_args={"open_timeout": 15, "close_timeout": 2})
        await self._network(stream.level_one_equity_subs, [INDEX], fields=equities)
        await self._network(stream.level_one_option_subs, contracts, fields=options)

    async def read(self):
        await self._network(self.stream.handle_message)

    async def close(self):
        # The pinned SDK's logout only exchanges ADMIN messages; it exposes no socket-close API.
        socket = getattr(self.stream, "_socket", None)
        if socket is not None:
            await asyncio.wait_for(socket.close(), timeout=5)


@asynccontextmanager
async def sdk_factory(repo):
    try:
        from httpx import TransportError
        from schwab.auth import client_from_access_functions
        from schwab.streaming import StreamClient
        from websockets.exceptions import ConnectionClosed
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"Schwab streaming dependency unavailable ({exc.name}). Install QTE's live extra.") from None
    key, secret = credentials()
    access = TokenAccess(TokenStore(repo), key)
    client = client_from_access_functions(key, secret, access.read, access.write, asyncio=True)
    try:
        yield lambda: QuoteStream(StreamClient(client), ConnectionClosed, TransportError)
    finally:
        await client.session.aclose()
