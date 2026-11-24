"""Join Schwab SPXW and cash index changes in collector order, within one subscription."""
import math

from qte.adapters.schwab_option_stream import OptionStreamReplay, _clock, _MAX_NS, _objects

INDEX = "$SPX"
EQUITY = "LEVELONE_EQUITIES"
OPTION = "LEVELONE_OPTIONS"


class SpxStreamReplay:
    def __init__(self, contracts, max_age_ms):
        self.options = OptionStreamReplay(contracts, max_age_ms)
        if len(self.options.contracts) > 100 or any(k[:6] != "SPXW  " for k in self.options.contracts):
            raise ValueError("Select 1 to 100 exact padded SPXW contracts for the $SPX subscription")
        self.index = {}
        self.underlying = None

    def apply(self, record):
        if record.get("event") != "data":
            self.index.clear()
            self.underlying = None
            yield from self.options.apply(record)
            return
        if set(record) != {"event", "received_utc_ns", "message"} or self.options.books is None:
            raise ValueError("Data requires an active acknowledged subscription")
        received = _clock(record["received_utc_ns"], _MAX_NS, "received_utc_ns")
        if received < self.options.received:
            raise ValueError("Collector clock moved backwards")
        self.options.received = received
        message = record["message"]
        if type(message) is not dict or set(message) != {"data"}:
            raise ValueError("Expected a market data frame")
        for packet in _objects(message["data"], "market data packet"):
            if packet.get("service") == OPTION:
                for row in self.options.apply(dict(record, message={"data": [packet]})):
                    if row["event"] == "quote":
                        row["source_age_ms_at_receipt"] = (received - row["source_quote_ms"] * 1_000_000) / 1_000_000
                        row["spread"] = row["ask"] - row["bid"]
                        self._join(row)
                    yield row
            else:
                yield from self._index(packet, received)

    def _join(self, row):
        index = self.underlying
        row["underlying_price"] = None
        if index is None:
            row["underlying_reason"] = "no_index_in_epoch"
        elif index["event"] != "underlying":
            row["underlying_reason"] = index["reason"]
        elif row["received_utc_ns"] - index["source_trade_ms"] * 1_000_000 > self.options.max_age_ms * 1_000_000:
            row["underlying_reason"] = "stale"
        else:
            row.update(underlying_price=index["price"], underlying_source_ms=index["source_trade_ms"],
                       underlying_received_utc_ns=index["received_utc_ns"])

    def _index(self, packet, received):
        if packet.get("service") != EQUITY or packet.get("command") != "SUBS":
            raise ValueError("Expected an acknowledged LEVELONE_EQUITIES SUBS packet")
        packet_ms = _clock(packet.get("timestamp"), _MAX_NS // 1_000_000, "packet timestamp")
        for item in _objects(packet.get("content"), "index update"):
            if item.get("key") != INDEX or item.get("0", INDEX) != INDEX:
                raise ValueError("Unexpected underlying subscription key")
            changed = {"3", "32", "35", "delayed"}.intersection(item)
            if not changed:
                continue
            if "35" in changed and item["35"] not in (0, None):
                stamp = _clock(item["35"], _MAX_NS // 1_000_000, "index trade time")
                if stamp < self.index.get("latest_source_ms", 0):
                    raise ValueError("Index source time regressed within subscription")
                self.index["latest_source_ms"] = stamp
            self.index.update({field: item[field] for field in changed})
            if "3" in changed and "35" not in changed:
                self.index.pop("35", None)
            reason = self._index_reason(received)
            row = dict(event="underlying_unavailable" if reason else "underlying",
                       symbol=INDEX, epoch=self.options.epoch, received_utc_ns=received,
                       provider_packet_ms=packet_ms, source_trade_ms=self.index.get("35"))
            if reason:
                row["reason"] = reason
            else:
                row["price"] = self.index["3"]
                row["source_age_ms_at_receipt"] = (received - row["source_trade_ms"] * 1_000_000) / 1_000_000
            self.underlying = row
            yield row

    def _index_reason(self, received):
        book = self.index
        if any(book.get(k) is None for k in ("3", "35", "32", "delayed")):
            return "incomplete"
        if type(book["delayed"]) is not bool:
            raise ValueError("Malformed index delayed indicator")
        if book["delayed"]:
            return "delayed"
        if not isinstance(book["32"], str) or book["32"].lower() != "normal":
            return "trading_status"
        if type(book["3"]) not in (float, int) or not 0 < book["3"] < math.inf:
            return "invalid_market"
        if type(book["35"]) is not int or book["35"] <= 0:
            return "invalid_source_time"
        age = received - book["35"] * 1_000_000
        if age < 0:
            return "source_after_receipt"
        if age > self.options.max_age_ms * 1_000_000:
            return "stale"
        return None
