"""Normalize recorded Schwab LEVELONE_OPTIONS change updates in collector order."""
from datetime import date
import math
import re

_SERVICE = "LEVELONE_OPTIONS"
_BOOK = {"2", "3", "16", "17"}
_FIELDS = _BOOK | {"33", "38", "delayed"}
_OPTION = re.compile(r"([A-Z0-9.]+ *)([0-9]{2})([0-9]{2})([0-9]{2})[CP]([0-9]{8})")
_MAX_NS = (1 << 63) - 1


def _contracts(items):
    if type(items) not in (list, tuple) or not items:
        raise ValueError("An explicit nonempty contract list is required")
    for key in items:
        match = _OPTION.fullmatch(key) if type(key) is str else None
        if not match or len(match[1]) != 6 or int(match[5]) == 0:
            raise ValueError("Expected an exact Schwab 21-character option key with padded root")
        date(2000 + int(match[2]), int(match[3]), int(match[4]))
    if len(items) != len(set(items)):
        raise ValueError("Duplicate subscription contract")
    return frozenset(items)


def _clock(value, maximum, name):
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError(f"{name} must be a positive integer epoch timestamp within range")
    return value


def _objects(items, name):
    if type(items) is not list or not items or any(type(item) is not dict for item in items):
        raise ValueError(f"Expected a nonempty array of {name} objects")
    return items


class OptionStreamReplay:
    """One explicit subscription. A new acknowledgement invalidates all previous state."""

    def __init__(self, contracts, max_age_ms):
        self.contracts = _contracts(contracts)
        if type(max_age_ms) is not int or not 0 < max_age_ms <= _MAX_NS // 1_000_000:
            raise ValueError("Maximum source age must be a positive integer in milliseconds")
        self.max_age_ms = max_age_ms
        self.received = 0
        self.epoch = 0
        self.books = None

    def apply(self, record):
        if type(record) is not dict or record.get("event") not in ("subscribed", "disconnect", "data"):
            raise ValueError("Expected subscribed, disconnect or data journal record")
        event = record["event"]
        fields = {"event", "received_utc_ns"}
        if event == "subscribed":
            fields.add("contracts")
        if event == "data":
            fields.add("message")
        if set(record) != fields:
            raise ValueError("Unexpected or missing journal fields")
        received = _clock(record["received_utc_ns"], _MAX_NS, "received_utc_ns")
        if received < self.received:
            raise ValueError("Collector receipt clock moved backwards")
        self.received = received
        if event == "subscribed":
            if _contracts(record["contracts"]) != self.contracts:
                raise ValueError("Acknowledged subscription differs from the requested contract set")
            self.books = {key: {} for key in self.contracts}
            self.epoch += 1
        elif self.books is None:
            raise ValueError("An acknowledged subscription is required before data or disconnect")
        elif event == "disconnect":
            self.books = None
        else:
            yield from self._data(record["message"])
            return
        yield {"event": event, "epoch": self.epoch, "received_utc_ns": received}

    def _data(self, message):
        if type(message) is not dict or set(message) != {"data"}:
            raise ValueError("Expected a market data frame with a data array")
        for packet in _objects(message["data"], "data packet"):
            if packet.get("service") != _SERVICE or packet.get("command") != "SUBS":
                raise ValueError("Expected a LEVELONE_OPTIONS SUBS data packet")
            packet_ms = _clock(packet.get("timestamp"), _MAX_NS // 1_000_000, "packet timestamp")
            for item in _objects(packet.get("content"), "contract update"):
                key = item.get("key")
                if type(key) is not str or key not in self.contracts or item.get("0", key) != key:
                    raise ValueError("Stream content does not match an acknowledged contract")
                changed = _FIELDS.intersection(item)
                if not changed:
                    continue
                book = self.books[key]
                if "38" in changed and item["38"] not in (0, None):
                    stamp = _clock(item["38"], _MAX_NS // 1_000_000, "quote source time")
                    if stamp < book.get("latest_source_ms", 0):
                        raise ValueError("Contract quote source time regressed within a subscription")
                    book["latest_source_ms"] = stamp
                book.update({field: item[field] for field in changed})
                if _BOOK.intersection(changed) and "38" not in changed:
                    # A changed price or size cannot be assigned the preceding event's time.
                    book.pop("38", None)
                reason = self._invalid_reason(book)
                row = dict(event="unavailable" if reason else "quote", contract=key, epoch=self.epoch,
                           received_utc_ns=self.received, provider_packet_ms=packet_ms,
                           source_quote_ms=book.get("38"))
                if reason:
                    row["reason"] = reason
                else:
                    row.update(bid=book["2"], ask=book["3"], bid_size=book["16"], ask_size=book["17"])
                yield row

    def _invalid_reason(self, book):
        if any(field not in book or book[field] is None for field in _FIELDS):
            return "incomplete"
        if type(book["delayed"]) is not bool:
            raise ValueError("Malformed delayed indicator")
        if book["delayed"]:
            return "delayed"
        if type(book["33"]) is not str or book["33"].lower() != "normal":
            return "trading_status"
        bid, ask = book["2"], book["3"]
        if (type(bid) not in (float, int) or type(ask) not in (float, int)
                or not 0 < bid <= ask < math.inf):
            return "invalid_market"
        if any(type(book[k]) is not int or book[k] <= 0 for k in ("16", "17")):
            return "invalid_market"
        if type(book["38"]) is not int or book["38"] <= 0:
            return "invalid_source_time"
        age = self.received - book["38"] * 1_000_000
        if age < 0:
            return "source_after_receipt"
        if age > self.max_age_ms * 1_000_000:
            return "stale"
        return None
