from __future__ import annotations

import json
from pathlib import Path

import pytest

import qte
from qte.adapters import CsvBarSchema, load_alpaca_fixture, load_csv_bars


ROOT = Path(__file__).resolve().parents[2]
HOUR_NS = 3_600_000_000_000
ALPACA = ROOT / "tests/fixtures/alpaca/stock_bars_complete.json"
CSV = ROOT / "tests/fixtures/csv/stock_bars_complete.csv"


class BuyOnce(qte.Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.done = False

    def on_bar(self, context, bar) -> None:
        if not self.done:
            context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 1))
            self.done = True


def run(data):
    engine = qte.BacktestEngine(qte.BacktestConfig(1_000.0, build_identity="adapter-test"))
    return engine.run(data, BuyOnce())


def test_alpaca_saved_fixture_normalizes_with_provenance() -> None:
    result = load_alpaca_fixture(ALPACA, interval_ns=HOUR_NS)
    assert result.dataset.bar_count == 3
    assert result.dataset.gap_count == 0
    assert result.provenance.provider == "alpaca"
    assert result.provenance.schema_id == "alpaca-market-data-v2-stock-bars"
    assert len(result.provenance.source_sha256) == 64
    replay = run(result.dataset)
    assert replay.fills[0].executed_price == 110.0
    assert replay.equity_curve[-1].timestamp_ns == 1_704_214_800_123_456_789


def test_missing_alpaca_field_partial_page_and_assumptions_fail(tmp_path: Path) -> None:
    payload = json.loads(ALPACA.read_text())
    del payload["bars"]["SPY"][0]["o"]
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="missing fields"):
        load_alpaca_fixture(missing, interval_ns=HOUR_NS)

    payload = json.loads(ALPACA.read_text())
    payload["next_page_token"] = "more"
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="incomplete paginated"):
        load_alpaca_fixture(partial, interval_ns=HOUR_NS)

    payload = json.loads(ALPACA.read_text())
    payload["_qte"]["adjustment"] = "all"
    adjusted = tmp_path / "adjusted.json"
    adjusted.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="adjustment='raw'"):
        load_alpaca_fixture(adjusted, interval_ns=HOUR_NS)


def test_csv_and_alpaca_have_equivalent_canonical_replay() -> None:
    alpaca = load_alpaca_fixture(ALPACA, interval_ns=HOUR_NS)
    csv_result = load_csv_bars(CSV, interval_ns=HOUR_NS, action_free=True)
    assert csv_result.dataset.bar_count == alpaca.dataset.bar_count
    assert csv_result.provenance.provider == "csv"
    alpaca_run = run(alpaca.dataset)
    csv_run = run(csv_result.dataset)
    assert [fill.executed_price for fill in alpaca_run.fills] == [
        fill.executed_price for fill in csv_run.fills
    ]
    assert [point.equity for point in alpaca_run.equity_curve] == [
        point.equity for point in csv_run.equity_curve
    ]
    assert alpaca_run.manifest.source_id != csv_run.manifest.source_id


def test_csv_schema_mapping_is_explicit(tmp_path: Path) -> None:
    renamed = tmp_path / "renamed.csv"
    renamed.write_text(
        "ticker,time,o,h,l,c,v\n"
        "SPY,2024-01-02T14:00:00Z,100,101,99,100,1\n"
    )
    schema = CsvBarSchema(
        symbol="ticker", timestamp="time", open="o", high="h", low="l", close="c", volume="v"
    )
    result = load_csv_bars(
        renamed, interval_ns=HOUR_NS, schema=schema, action_free=True
    )
    assert result.dataset.bar_count == 1
    with pytest.raises(ValueError, match="missing mapped columns"):
        load_csv_bars(renamed, interval_ns=HOUR_NS, action_free=True)
