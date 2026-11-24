"""Replay independent SPXW paper execution probes against recorded Level 1 quotes."""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import math
import os
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from .adapters.schwab_option_stream import _contracts
from .json_input import strict_json
from .market_sessions import entry_window, load_schedule

MS = 1_000_000
NY = ZoneInfo("America/New_York")


def _integer(value, name, minimum=0):
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError(f"{name} must be an integer >= {minimum} within int64")
    return value


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return Decimal(str(value))


def _fields(obj, fields):
    if type(obj) is not dict or set(obj) != set(fields.split()):
        raise ValueError(f"Expected fields {fields}")


def _configure(config):
    _fields(config, "schema evidence extra_feed_delay_ms order_delay_ms max_wait_ms max_age_ms commission_per_contract_usd hold_ms buffer_ms orders")
    if config["schema"] != "qte-execution-probes-v1" or config["evidence"] not in ("synthetic", "observed"):
        raise ValueError("Invalid probe schema or evidence label")
    for name in ("extra_feed_delay_ms", "order_delay_ms", "max_wait_ms", "max_age_ms", "hold_ms", "buffer_ms"):
        _integer(config[name], name, 1 if name in ("max_wait_ms", "max_age_ms", "hold_ms") else 0)
    _number(config["commission_per_contract_usd"], "commission")
    if type(config["orders"]) is not list or not 1 <= len(config["orders"]) <= 256:
        raise ValueError("Expected 1–256 independent probes")
    seen = set()
    for order in config["orders"]:
        if type(order) is not dict:
            raise ValueError("Expected a probe object")
        cutoffs = ("contract_cutoff_ns", "broker_cutoff_ns")
        _fields({k: v for k, v in order.items() if k not in cutoffs}, "id contract side quantity decision_ns")
        for key in cutoffs:
            if key in order:
                _integer(order[key], key, 1)
        key = order["id"]
        if type(key) is not str or not key.strip() or not key.isprintable() or len(key) > 80 or key in seen:
            raise ValueError("Expected a unique printable probe ID of at most 80 characters")
        seen.add(key)
        _contracts([order["contract"]])
        if not order["contract"].startswith("SPXW  ") or order["side"] not in ("buy", "sell"):
            raise ValueError("Expected an exact SPXW contract and buy or sell")
        _integer(order["quantity"], "quantity", 1)
        _integer(order["decision_ns"], "decision_ns", 1)


def _quote(row, line):
    source = _integer(row.get("source_quote_ms"), "source_quote_ms", 1) * MS
    received = row["received_utc_ns"]
    bid = _number(row.get("bid"), "bid")
    ask = _number(row.get("ask"), "ask")
    if source > received or not 0 < bid <= ask:
        raise ValueError(f"Invalid quote at line {line}")
    for name in ("bid_size", "ask_size"):
        _integer(row.get(name), name, 1)
    return {**{k: row[k] for k in ("epoch", "received_utc_ns", "source_quote_ms", "bid", "ask",
                                      "bid_size", "ask_size")}, "line": line}


def _window(schedule, order, config, now):
    expiry = datetime.strptime("20" + order["contract"][6:12], "%Y%m%d").date().isoformat()
    return entry_window(schedule, product="SPXW_PM", expiry=expiry, now_ns=now,
                        hold_ns=config["hold_ms"] * MS, buffer_ns=config["buffer_ms"] * MS,
                        broker_cutoff_ns=order.get("broker_cutoff_ns"), contract_cutoff_ns=order.get("contract_cutoff_ns"))


def _decide(row, book, config, schedule):
    window = _window(schedule, row, config, row["decision_ns"])
    row["session"] = window
    if not window["admitted"]:
        row["status"] = window["reason"]
    elif book is None:
        row["status"] = "no_visible_quote"
    elif row["decision_ns"] - book["source_quote_ms"] * MS > config["max_age_ms"] * MS:
        row["status"] = "stale_decision_quote"
    else:
        row["decision_quote"] = book
        row["decision_mid"] = float((_number(book["bid"], "bid") + _number(book["ask"], "ask")) / 2)
        row["status"] = "awaiting_quote"


def _match(row, quote, config, schedule):
    now = quote["received_utc_ns"]
    source = quote["source_quote_ms"] * MS
    if now > row["decision_ns"] + config["max_wait_ms"] * MS:
        row["status"] = "wait_expired"
        return
    if source <= row["decision_ns"] or source < row["eligible_ns"]:
        return
    if now - source > config["max_age_ms"] * MS:
        return
    if now >= row["session"]["cutoff_ns"]:
        row["status"] = "at_or_after_cutoff"
        return
    window = _window(schedule, row, config, now)
    if not window["admitted"]:
        row["status"] = window["reason"]
        return
    side = "ask" if row["side"] == "buy" else "bid"
    row["execution_quote"] = quote
    if quote[side + "_size"] < row["quantity"]:
        row["status"] = "insufficient_displayed_size"
        return
    premium = _number(quote[side], side)
    midpoint = Decimal(str(row["decision_mid"]))
    signed = (premium - midpoint) * (1 if row["side"] == "buy" else -1)
    fees = config["commission_per_contract_usd"]
    quantity = row["quantity"]
    per_contract = signed * 100 + Decimal(str(fees))
    row.update(status="paper_filled", price=float(premium), commission_usd=float(Decimal(str(fees)) * quantity),
               shortfall_usd=float(per_contract * quantity), shortfall_per_contract_usd=float(per_contract),
               source_to_receipt_ms=(now-source)/MS, decision_to_quote_ms=(now-row["decision_ns"])/MS)


