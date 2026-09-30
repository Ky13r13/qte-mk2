from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

import qte
import qte.cli
from qte.cli import run_research
from qte.research_export import CAPABILITIES, TABLE_COLUMNS, TABLE_FILES, write_owned_export


ROOT = Path(__file__).parents[2]
INTERVAL = 3_600_000_000_000
START = 9_007_199_254_740_993
ADDITIVE = set(TABLE_FILES.values()) | {"research_export.json"}


def _rows(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _dataset():
    prices = [100.0, 110.0, 120.0, 115.0]
    return qte.Dataset.from_bars([
        qte.Bar("SPY", START + index * INTERVAL, START + (index + 1) * INTERVAL,
                price, price + 1, price - 1, price, 1_000.0)
        for index, price in enumerate(prices)
    ], interval_ns=INTERVAL, source_id="owned-export-fixture")


class Reversal(qte.Strategy):
    def __init__(self):
        super().__init__(); self.started = False

    def on_bar(self, context, bar):
        if self.started: return
        self.started = True
        context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 3))
        context.submit_order(qte.limit_order("SPY", qte.OrderSide.BUY, 1, 1.0))
        context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 2**53 + 1))
        context.cancel_order(2**64 - 1)

    def on_fill(self, context, fill):
        if fill.side == qte.OrderSide.BUY:
            context.submit_order(qte.market_order("SPY", qte.OrderSide.SELL, 5))


class Cash(qte.Strategy):
    def on_bar(self, context, bar):
        pass


def _result(strategy=None):
    limits = qte.RiskLimits(max_order_quantity=10, allow_short=True)
    config = qte.BacktestConfig(10_000.0, risk_limits=limits, random_seed=2**64 - 1,
                                build_identity="owned-export-test")
    return qte.BacktestEngine(config).run(_dataset(), strategy or Reversal())


def _manifest(result):
    return {"source_identity": result.manifest.build_identity, "dataset_hash": result.manifest.dataset_hash}


def test_v2_is_strictly_additive_and_legacy_payloads_are_byte_identical(tmp_path):
    first = run_research(ROOT / "examples/research-run.json", tmp_path / "v1")
    second = run_research(ROOT / "examples/research-run.json", tmp_path / "v2", export_version=2)
    first_payloads = {path.name: path.read_bytes() for path in first.iterdir() if path.name != "complete.json"}
    second_payloads = {path.name: path.read_bytes() for path in second.iterdir()
                       if path.name != "complete.json" and path.name not in ADDITIVE}
    assert first_payloads == second_payloads
    completion = json.loads((second / "complete.json").read_text())
    assert completion["schema_version"] == 1
    assert set(completion["sha256"]) == {path.name for path in second.iterdir() if path.name != "complete.json"}
    for name, digest in completion["sha256"].items():
        assert hashlib.sha256((second / name).read_bytes()).hexdigest() == digest


