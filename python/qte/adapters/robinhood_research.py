"""Offline Robinhood MCP captures -> provider-neutral research OHLC rows.

No brokerage calls, credentials, volume invention, or interpolation of missing data.
Daily labels are dates, not publication times; consumers must lag daily inputs.
"""
from collections import Counter
from datetime import datetime, time, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def load_last_hour_screen(directory: Path):
    """Observed 15:00/15:30 ET bars only; retrospective candidate discovery."""
    days, sources = {}, []
    for path in sorted(directory.glob("screen-*.json")):
        raw = path.read_bytes()
        doc = json.loads(raw)
        request, response = doc["request"], doc["response"]
        if request["interval"] != "30minute" or response["data"].get("restricted"):
            raise ValueError("Invalid or restricted screen capture")
        sources.append({"file": path.name, "sha256": sha256(raw).hexdigest(), "request": request})
        for result in response["data"].get("results", []) or []:
            if result["symbol"] != "SPX" or result["interval"] != "30minute":
                raise ValueError("Unexpected screen instrument or interval")
            for bar in result["bars"] or []:
                if bar.get("interpolated", False):
                    continue
                ts = datetime.fromisoformat(bar["begins_at"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    raise ValueError("UTC timestamp required")
                local = ts.astimezone(NY)
                if local.time() not in (time(15), time(15, 30)):
                    continue
                values = {key: float(bar[f"{key}_value"]) for key in ("open", "high", "low", "close")}
                if (any(not math.isfinite(v) or v <= 0 for v in values.values())
                        or values["high"] < max(values.values()) or values["low"] > min(values.values())):
                    raise ValueError("Invalid screen OHLC")
                dest, key = days.setdefault(local.date().isoformat(), {}), local.minute
                if key in dest and dest[key] != values:
                    raise ValueError("Conflicting screen duplicate")
                dest[key] = values
    rows = [{"date": day, "open": bars[0]["open"], "close": bars[30]["close"],
             "return": bars[30]["close"] / bars[0]["open"] - 1,
             "low": min(bars[0]["low"], bars[30]["low"])}
            for day, bars in sorted(days.items()) if 0 in bars and 30 in bars]
    return rows, sources


def load_captures(directory: Path):
    daily = {symbol: {} for symbol in ("SPX", "VIX", "SPY")}
    minute = {symbol: {} for symbol in ("SPX", "SPY")}
    sources, counts = [], Counter()
    for path in sorted(directory.glob("*.json")):
        raw = path.read_bytes()
        doc = json.loads(raw)
        if "request" not in doc or "response" not in doc:
            continue
        request, response = doc["request"], doc["response"]
        if "data" not in response:
            raise ValueError(f"Unsuccessful capture: {path.name}")
        sources.append({"file": path.name, "sha256": sha256(raw).hexdigest(), "request": request})
        interval = request["interval"]
        if interval == "minute" and path.name.startswith("probe-"):
            continue  # Boundary probes are audited, not silently added to selected test windows.
        for result in response["data"].get("results", []) or []:
            symbol = result["symbol"]
            if symbol not in daily or interval not in ("day", "minute"):
                continue
            suffix = "price" if symbol == "SPY" else "value"
            for bar in result["bars"] or []:
                if bar.get("interpolated", False):
                    counts[f"{symbol}_{interval}_interpolated"] += 1
                    continue
                ts = datetime.fromisoformat(bar["begins_at"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    raise ValueError("UTC timestamp required")
                local = ts.astimezone(NY)
                if interval == "minute" and not time(9, 30) <= local.time() < time(16):
                    counts[f"{symbol}_outside_rth"] += 1
                    continue
                day = (ts.date() if interval == "day" else local.date()).isoformat()
                row = {key: float(bar[f"{key}_{suffix}"]) for key in ("open", "high", "low", "close")}
                if (any(not math.isfinite(x) or x <= 0 for x in row.values())
                        or row["high"] < max(row["open"], row["close"], row["low"])
                        or row["low"] > min(row["open"], row["close"])):
                    raise ValueError(f"Invalid OHLC: {path.name} {bar['begins_at']}")
                row["date"] = day
                if symbol == "SPY":
                    volume = bar["volume"]
                    if type(volume) not in (int, float) or not math.isfinite(volume) or volume < 0:
                        raise ValueError("Invalid volume")
                    row["volume"] = volume
                if interval == "day":
                    dest, key = daily[symbol], day
                else:
                    # Complete minute becomes available at its right edge, with zero extra latency.
                    row["time"] = int((ts + timedelta(minutes=1)).timestamp())
                    dest, key = minute[symbol].setdefault(day, {}), row["time"]
                if key in dest and dest[key] != row:
                    raise ValueError(f"Conflicting duplicate: {symbol} {key}")
                dest[key] = row
    return daily, {s: {d: [b[t] for t in sorted(b)] for d, b in days.items()}
                   for s, days in minute.items()}, {"sources": sources, "excluded": dict(counts)}


def read_index_minutes(path, symbol, *, include_open=False):
    """Read an explicitly selected index capture; index minutes carry no volume."""
    from qte.json_input import strict_json

    raw = path.read_bytes()
    doc = strict_json(raw)
    request, data = doc["request"], doc["response"]["data"]
    if (doc["source"] != "Robinhood MCP get_index_historicals" or request["interval"] != "minute"
            or data.get("restricted") or len(data["results"]) != 1):
        raise ValueError("Expected an unrestricted single-index Robinhood minute capture")
    result, = data["results"]
    if result["symbol"] != symbol or result["interval"] != "minute":
        raise ValueError("Index capture symbol or interval mismatch")
    sessions = {}
    for row in result["bars"]:
        ts = datetime.fromisoformat(row["begins_at"])
        if ts.tzinfo is None or ts.second or ts.microsecond:
            raise ValueError("Index minute must have an explicit timezone and minute alignment")
        local = ts.astimezone(NY)
        if not time(9, 30) <= local.time() < time(16):
            continue
        bars = sessions.setdefault(local.date().isoformat(), {})
        if row.get("interpolated", False):
            continue
        o, h, lo, c = (float(row[f"{name}_value"]) for name in ("open", "high", "low", "close"))
        if not all(math.isfinite(v) and v > 0 for v in (o, h, lo, c)) or not lo <= min(o,c) <= max(o,c) <= h:
            raise ValueError("Invalid index minute OHLC")
        start = int(ts.timestamp()) * 1_000_000_000
        if start in bars:
            raise ValueError("Duplicate index minute")
        bars[start] = (o, h, lo, c) if include_open else (h, lo, c)
    if not sessions:
        raise ValueError("Index capture contains no observed cash-session minutes")
    return sessions, sha256(raw).hexdigest()


def read_index_closes(path, symbol):
    """Official daily bar labels are UTC dates. Consumers must select a prior session."""
    from qte.json_input import strict_json

    raw = path.read_bytes()
    doc = strict_json(raw)
    data = doc["response"]["data"]
    if (doc["source"] != "Robinhood MCP get_index_historicals" or doc["request"]["interval"] != "day"
            or data.get("restricted")):
        raise ValueError("Expected unrestricted Robinhood daily index capture")
    matches = [r for r in data["results"] if r["symbol"] == symbol and r["interval"] == "day"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one daily series for the requested index")
    closes = {}
    for row in matches[0]["bars"]:
        if row.get("interpolated", False):
            continue
        stamp = datetime.fromisoformat(row["begins_at"])
        if stamp.utcoffset() != timedelta(0) or stamp.time() != time(0):
            raise ValueError("Expected UTC midnight daily index label")
        label = stamp.date().isoformat()
        price = float(row["close_value"])
        if label in closes or not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid or duplicate daily index close")
        closes[label] = price
    return closes, sha256(raw).hexdigest()