def _groups(rows):
    groups = defaultdict(list)
    for row in rows:
        quote = row.get("decision_quote")
        if quote is None:
            continue
        width = Decimal(str(quote["ask"])) - Decimal(str(quote["bid"]))
        spread = ["≤0.05", "(0.05, 0.25]", ">0.25"][bisect_left([Decimal(".05"), Decimal(".25")], width)]
        hour = datetime.fromtimestamp(row["decision_ns"] // 10**9, NY).strftime("%H:00")
        groups[hour, spread].append(row)
    result = []
    for (hour, spread), group in sorted(groups.items()):
        fills = [r for r in group if r["status"] == "paper_filled"]
        stats = {}
        for key in ("shortfall_per_contract_usd", "source_to_receipt_ms", "decision_to_quote_ms"):
            values = sorted(r[key] for r in fills)
            stats[key] = ({"median": median(values), "p95": values[math.ceil(.95 * len(values)) - 1]}
                          if values else {"median": None, "p95": None})
        result.append(dict(hour_et=hour, decision_spread=spread, probes=len(group), fills=len(fills), **stats))
    return result


def measure(config, schedule, records):
    """Equal receipt times are invisible to the decision. Independent probes never share a ledger."""
    _configure(config)
    rows = [dict(o, eligible_ns=o["decision_ns"] + config["order_delay_ms"] * MS) for o in config["orders"]]
    pending = sorted(rows, key=lambda r: r["decision_ns"])
    cursor = 0
    books = {}
    active = defaultdict(list)
    epoch = previous = 0
    connected = False

    def decisions(until):
        nonlocal cursor
        while cursor < len(pending):
            row = pending[cursor]
            if row["decision_ns"] - config["extra_feed_delay_ms"] * MS > until:
                break
            cursor += 1
            _decide(row, books.get(row["contract"]), config, schedule)
            if row["status"] == "awaiting_quote":
                active[row["contract"]].append(row)

    for line, record in enumerate(records, 1):
        if type(record) is not dict or not {"event","epoch","received_utc_ns"} <= record.keys():
            raise ValueError(f"Malformed normalized record on line {line}")
        _integer(record["epoch"], "epoch", 1)
        now = _integer(record["received_utc_ns"], "received_utc_ns", 1)
        if now < previous:
            raise ValueError(f"Receipt clock regression on line {line}")
        previous = now
        decisions(now)
        event = record["event"]
        if event in ("subscribed", "disconnect"):
            for waiting in active.values():
                for row in waiting:
                    row["status"] = "subscription_changed"
            active.clear()
            books.clear()
            if event == "subscribed":
                if record["epoch"] != epoch + 1:
                    raise ValueError("Invalid subscription epoch")
                epoch += 1
                connected = True
            elif not connected or record["epoch"] != epoch:
                raise ValueError("Unexpected disconnect")
            else:
                connected = False
            continue
        if not connected or record["epoch"] != epoch:
            raise ValueError("Record outside subscription")
        if event in ("underlying", "underlying_unavailable"):
            continue
        symbol = record.get("contract")
        if type(symbol) is not str:
            raise ValueError(f"Missing contract on line {line}")
        if event == "unavailable":
            books.pop(symbol, None)
            continue
        if event != "quote":
            raise ValueError("Unexpected normalized journal record")
        book = _quote(record, line)
        books[symbol] = book
        for row in active[symbol]:
            _match(row, book, config, schedule)
        active[symbol] = [r for r in active[symbol] if r["status"] == "awaiting_quote"]

    for row in pending[cursor:]:
        row["status"] = "outside_capture"
    for waiting in active.values():
        for row in waiting:
            row["status"] = ("wait_expired" if previous > row["decision_ns"] + config["max_wait_ms"] * MS
                             else "capture_ended")
    for row in rows:
        if row["decision_ns"] > previous:
            row["status"] = "outside_capture"
    return dict(schema="qte-execution-quality-v1", evidence=config["evidence"], assumptions={
        k: v for k, v in config.items() if k not in ("orders", "schema", "evidence")},
        schedule_sha256=schedule.snapshot_sha256, orders=rows, groups=_groups(rows))


def from_files(config_path: Path, schedule_path: Path, quotes_path: Path):
    cfg = config_path.read_bytes()
    schedule = load_schedule(schedule_path)
    digest = sha256()
    def records():
        with quotes_path.open("rb") as source:
            initial = os.fstat(source.fileno())
            while line := source.readline(1024 * 1024 + 1):
                if len(line) > 1024 * 1024 or not line.endswith(b"\n"):
                    raise ValueError("Oversize or incomplete quote journal line")
                digest.update(line)
                yield strict_json(line)
            final = os.fstat(source.fileno())
            if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                raise ValueError("Journal changed during analysis. Use a completed capture.")
    result = measure(strict_json(cfg), schedule, records())
    result.update(journal_sha256=digest.hexdigest(), config_sha256=sha256(cfg).hexdigest())
    return result
