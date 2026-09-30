"""Structural verification of additive owned-result exports, not accounting."""
from __future__ import annotations

import csv
import io
from itertools import zip_longest
import math
import re

from qte.research_export import CAPABILITIES, TABLE_COLUMNS, TABLE_FILES

from .artifact_io import FILE_LIMIT, METADATA_LIMIT, read_stable
from .artifacts import ArtifactError, strict_json

U64 = 2**64 - 1
I64 = 2**63 - 1
ENUMS = {
    "side": {"BUY", "SELL"}, "direction": {"LONG", "SHORT"},
    "order_type": {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"},
    "time_in_force": {"GOOD_TIL_CANCELED"},
    "status": {"NEW", "OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"},
    "kind": {"ACCEPTED", "REJECTED", "CANCELED", "CANCEL_NOOP", "CANCEL_UNKNOWN", "FILLED"},
    "rejection_reason": {"INVALID_REQUEST", "NO_REFERENCE_PRICE", "RISK"},
    "cancellation_reason": {"USER_REQUESTED", "END_OF_DATA", "EXECUTION_RISK"},
    "outcome": {"WINNING", "LOSING", "BREAKEVEN"},
}
OPTIONAL = {"limit_price", "stop_price", "rejection_reason", "cancellation_reason",
            "closing_fill_id", "closed_ns", "closing_sequence", "outcome",
            "mark_price", "mark_ns", "mark_sequence"}
FLOATS = {"equity", "gross_exposure", "reference_open", "price", "gross_notional", "commission",
          "limit_price", "stop_price", "mark_price", "realized_gross_pnl",
          "allocated_commissions", "net_realized_pnl"}


def invalid(message="Owned-result export is inconsistent."):
    raise ArtifactError("artifact_invalid", 422, message)


def integer(text, low, high):
    if len(text) > 20 or not re.fullmatch(r"0|-?[1-9][0-9]*", text):
        invalid("Owned-result integer is not canonical decimal text.")
    value = int(text)
    if not low <= value <= high:
        invalid("Owned-result integer is outside its domain.")
    return value


def cell(field, text, semantic):
    if text == "" and (field in OPTIONAL or semantic == "order_events" and field == "order_id"):
        return None
    if field.endswith("_ns"):
        return integer(text, -2**63, I64)
    if field.endswith("_id") or field == "sequence" or field.endswith("_sequence"):
        return integer(text, 1, U64)
    if field in {"opened_quantity", "closed_quantity", "remaining_quantity"} and semantic.endswith("trades"):
        return integer(text, 0, U64)
    if field in {"quantity", "filled_quantity", "remaining_quantity"}:
        low = -I64 if semantic == "positions" else 1 if field == "quantity" else 0
        return integer(text, low, I64)
    if field in ENUMS:
        if text not in ENUMS[field]: invalid("Owned-result enum is unsupported.")
        return text
    if field in {"is_closed", "stop_triggered"}:
        if text not in {"true", "false"}: invalid("Owned-result boolean is invalid.")
        return text == "true"
    if field in FLOATS:
        try: value = float(text)
        except (ValueError, OverflowError): invalid("Owned-result number is invalid.")
        if not math.isfinite(value): invalid("Owned-result number is nonfinite.")
        if field in {"gross_exposure", "commission", "allocated_commissions", "limit_price", "stop_price", "mark_price"} and value < 0:
            invalid("Owned-result number is outside its domain.")
        if field in {"reference_open", "price", "gross_notional"} and value <= 0:
            invalid("Owned-result execution price or notional is invalid.")
        return value
    if field == "symbol" and (not text or any(ord(c) < 32 for c in text)):
        invalid("Owned-result symbol is invalid.")
    return text


def csv_rows(root, snapshots, filename, columns=None):
    try:
        data = read_stable(root, filename, FILE_LIMIT, snapshots[filename])
        reader = csv.DictReader(io.StringIO(data.decode("utf-8")), strict=True)
        if reader.fieldnames is None or columns is not None and tuple(reader.fieldnames) != columns:
            invalid("Owned-result table columns are invalid.")
        for row in reader:
            if None in row or None in row.values(): invalid("Owned-result table row is invalid.")
            yield row
    except (KeyError, UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise ArtifactError("artifact_invalid", 422, "Owned-result table is invalid.") from exc


def owned_rows(root, snapshots, semantic):
    for row in csv_rows(root, snapshots, TABLE_FILES[semantic], TABLE_COLUMNS[semantic]):
        yield {key: cell(key, value, semantic) for key, value in row.items()}


def paired(root, snapshots, semantic, legacy, projection):
    """Compare persisted common facts; never derive new financial results."""
    for owned, old in zip_longest(owned_rows(root, snapshots, semantic), csv_rows(root, snapshots, legacy)):
        if owned is None or old is None: invalid("Legacy and owned-result row counts disagree.")
        for old_key, new_key in projection.items():
            if old_key not in old or cell(new_key, old[old_key], semantic) != owned[new_key]:
                invalid("Legacy and owned-result fields disagree.")
        yield owned


def ordered(rows, *, strict_time=False, strict_sequence=True):
    previous_time = previous_sequence = None
    for row in rows:
        timestamp, sequence = row["timestamp_ns"], row["sequence"]
        if previous_time is not None and (timestamp < previous_time or strict_time and timestamp == previous_time):
            invalid("Owned-result timestamps are not chronological.")
        if previous_sequence is not None and (sequence < previous_sequence or strict_sequence and sequence == previous_sequence):
            invalid("Owned-result sequences are not ordered.")
        previous_time, previous_sequence = timestamp, sequence
        yield row


def sampling_grid(manifest):
    sampling = manifest["sampling"]
    if not isinstance(sampling, dict) or sampling.get("policy") != "last_event_at_or_before_v1":
        invalid("Owned-result sampling policy is invalid.")
    times, stale = sampling.get("timestamps_ns"), sampling.get("max_staleness_ns")
    if (not isinstance(times, list) or len(times) < 2 or
            any(type(t) is not int or not -2**63 <= t <= I64 for t in times) or
            any(a >= b for a, b in zip(times, times[1:])) or
            times[0] != manifest["dataset_start_ns"] or times[-1] != manifest["dataset_end_ns"] or
            type(stale) is not int or not 0 <= stale <= I64):
        invalid("Owned-result sampling grid or staleness is invalid.")
    if "interval_ns" in sampling:
        interval = sampling["interval_ns"]
        if type(interval) is not int or not 0 < interval <= I64 or any(b - a != interval for a, b in zip(times, times[1:])):
            invalid("Owned-result sampling interval disagrees with its grid.")
    return times, stale


def validate_order_state(order):
    status = order["status"]
    if ((order["rejection_reason"] is not None) != (status == "REJECTED") or
            (order["cancellation_reason"] is not None) != (status == "CANCELED")):
        invalid("Owned-result order status and reasons disagree.")
    filled, remaining = order["filled_quantity"], order["remaining_quantity"]
    if (status in {"NEW", "OPEN", "REJECTED"} and filled != 0 or
            status == "FILLED" and remaining != 0 or
            status == "PARTIALLY_FILLED" and (filled == 0 or remaining == 0) or
            status == "CANCELED" and remaining == 0):
        invalid("Owned-result order status and quantities disagree.")


def validate_v2(root, snapshots, manifest, report):
    """Return recognized filenames only after a complete structural check."""
    try:
        wrapper = strict_json(read_stable(root, "research_export.json", METADATA_LIMIT, snapshots["research_export.json"]))
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise ArtifactError("artifact_invalid", 422, "Owned-result wrapper is invalid.") from exc
    if not isinstance(wrapper, dict): invalid("Owned-result wrapper is invalid.")
    if type(wrapper.get("schema_version")) is not int or wrapper["schema_version"] != 2 or wrapper.get("artifact_kind") != "research_export":
        raise ArtifactError("artifact_unsupported", 422, "Owned-result export version is unsupported.")
    if set(wrapper) != {"schema_version", "artifact_kind", "lineage", "capabilities", "files"}:
        invalid("Owned-result wrapper fields are invalid.")
    expected_lineage = {"legacy_format": "single_run_v1", "manifest": "manifest.json", "report": "report.json",
                        "source_identity": manifest["source_identity"], "dataset_hash": manifest["dataset_hash"]}
    if wrapper["lineage"] != expected_lineage: invalid("Owned-result lineage disagrees with the manifest.")
    sampled = manifest.get("sampling") is not None
    capabilities = {**CAPABILITIES, "sampled_event_sequence": "recorded" if sampled else "not_recorded"}
    files = {k: v for k, v in TABLE_FILES.items() if sampled or k != "sampled_equity_events"}
    if wrapper["capabilities"] != capabilities or wrapper["files"] != files:
        invalid("Owned-result capabilities or file map is inconsistent.")
    if not set(files.values()) <= set(snapshots) or sampled != ("sampled_equity.csv" in snapshots):
        invalid("Owned-result files are incomplete.")
    if not sampled and "sampled_equity_events.csv" in snapshots:
        invalid("Unconfigured owned-result sampling file is present.")

    orders = {}
    order_projection = {k: k for k in ("order_id", "symbol", "status", "detail")}
    for order in paired(root, snapshots, "order_snapshots", "orders.csv", order_projection):
        key = order["order_id"]
        if key in orders or order["filled_quantity"] + order["remaining_quantity"] != order["quantity"]:
            invalid("Owned-result order quantities or identities are inconsistent.")
        if order["eligible_after_sequence"] < order["submission_sequence"]: invalid()
        if orders and (key <= next(reversed(orders)) or order["submission_sequence"] <= orders[next(reversed(orders))]["submission_sequence"]): invalid()
        # Rejected invalid requests remain facts. Do not reapply accepted-order
        # limit/stop combinations to every final snapshot here.
        validate_order_state(order)
        orders[key] = order
    if len(orders) != report["order_count"]: invalid("Owned-result order count disagrees with report.")

    fills, filled_quantities = {}, {}
    fill_projection = {k: k for k in ("fill_id", "order_id", "symbol", "side", "quantity", "timestamp_ns", "price", "commission")}
    for fill in ordered(paired(root, snapshots, "fill_events", "fills.csv", fill_projection)):
        key = fill["fill_id"]
        order = orders.get(fill["order_id"])
        if key in fills or order is None or fill["symbol"] != order["symbol"] or fill["side"] != order["side"]: invalid()
        if fill["timestamp_ns"] < order["submitted_ns"] or fill["sequence"] <= order["eligible_after_sequence"]: invalid()
        if fills and key <= next(reversed(fills)): invalid()
        filled_quantities[fill["order_id"]] = filled_quantities.get(fill["order_id"], 0) + fill["quantity"]
        fills[key] = fill
    if len(fills) != report["fill_count"] or any(filled_quantities.get(key, 0) != value["filled_quantity"] for key, value in orders.items()):
        invalid("Owned-result fill counts disagree with the recorded order/report.")

    equity_projection = {k: k for k in ("timestamp_ns", "equity", "gross_exposure")}
    equity = list(ordered(paired(root, snapshots, "equity_events", "equity.csv", equity_projection)))
    if not equity or equity[0]["timestamp_ns"] != manifest["dataset_start_ns"] or equity[-1]["timestamp_ns"] != manifest["dataset_end_ns"]:
        invalid("Owned-result equity boundaries disagree with the manifest.")
    if sampled:
        times, stale = sampling_grid(manifest)
        cursor = 0
        points = ordered(paired(root, snapshots, "sampled_equity_events", "sampled_equity.csv", equity_projection), strict_time=True, strict_sequence=False)
        for timestamp, point in zip_longest(times, points):
            if timestamp is None or point is None or timestamp != point["timestamp_ns"]:
                invalid("Sampled equity does not match its complete recorded grid.")
            while cursor + 1 < len(equity) and equity[cursor + 1]["timestamp_ns"] <= point["timestamp_ns"]: cursor += 1
            source = equity[cursor]
            if not 0 <= point["timestamp_ns"] - source["timestamp_ns"] <= stale or any(source[k] != point[k] for k in ("sequence", "equity", "gross_exposure")): invalid("Sampled equity does not refer to its recorded source event.")

    fills_by_sequence = {fill["sequence"]: fill for fill in fills.values()}
    submissions, cancellations, filled_events = set(), set(), set()
    for event in ordered(owned_rows(root, snapshots, "order_events")):
        key = event["order_id"]
        if not manifest["dataset_start_ns"] <= event["timestamp_ns"] <= manifest["dataset_end_ns"]: invalid()
        if key is None:
            if event["kind"] != "CANCEL_UNKNOWN": invalid("Order lifecycle event has no order identity.")
            continue
        order = orders.get(key)
        if event["kind"] == "CANCEL_UNKNOWN":
            if order is not None and order["submission_sequence"] <= event["sequence"]: invalid()
        elif order is None or order["submission_sequence"] > event["sequence"] or order["submitted_ns"] > event["timestamp_ns"]:
            invalid("Order event refers to an unavailable order.")
        if event["kind"] in {"ACCEPTED", "REJECTED"}:
            expected = "REJECTED" if order["status"] == "REJECTED" else "ACCEPTED"
            if (key in submissions or event["kind"] != expected or
                    event["sequence"] != order["submission_sequence"] or event["timestamp_ns"] != order["submitted_ns"]): invalid()
            submissions.add(key)
        if event["kind"] == "CANCELED":
            if key in cancellations or order["status"] != "CANCELED" or event["sequence"] <= order["submission_sequence"]: invalid()
            cancellations.add(key)
        if event["kind"] == "FILLED":
            fill = fills_by_sequence.get(event["sequence"])
            if fill is None or fill["order_id"] != key or fill["timestamp_ns"] != event["timestamp_ns"]: invalid()
            filled_events.add(fill["fill_id"])
    if (submissions != set(orders) or filled_events != set(fills) or
            cancellations != {key for key, order in orders.items() if order["status"] == "CANCELED"}):
        invalid("Owned-result lifecycle history is incomplete.")

    positions = {}
    for position in owned_rows(root, snapshots, "positions"):
        if position["symbol"] in positions: invalid("Duplicate final position.")
        positions[position["symbol"]] = position
        absent = [position[k] is None for k in ("mark_price", "mark_ns", "mark_sequence")]
        if any(absent) and not all(absent): invalid("Position mark facts are partially missing.")
        if not any(absent) and not (
                equity[0]["timestamp_ns"] <= position["mark_ns"] <= equity[-1]["timestamp_ns"] and
                equity[0]["sequence"] < position["mark_sequence"] < equity[-1]["sequence"]):
            invalid("Position valuation mark lies outside the run.")
    if any(fill["symbol"] not in positions for fill in fills.values()): invalid("Filled symbol has no final position record.")

    episode_ids, open_quantities = set(), {}
    for semantic, count_name, closed in (("closed_trades", "trade_count", True), ("open_trades", "open_trade_count", False)):
        count = 0
        for trade in owned_rows(root, snapshots, semantic):
            count += 1
            key = (trade["symbol"], trade["opening_fill_id"])
            if key in episode_ids: invalid("Duplicate trade episode.")
            episode_ids.add(key)
            if trade["is_closed"] is not closed or trade["opened_quantity"] == 0 or trade["opened_quantity"] - trade["closed_quantity"] != trade["remaining_quantity"]: invalid()
            closing_keys = ("closing_fill_id", "closed_ns", "closing_sequence", "outcome")
            if any((trade[k] is not None) != closed for k in closing_keys): invalid("Trade closure facts are inconsistent.")
            if closed and trade["remaining_quantity"] != 0 or not closed and trade["remaining_quantity"] == 0: invalid()
            opening = fills.get(trade["opening_fill_id"])
            if opening is None or opening["symbol"] != trade["symbol"] or opening["timestamp_ns"] != trade["opened_ns"] or opening["sequence"] != trade["opening_sequence"]: invalid()
            if opening["side"] != ("BUY" if trade["direction"] == "LONG" else "SELL"): invalid()
            if not closed:
                symbol = trade["symbol"]
                if symbol in open_quantities: invalid("Multiple open trade episodes for one symbol.")
                open_quantities[symbol] = trade["remaining_quantity"] * (1 if trade["direction"] == "LONG" else -1)
            if closed:
                closing = fills.get(trade["closing_fill_id"])
                if closing is None or closing["symbol"] != trade["symbol"] or closing["timestamp_ns"] != trade["closed_ns"] or closing["sequence"] != trade["closing_sequence"]: invalid()
                if closing["side"] == opening["side"] or trade["closing_sequence"] <= trade["opening_sequence"]: invalid()
        if count != report[count_name]: invalid("Owned-result trade count disagrees with the report.")
    if any(position["quantity"] != open_quantities.get(symbol, 0) for symbol, position in positions.items()):
        invalid("Final position and open episode quantities disagree.")
    return {"research_export.json", *files.values()}
