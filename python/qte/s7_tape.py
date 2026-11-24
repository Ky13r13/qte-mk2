"""Validated synchronized snapshots for S7, indexed by when the observer received them."""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

from .adapters.schwab_option_stream import _contracts
from .volatility import MINUTE_NS
from .spx_spreads import _fields, _finite, _ns

FRESH = MINUTE_NS


@dataclass(frozen=True)
class Book:
    key: str
    delta: float | None
    bid: float
    ask: float
    bid_size: int
    ask_size: int

    @property
    def strike(self):
        return int(self.key[13:])/1000

    @property
    def mid(self):
        return (self.bid+self.ask)/2


@dataclass(frozen=True)
class Snapshot:
    source: int
    receipt: int
    kind: str
    books: tuple


@dataclass
class Tape:
    snapshots: list
    receipts: list
    regimes: dict
    settlement: float | None

    def asof(self, at):
        idx = bisect_right(self.receipts, at) - 1
        if idx >= 0 and at - self.snapshots[idx].source <= FRESH:
            return self.snapshots[idx]
        return None


def parse_session(payload, day, symbol):
    _fields(payload, ("snapshots", "regimes", "settlement"), "S7 session")
    if not isinstance(payload["snapshots"], list) or not isinstance(payload["regimes"], list):
        raise ValueError("S7 snapshots and regimes must be arrays")
    root = "SPXW" if symbol == "SPX" else "XSP"
    prefix = root.ljust(6) + date.fromisoformat(day.trading_date).strftime("%y%m%d")
    snapshots = []
    for snap in payload["snapshots"]:
        _fields(snap, ("source_ns", "available_ns", "kind", "books"), "S7 synchronized chain")
        if snap["kind"] not in ("chain", "update"):
            raise ValueError("Expected chain or quote update")
        if not isinstance(snap["books"], list):
            raise ValueError("S7 books must be an array")
        source, receipt = _ns(snap["source_ns"]), _ns(snap["available_ns"])
        if not day.open_ns <= source <= receipt <= day.close_ns:
            raise ValueError("Option snapshot outside session or received before source")
        if snapshots and (source <= snapshots[-1].source or receipt <= snapshots[-1].receipt):
            raise ValueError("Option snapshots must advance in source and availability order")
        books = []
        for raw in snap["books"]:
            _fields(raw, ("key", "delta", "bid", "ask", "bid_size", "ask_size"), "S7 contract quote")
            key = raw["key"]
            if not isinstance(key, str) or not key.startswith(prefix):
                raise ValueError("Expected selected index's same-day PM option")
            delta = None if raw["delta"] is None else _finite(raw["delta"], "delta")
            bid, ask = _finite(raw["bid"], "bid"), _finite(raw["ask"], "ask")
            sizes = raw["bid_size"], raw["ask_size"]
            if (not 0 <= bid <= ask or ask <= 0 or any(type(n) is not int or n < 0 for n in sizes)
                    or delta is not None and not 0 <= (delta if key[12:13] == "C" else -delta) <= 1):
                raise ValueError("Invalid option delta, price or displayed size")
            books.append(Book(key, delta, bid, ask, *sizes))
        _contracts([b.key for b in books])
        snapshots.append(Snapshot(source, receipt, snap["kind"], tuple(books)))
    regimes = {}
    for r in payload["regimes"]:
        _fields(r, ("source_ns", "available_ns", "gamma", "vix", "vix3m"), "S7 regime")
        source, receipt = _ns(r["source_ns"]), _ns(r["available_ns"])
        if (not day.open_ns + 15*MINUTE_NS <= source <= receipt <= day.close_ns
                or (source-day.open_ns) % (15*MINUTE_NS) or source in regimes):
            raise ValueError("Expected unique scheduled 15-minute regime observation")
        if r["gamma"] not in (None, "negative", "positive", "neutral", "estimated_negative", "estimated_positive"):
            raise ValueError("Invalid dealer-gamma evidence")
        for field in ("vix", "vix3m"):
            if r[field] is not None and _finite(r[field], field) <= 0:
                raise ValueError("Volatility index must be positive")
        regimes[source] = r
    settlement = payload["settlement"]
    if settlement is not None and _finite(settlement, "official PM settlement") <= 0:
        raise ValueError("Invalid cash settlement")
    return Tape(snapshots, [s.receipt for s in snapshots], regimes, settlement)


def regime_at(tape, opening, at):
    if tape is None:
        return None
    interval = 15 * MINUTE_NS
    refresh = opening + (at-opening)//interval*interval
    r = tape.regimes.get(refresh)
    if r is None or r["available_ns"] > at:
        return None
    ratio = r["vix"]/r["vix3m"] if None not in (r["vix"], r["vix3m"]) else None
    if r["gamma"] == "negative" or ratio is not None and ratio > 1.02:
        return True
    if ratio is None or r["gamma"] not in ("neutral", "positive"):
        return None
    return False


def straddle_move(tape, formed, spot):
    snap = tape.asof(formed) if tape else None
    if snap is None or snap.kind != "chain":
        return None
    strikes = [b.strike for b in snap.books]
    if not min(strikes) <= spot <= max(strikes):
        return None
    strike = min(strikes, key=lambda k: (abs(k-spot), k))
    legs = [b for b in snap.books if b.strike == strike]
    if len(legs) == 2 and all(b.bid > 0 and min(b.bid_size, b.ask_size) > 0 for b in legs):
        return sum(b.mid for b in legs)
    return None


def select(tape, at, spot, right):
    snap = tape.asof(at)
    if snap is None or snap.kind != "chain":
        return None
    choices = [b for b in snap.books if b.key[12] == right and b.delta is not None
               and .10 <= abs(b.delta) <= .15 and (b.strike > spot if right == "C" else b.strike < spot)
               and min(b.bid_size, b.ask_size) > 0 and b.bid > 0 and b.ask-b.bid <= .1*b.mid+1e-12]
    return min(choices, key=lambda b: (round(abs(abs(b.delta)-.12), 12), b.ask-b.bid, b.key), default=None)
