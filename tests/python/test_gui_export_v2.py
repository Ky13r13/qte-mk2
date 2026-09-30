from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

import qte.cli
from qte.cli import run_research
from qte.gui.artifacts import ArtifactError, inspect_artifact
from qte.gui.catalog import Catalog
from qte.gui.run_views import RunViews
from test_research_export import INTERVAL, START, Reversal


ROOT = Path(__file__).parents[2]


def _export(tmp_path: Path, name: str, *, sampled: bool = True) -> Path:
    """Write a real CLI export below this test repository's build directory."""
    config = ROOT / "examples/research-run.json"
    if not sampled:
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload.pop("sampling")
        payload["data"]["path"] = str((ROOT / "examples/research-bars.csv").resolve())
        config = tmp_path / "research-run-without-sampling.json"
        config.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "build" / name
    run_research(config, output, export_version=2)
    return output


def _rehash_export(root: Path) -> None:
    """Refresh only a temporary, complete v2 export after a deliberate mutation."""
    if root.parent.name != "build" or not (root / "research_export.json").is_file() or not (root / "complete.json").is_file():
        raise AssertionError("rehash is restricted to an isolated v2 test export")
    checks = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in root.iterdir() if path.name != "complete.json"}
    (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checks}), encoding="utf-8")


def _csv_rows(root: Path, name: str) -> tuple[list[str], list[dict[str, str]]]:
    with (root / name).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames is not None
        return list(reader.fieldnames), list(reader)


def _write_csv(root: Path, name: str, columns: list[str], rows: list[dict[str, str]]) -> None:
    with (root / name).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)


def _expect_rejected(root: Path, code: str) -> None:
    with pytest.raises(ArtifactError) as failure:
        inspect_artifact(root)
    assert failure.value.code == code

    # Registration must retain an explicit, non-verified record instead of
    # treating a tagged v2 export as an unrelated v1 artifact.
    detail = Catalog(root.parents[1]).register(f"build/{root.name}")
    assert detail["kind"] == "research_export_v2"
    assert detail["status"]["integrity"] != "verified"
    assert detail["errors"] == [code]


@pytest.mark.parametrize("sampled", [False, True])
def test_real_v2_exports_are_verified_with_and_without_sampled_records(tmp_path, sampled):
    root = _export(tmp_path, f"v2-{'sampled' if sampled else 'event-only'}", sampled=sampled)
    artifact = inspect_artifact(root)

    assert artifact.kind == "research_export_v2"
    assert artifact.metadata["integrity"] == "verified"
    assert "equity_events.csv" in artifact.known
    assert ("sampled_equity_events.csv" in artifact.known) is sampled


def test_v1_export_remains_a_single_run_v1_artifact(tmp_path):
    output = tmp_path / "build" / "v1"
    run_research(ROOT / "examples/research-run.json", output)

    artifact = inspect_artifact(output)
    assert artifact.kind == "single_run_v1"
    assert artifact.metadata["integrity"] == "verified"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("unsupported_version", "artifact_unsupported"),
        ("wrong_wrapper_shape", "artifact_invalid"),
        ("missing_owned_table", "artifact_invalid"),
    ],
)
def test_tagged_wrapper_errors_do_not_fallback_to_v1(tmp_path, mutation, code):
    root = _export(tmp_path, f"v2-wrapper-{mutation}")
    wrapper = json.loads((root / "research_export.json").read_text(encoding="utf-8"))
    if mutation == "unsupported_version":
        wrapper["schema_version"] = 3
    elif mutation == "wrong_wrapper_shape":
        wrapper["lineage"] = []
    else:
        wrapper["files"].pop("positions")
    (root / "research_export.json").write_text(json.dumps(wrapper), encoding="utf-8")
    _rehash_export(root)

    _expect_rejected(root, code)


@pytest.mark.parametrize("mutation", ["header", "noncanonical_sequence", "reversed_equity"])
def test_rehashed_v2_table_structure_and_order_still_validate(tmp_path, mutation):
    root = _export(tmp_path, f"v2-structure-{mutation}")
    columns, rows = _csv_rows(root, "equity_events.csv")
    if mutation == "header":
        text = (root / "equity_events.csv").read_text(encoding="utf-8")
        (root / "equity_events.csv").write_text(text.replace("sequence", "source_sequence", 1), encoding="utf-8")
    elif mutation == "noncanonical_sequence":
        rows[0]["sequence"] = "01"
    else:
        assert len(rows) >= 2
        rows[0], rows[1] = rows[1], rows[0]
    if mutation != "header": _write_csv(root, "equity_events.csv", columns, rows)
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


