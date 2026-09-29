"""Additive owned-result export tables for research_export schema v2."""

from __future__ import annotations

import csv
from enum import Enum
import json
import math
from pathlib import Path

TABLE_FILES = {
    "equity_events": "equity_events.csv",
    "sampled_equity_events": "sampled_equity_events.csv",
    "fill_events": "fill_events.csv",
    "order_snapshots": "order_snapshots.csv",
    "order_events": "order_events.csv",
    "positions": "positions.csv",
    "closed_trades": "closed_trades.csv",
    "open_trades": "open_trades.csv",
}

TABLE_COLUMNS = {
    "equity_events": ("timestamp_ns", "sequence", "equity", "gross_exposure"),
    "sampled_equity_events": ("timestamp_ns", "sequence", "equity", "gross_exposure"),
    "fill_events": ("fill_id", "order_id", "symbol", "side", "quantity", "timestamp_ns", "sequence",
                    "reference_open", "price", "gross_notional", "commission"),
    "order_snapshots": ("order_id", "symbol", "side", "quantity", "order_type", "limit_price", "stop_price",
                        "time_in_force", "submitted_ns", "submission_sequence", "eligible_after_sequence", "status",
                        "filled_quantity", "remaining_quantity", "stop_triggered", "rejection_reason",
                        "cancellation_reason", "detail"),
    "order_events": ("timestamp_ns", "sequence", "order_id", "kind", "detail"),
    "positions": ("symbol", "quantity", "mark_price", "mark_ns", "mark_sequence"),
    "closed_trades": ("symbol", "direction", "opening_fill_id", "opened_ns", "opening_sequence",
                      "closing_fill_id", "closed_ns", "closing_sequence", "opened_quantity", "closed_quantity",
                      "remaining_quantity", "realized_gross_pnl", "allocated_commissions", "net_realized_pnl",
                      "is_closed", "outcome"),
    "open_trades": ("symbol", "direction", "opening_fill_id", "opened_ns", "opening_sequence",
                    "closing_fill_id", "closed_ns", "closing_sequence", "opened_quantity", "closed_quantity",
                    "remaining_quantity", "realized_gross_pnl", "allocated_commissions", "net_realized_pnl",
                    "is_closed", "outcome"),
}

CAPABILITIES = {
    "event_sequence": "recorded",
    "order_snapshots": "recorded",
    "fill_sequence": "recorded",
    "positions": "recorded",
    "closed_trades": "recorded",
    "open_trades": "recorded",
    "order_events": "recorded",
    "sampled_event_sequence": "not_recorded",
}


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum) or (not isinstance(value, str) and isinstance(getattr(value, "name", None), str)):
        return value.name.upper()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("owned export contains a nonfinite value")
        return repr(value)
    if isinstance(value, int):
        return str(value)
    return value


def _write_csv(path: Path, semantic: str, rows) -> None:
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(TABLE_COLUMNS[semantic])
        writer.writerows(tuple(_cell(value) for value in row) for row in rows)


def _trade_rows(trades):
    for value in trades:
        yield (value.symbol, value.direction, value.opening_fill_id, value.opened_ns, value.opening_sequence,
               value.closing_fill_id, value.closed_ns, value.closing_sequence, value.opened_quantity,
               value.closed_quantity, value.remaining_quantity, value.realized_gross_pnl,
               value.allocated_commissions, value.net_realized_pnl, value.is_closed, value.outcome)


def write_owned_export(output: Path, result, manifest: dict, sampled_points=None) -> None:
    """Write only schema-v2 additive files into an existing fresh export directory."""
    output = Path(output)
    sampled_points = None if sampled_points is None else list(sampled_points)
    rows = {
        "equity_events": ((p.timestamp_ns, p.sequence, p.equity, p.gross_exposure) for p in result.equity_curve),
        "fill_events": ((f.id, f.order_id, f.symbol, f.side, f.quantity, f.effective_ns, f.effective_sequence,
                         f.reference_open, f.executed_price, f.gross_notional, f.commission) for f in result.fills),
        "order_snapshots": ((o.id, o.symbol, o.side, o.quantity, o.type, o.limit_price, o.stop_price,
                             o.time_in_force, o.submitted_ns, o.submission_sequence, o.eligible_after_sequence,
                             o.status, o.filled_quantity, o.remaining_quantity, o.stop_triggered,
                             o.rejection_reason, o.cancellation_reason, o.detail) for o in result.orders),
        "order_events": ((e.timestamp_ns, e.sequence, e.order_id, e.kind, e.detail) for e in result.order_events),
        "positions": ((p.symbol, p.quantity, p.mark_price, p.mark_ns, p.mark_sequence) for p in result.positions),
        "closed_trades": _trade_rows(result.trades),
        "open_trades": _trade_rows(result.open_trades),
    }
    if sampled_points is not None:
        rows["sampled_equity_events"] = ((p.timestamp_ns, p.sequence, p.equity, p.gross_exposure)
                                          for p in sampled_points)
    for semantic, values in rows.items():
        _write_csv(output / TABLE_FILES[semantic], semantic, values)

    capabilities = dict(CAPABILITIES)
    if sampled_points is not None:
        capabilities["sampled_event_sequence"] = "recorded"
    files = {semantic: TABLE_FILES[semantic] for semantic in rows}
    wrapper = {
        "schema_version": 2,
        "artifact_kind": "research_export",
        "lineage": {"legacy_format": "single_run_v1", "manifest": "manifest.json",
                    "report": "report.json", "source_identity": manifest["source_identity"],
                    "dataset_hash": manifest["dataset_hash"]},
        "capabilities": capabilities,
        "files": files,
    }
    with (output / "research_export.json").open("x", encoding="utf-8") as stream:
        json.dump(wrapper, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
