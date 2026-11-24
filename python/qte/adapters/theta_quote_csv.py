"""Read one explicitly selected SPXW expiration-day contract from Theta sample CSVs."""
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from hashlib import sha256
from math import isfinite
from pathlib import Path

_DAY_OPEN = time(9, 30)


@dataclass(frozen=True)
class QuoteSession:
    day: date
    strike: float
    right: str
    close: time

    def __post_init__(self):
        if type(self.day) is not date or self.right not in {"CALL", "PUT"}:
            raise ValueError("Supply one trading date and CALL or PUT")
        if not isfinite(self.strike) or self.strike <= 0:
            raise ValueError("Strike must be positive and finite")
        if self.close.tzinfo is not None or not _DAY_OPEN < self.close <= time(16):
            raise ValueError("Session close must be a New York clock after 09:30 and at or before 16:00")

    @property
    def opens_at(self):
        return datetime.combine(self.day, _DAY_OPEN)

    @property
    def closes_at(self):
        return datetime.combine(self.day, self.close)


def _rows(path, required, hashes):
    # Hash the exact bytes consumed by csv, including vendor line endings.
    digest = sha256()
    with path.open("rb") as stream:
        def lines():
            for raw in stream:
                digest.update(raw)
                yield raw.decode("utf-8")

        reader = csv.DictReader(lines())
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields) or not required.issubset(fields):
            raise ValueError(f"{path} requires unique columns including {', '.join(sorted(required))}")
        yield from reader
    if hashes is not None:
        hashes[str(path)] = digest.hexdigest()


def _stamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is not None:
        raise ValueError("Expected a naive New York timestamp in the Theta sample schema")
    return stamp


def read_spot(path: Path, session: QuoteSession, *, hashes=None):
    prices = []
    for row in _rows(path, {"timestamp", "price"}, hashes):
        stamp = _stamp(row["timestamp"])
        if not session.opens_at <= stamp < session.closes_at:
            continue
        price = float(row["price"])
        if not isfinite(price) or price <= 0:
            raise ValueError("SPX spot prices must be positive and finite")
        if prices and prices[-1][0] >= stamp:
            raise ValueError("SPX timestamps must be strictly increasing")
        prices.append((stamp, price))
    if not prices:
        raise ValueError("No SPX spot observations in the selected session")
    return prices


def read_quotes(path: Path, session: QuoteSession, *, hashes=None):
    columns = {"symbol", "expiration", "strike", "right", "timestamp", "bid", "ask",
               "bid_size", "ask_size", "bid_condition", "ask_condition"}
    counts = Counter()
    quotes = []
    incomplete = False
    last_seen = None
    opened, closed = session.opens_at, session.closes_at
    expiry = session.day.isoformat()
    side = session.right.lower()
    for row in _rows(path, columns, hashes):
        if incomplete:
            raise ValueError("Incomplete quote row inside capture; cannot establish continuity")
        if None in row or any(not row[key] for key in columns):
            # The published sample ends mid-record. A partial final row
            # sets the coverage boundary; an interior partial row is fatal.
            counts["malformed"] += 1
            incomplete = True
            continue
        if (row["symbol"] != "SPXW" or row["expiration"] != expiry
                or row["right"] != session.right or float(row["strike"]) != session.strike):
            counts["other_contract"] += 1
            continue
        stamp = _stamp(row["timestamp"])
        if not opened <= stamp < closed:
            counts[f"outside_{side}_session"] += 1
            continue
        if last_seen is not None and stamp < last_seen:
            counts[f"out_of_order_{side}"] += 1
            continue
        last_seen = stamp
        bid, ask = float(row["bid"]), float(row["ask"])
        if (not isfinite(bid) or not isfinite(ask) or not 0 < bid <= ask
                or min(int(row["bid_size"]), int(row["ask_size"])) <= 0
                or (row["bid_condition"], row["ask_condition"]) != ("50", "50")):
            counts[f"unusable_{side}_quote"] += 1
            continue
        quotes.append((stamp, bid, ask))
    if not quotes:
        raise ValueError(f"No usable {session.right} quotes for SPXW {expiry} strike {session.strike:g}")
    return quotes, counts