@pytest.mark.parametrize("mutation", ["float_count", "count_projection", "noncanonical_fill_id"])
def test_v2_projections_reject_noncanonical_or_inconsistent_count_and_id_values(tmp_path, mutation):
    root = _export(tmp_path, f"v2-projection-{mutation}")
    if mutation in {"float_count", "count_projection"}:
        report_path = root / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["fill_count"] = 1.0 if mutation == "float_count" else report["fill_count"] + 1
        report_path.write_text(json.dumps(report), encoding="utf-8")
    else:
        columns, rows = _csv_rows(root, "fill_events.csv")
        assert rows
        rows[0]["fill_id"] = "1.0"
        _write_csv(root, "fill_events.csv", columns, rows)
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


@pytest.mark.parametrize("mutation", ["fill_order_reference", "episode_closing_fill", "partial_position_mark"])
def test_v2_cross_table_and_optional_mark_contracts_are_checked(tmp_path, mutation):
    root = _export(tmp_path, f"v2-reference-{mutation}")
    if mutation == "fill_order_reference":
        columns, rows = _csv_rows(root, "fill_events.csv")
        assert rows
        rows[0]["order_id"] = "999999999"
        _write_csv(root, "fill_events.csv", columns, rows)
    elif mutation == "episode_closing_fill":
        columns, rows = _csv_rows(root, "closed_trades.csv")
        assert rows
        rows[0]["closing_fill_id"] = "999999999"
        _write_csv(root, "closed_trades.csv", columns, rows)
    else:
        columns, rows = _csv_rows(root, "positions.csv")
        assert rows
        rows[0]["mark_price"] = ""
        assert rows[0]["mark_ns"] and rows[0]["mark_sequence"]
        _write_csv(root, "positions.csv", columns, rows)
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


def test_copied_v2_owned_csv_stays_protected_for_catalog_and_run_views(tmp_path):
    root = _export(tmp_path, "v2-protected")
    copied = (root / "positions.csv").read_bytes()
    unknown = tmp_path / "build" / "earlier-protected"; unknown.mkdir(parents=True)
    (unknown / "attachment.bin").write_bytes(copied)

    catalog = Catalog(tmp_path)
    catalog.register("build/earlier-protected")
    detail = catalog.register("build/v2-protected")
    positions = next(row for row in detail["files"] if row["name"] == "positions.csv")
    assert positions["protection"] == "protected"
    assert positions["available"] is False
    with pytest.raises(ArtifactError) as failure:
        RunViews(catalog).table(detail["id"], "positions")
    assert failure.value.code == "holdout_protected"