def test_wrapper_tables_and_hand_calculated_reversal(tmp_path):
    result = _result(); output = tmp_path / "owned"; output.mkdir()
    grid = [START, START + INTERVAL // 2, START + INTERVAL // 2 + 1, START + 4 * INTERVAL]
    sampled = qte.sample_equity(result, qte.SamplingConfig(grid, max_staleness_ns=INTERVAL))
    write_owned_export(output, result, _manifest(result), sampled)

    wrapper = json.loads((output / "research_export.json").read_text())
    assert wrapper["schema_version"] == 2 and wrapper["artifact_kind"] == "research_export"
    assert wrapper["lineage"] == {"legacy_format": "single_run_v1", "manifest": "manifest.json",
        "report": "report.json", "source_identity": "owned-export-test",
        "dataset_hash": result.manifest.dataset_hash}
    assert wrapper["capabilities"] == {**CAPABILITIES, "sampled_event_sequence": "recorded"}
    assert wrapper["files"] == TABLE_FILES
    for semantic, filename in TABLE_FILES.items():
        with (output / filename).open(newline="", encoding="utf-8") as stream:
            assert tuple(next(csv.reader(stream))) == TABLE_COLUMNS[semantic]

    fills = _rows(output / "fill_events.csv")
    assert [(row["fill_id"], row["order_id"], row["quantity"], row["timestamp_ns"], row["sequence"])
            for row in fills] == [("1", "1", "3", str(START + INTERVAL), fills[0]["sequence"]),
                                  ("2", "4", "5", str(START + 2 * INTERVAL), fills[1]["sequence"])]
    assert fills[0]["reference_open"] == "110.0" and fills[0]["price"] == "110.0"
    assert fills[0]["gross_notional"] == "330.0" and fills[0]["commission"] == "0.0"

    closed, opened = _rows(output / "closed_trades.csv"), _rows(output / "open_trades.csv")
    assert len(closed) == len(opened) == 1
    assert closed[0]["opening_fill_id"] == "1" and closed[0]["closing_fill_id"] == "2"
    assert closed[0]["opened_quantity"] == closed[0]["closed_quantity"] == "3"
    assert closed[0]["remaining_quantity"] == "0" and closed[0]["net_realized_pnl"] == "30.0"
    assert closed[0]["is_closed"] == "true" and closed[0]["outcome"] == "WINNING"
    assert opened[0]["direction"] == "SHORT" and opened[0]["opening_fill_id"] == "2"
    assert opened[0]["opened_quantity"] == opened[0]["remaining_quantity"] == "2"
    assert opened[0]["closing_fill_id"] == opened[0]["closed_ns"] == opened[0]["closing_sequence"] == ""
    assert opened[0]["is_closed"] == "false" and opened[0]["outcome"] == ""

    positions = _rows(output / "positions.csv")
    assert positions[0]["quantity"] == "-2" and int(positions[0]["mark_ns"]) == START + 4 * INTERVAL
    orders = _rows(output / "order_snapshots.csv")
    rejected = next(row for row in orders if row["status"] == "REJECTED")
    canceled = next(row for row in orders if row["status"] == "CANCELED")
    assert rejected["quantity"] == str(2**53 + 1) and rejected["rejection_reason"] == "RISK"
    assert canceled["limit_price"] == "1.0" and canceled["cancellation_reason"] == "END_OF_DATA"
    unknown = next(row for row in _rows(output / "order_events.csv") if row["kind"] == "CANCEL_UNKNOWN")
    assert unknown["order_id"] == str(2**64 - 1)

    sampled_rows = _rows(output / "sampled_equity_events.csv")
    assert [row["timestamp_ns"] for row in sampled_rows] == [str(value) for value in grid]
    assert sampled_rows[1]["sequence"] == sampled_rows[2]["sequence"]


def test_empty_tables_keep_headers_and_sampling_is_omitted(tmp_path):
    result = _result(Cash()); output = tmp_path / "empty"; output.mkdir()
    write_owned_export(output, result, _manifest(result))
    wrapper = json.loads((output / "research_export.json").read_text())
    assert wrapper["capabilities"]["sampled_event_sequence"] == "not_recorded"
    assert "sampled_equity_events" not in wrapper["files"]
    assert not (output / "sampled_equity_events.csv").exists()
    for semantic in ("fill_events", "order_snapshots", "order_events", "closed_trades", "open_trades"):
        assert (output / TABLE_FILES[semantic]).read_text().splitlines() == [",".join(TABLE_COLUMNS[semantic])]
    assert _rows(output / "positions.csv")[0]["quantity"] == "0"


@pytest.mark.parametrize("version", [True, False, 0, 3, "2", None])
def test_invalid_export_version_fails_before_output(tmp_path, version):
    output = tmp_path / f"invalid-{version!s}"
    with pytest.raises(ValueError, match="export version"):
        run_research(ROOT / "examples/research-run.json", output, export_version=version)
    assert not output.exists()


def test_writer_and_run_never_overwrite_existing_output(tmp_path):
    result = _result(); output = tmp_path / "owned"; output.mkdir()
    write_owned_export(output, result, _manifest(result))
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(FileExistsError):
        write_owned_export(output, result, _manifest(result))
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    with pytest.raises(FileExistsError):
        run_research(ROOT / "examples/research-run.json", output, export_version=2)


def test_v2_runs_engine_once_and_reuses_one_sampled_projection(tmp_path, monkeypatch):
    engine_type = qte.cli.qte.BacktestEngine
    sample_function = qte.cli.qte.sample_equity
    calls = {"engine": 0, "sample": 0}

    def engine(*args, **kwargs):
        calls["engine"] += 1
        return engine_type(*args, **kwargs)

    def sample(*args, **kwargs):
        calls["sample"] += 1
        return sample_function(*args, **kwargs)

    monkeypatch.setattr(qte.cli.qte, "BacktestEngine", engine)
    monkeypatch.setattr(qte.cli.qte, "sample_equity", sample)
    run_research(ROOT / "examples/research-run.json", tmp_path / "once", export_version=2)
    assert calls == {"engine": 1, "sample": 1}