def test_v2_reader_accepts_a_full_engine_reversal_with_carry_sampling(tmp_path, monkeypatch):
    """Use the CLI's real data/load/analyze/export path, not a fabricated result."""
    bars = tmp_path / "reversal-bars.csv"
    with bars.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream); writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume"))
        for index, price in enumerate((100.0, 110.0, 120.0, 115.0)):
            timestamp = START + index * INTERVAL
            seconds, nanoseconds = divmod(timestamp, 1_000_000_000)
            rendered = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            writer.writerow(("SPY", f"{rendered}.{nanoseconds:09d}Z", price, price + 1, price - 1, price, 1_000.0))
    grid = [START, START + INTERVAL // 2, START + INTERVAL, START + 2 * INTERVAL, START + 4 * INTERVAL]
    config = tmp_path / "reversal-run.json"
    config.write_text(json.dumps({
        "data": {"adapter": "csv", "path": bars.name, "interval_ns": INTERVAL, "action_free": True},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY", "fast_period": 2, "slow_period": 3, "quantity": 1},
        "engine": {"initial_cash": 10_000.0, "history_capacity": 256, "random_seed": 0},
        "risk": {"max_order_quantity": 10, "allow_short": True},
        "sampling": {"timestamps_ns": grid, "max_staleness_ns": INTERVAL, "periods_per_year": 8766},
    }), encoding="utf-8")
    monkeypatch.setattr(qte.cli, "MovingAverageRegimeStrategy", lambda *_: Reversal())
    output = tmp_path / "build" / "v2-real-reversal"
    run_research(config, output, export_version=2)

    artifact = inspect_artifact(output)
    assert artifact.kind == "research_export_v2"
    assert artifact.metadata["integrity"] == "verified"
    closed = _csv_rows(output, "closed_trades.csv")[1]
    opened = _csv_rows(output, "open_trades.csv")[1]
    sampled = _csv_rows(output, "sampled_equity_events.csv")[1]
    assert closed and opened and sampled
    assert closed[0]["closing_fill_id"] == opened[0]["opening_fill_id"]
    assert any(left["sequence"] == right["sequence"] for left, right in zip(sampled, sampled[1:]))


def test_sampled_grid_cannot_be_replaced_by_two_header_only_csvs(tmp_path):
    root = _export(tmp_path, "v2-empty-sampled-grid")
    for name in ("sampled_equity.csv", "sampled_equity_events.csv"):
        columns, _ = _csv_rows(root, name)
        _write_csv(root, name, columns, [])
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


@pytest.mark.parametrize("mutation", ["missing", "header_only"])
def test_missing_or_empty_owned_order_event_history_is_invalid(tmp_path, mutation):
    root = _export(tmp_path, f"v2-order-events-{mutation}")
    if mutation == "missing":
        (root / "order_events.csv").unlink()
    else:
        columns, _ = _csv_rows(root, "order_events.csv")
        _write_csv(root, "order_events.csv", columns, [])
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


def test_filled_lifecycle_event_cannot_omit_its_order_identity(tmp_path):
    root = _export(tmp_path, "v2-filled-without-order")
    columns, rows = _csv_rows(root, "order_events.csv")
    filled = next(row for row in rows if row["kind"] == "FILLED")
    filled["order_id"] = ""
    _write_csv(root, "order_events.csv", columns, rows)
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


def test_filled_final_order_cannot_claim_a_risk_rejection(tmp_path):
    root = _export(tmp_path, "v2-filled-risk-reason")
    columns, rows = _csv_rows(root, "order_snapshots.csv")
    filled = next(row for row in rows if row["status"] == "FILLED")
    filled["rejection_reason"] = "RISK"
    _write_csv(root, "order_snapshots.csv", columns, rows)
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


@pytest.mark.parametrize("mutation", ["time_after_run", "sequence_after_run"])
def test_position_mark_cannot_extend_past_the_final_recorded_event(tmp_path, mutation):
    root = _export(tmp_path, f"v2-position-after-run-{mutation}")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    columns, rows = _csv_rows(root, "positions.csv")
    assert rows
    if mutation == "time_after_run":
        rows[0]["mark_ns"] = str(manifest["dataset_end_ns"] + 1)
    else:
        rows[0]["mark_sequence"] = str(2**64 - 1)
    _write_csv(root, "positions.csv", columns, rows)
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


def test_sampled_carry_cannot_exceed_its_recorded_staleness_bound(tmp_path):
    root = _export(tmp_path, "v2-sampled-stale")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grid = manifest["sampling"]["timestamps_ns"]
    carry_time = grid[0] + 1
    manifest["sampling"]["timestamps_ns"] = [grid[0], carry_time, *grid[1:]]
    manifest["sampling"].pop("interval_ns")
    manifest["sampling"]["max_staleness_ns"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for name in ("sampled_equity.csv", "sampled_equity_events.csv"):
        columns, rows = _csv_rows(root, name)
        carried = dict(rows[0]); carried["timestamp_ns"] = str(carry_time)
        _write_csv(root, name, columns, [rows[0], carried, *rows[1:]])
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")


def test_final_position_quantity_must_agree_with_open_trade_episodes(tmp_path):
    root = _export(tmp_path, "v2-position-quantity")
    columns, rows = _csv_rows(root, "positions.csv")
    assert rows
    rows[0]["quantity"] = str(int(rows[0]["quantity"]) + 1)
    _write_csv(root, "positions.csv", columns, rows)
    _rehash_export(root)

    _expect_rejected(root, "artifact_invalid")
