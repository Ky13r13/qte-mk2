from dataclasses import replace
import json

import pytest
import qte
from qte.cli import run_research
from qte.strategies.book151 import BOOK_FAMILIES, BookConfig, BookStrategy, book_signals, futures_weights
from qte.strategies.nasdaq_gamma import FuturesMark
from qte.option_structures import RECIPES, SPXStructure


SECOND = 1_000_000_000


def config(kind):
    return BookConfig(kind=kind, fast_period=1, middle_period=2, slow_period=3,
                      channel_period=2, atr_period=2, max_holding_bars=2, stop_atr_multiple=100)


def bars():
    closes = [100, 103, 101, 99, 100, 102, 104, 103, 99, 98, 103, 104]
    return [qte.Bar("SPY", i*SECOND, (i+1)*SECOND, p,
                    p+(9 if i % 2 == 0 else 1), p-(1 if i % 2 == 0 else 9), p, 100)
            for i, p in enumerate(closes)]


@pytest.mark.parametrize("kind", BOOK_FAMILIES)
def test_engine_execution_reproducible_and_cost_sensitive(kind):
    history = bars()
    data = qte.Dataset.from_bars(history, interval_ns=SECOND, source_id="synthetic-book-mechanics")
    strategy = BookStrategy("SPY", config(kind))
    engine = qte.BacktestEngine(qte.BacktestConfig(100000))
    first = engine.run(data, strategy)
    second = engine.run(data, strategy)
    assert len(first.fills) >= 2
    assert [(f.effective_ns, f.executed_price, f.quantity) for f in first.fills] == [
        (f.effective_ns, f.executed_price, f.quantity) for f in second.fills]
    assert first.fills[0].effective_ns >= config(kind).history_required*SECOND
    costs = qte.ExecutionCosts(commission_bps=10, spread_bps=0, slippage_bps=0)
    costly = qte.BacktestEngine(qte.BacktestConfig(100000, execution_costs=costs)).run(data, BookStrategy("SPY", config(kind)))
    assert sum(f.commission for f in costly.fills) > 0
    assert costly.equity_curve[-1].equity < first.equity_curve[-1].equity


def test_known_single_ma_channel_and_ibs_signals():
    history = bars()[:4]
    enter, exit_, _ = book_signals(history, config("book_single_ma"))
    assert not enter and exit_
    assert book_signals(history, config("book_channel_reversal"))[:2] == (True, False)
    assert book_signals(bars()[:3], config("book_ibs_reversal"))[:2] == (True, False)
    assert book_signals(history, config("book_ibs_reversal"))[:2] == (False, True)
    flat = [qte.Bar("SPY", i*SECOND, (i+1)*SECOND, 100, 100, 100, 100, 0) for i in range(4)]
    assert not book_signals(flat, config("book_ibs_reversal"))[0]
    assert not book_signals(flat, config("book_channel_reversal"))[0]


def marks(symbol, values):
    return [FuturesMark(symbol, i*SECOND, i*SECOND, p, "synthetic", str(i)) for i, p in enumerate(values)]


def weights(history, **kwargs):
    return futures_weights(history, at_ns=3*SECOND, interval_ns=SECOND, lookback=3,
                           volatility_period=3, **kwargs)


def test_futures_momentum_and_contrarian_are_different_and_causal():
    history = {"ESZ6": marks("ESZ6", [100, 102, 101, 104]), "NQZ6": marks("NQZ6", [100, 99, 100, 96])}
    trend = weights(history)
    contra = weights(history, kind="cross_sectional_contrarian")
    assert trend["weights"]["ESZ6"] > 0 and trend["weights"]["NQZ6"] < 0
    assert sum(abs(w) for w in trend["weights"].values()) == pytest.approx(1)
    assert contra["weights"] == pytest.approx({"ESZ6": -0.5, "NQZ6": 0.5})
    assert not trend["execution_enabled"]
    history["ESZ6"].append(FuturesMark("ESZ6", 4*SECOND, 4*SECOND, 1, "synthetic", "future"))
    assert weights(history) == trend
    history["NQZ6"][-1] = replace(history["NQZ6"][-1], available_ns=4*SECOND)
    with pytest.raises(ValueError):
        weights(history)


def test_futures_reject_ambiguous_zero_vol_and_single_name_relative_value():
    history = {"ESZ6": marks("ESZ6", [100, 100, 100, 100])}
    with pytest.raises(ValueError, match="volatility"):
        weights(history)
    with pytest.raises(ValueError, match="two instruments"):
        weights(history, kind="cross_sectional_contrarian")
    history["ESZ6"].append(history["ESZ6"][-1])
    with pytest.raises(ValueError, match="duplicate"):
        weights(history)


def test_futures_calendar_gaps_require_explicit_grid():
    grid = (0, SECOND, 4*SECOND, 5*SECOND)
    rows = [FuturesMark("ESZ6", t, t, p, "synthetic", str(t))
            for t,p in zip(grid, (100,102,101,104))]
    params = dict(at_ns=5*SECOND, interval_ns=SECOND, lookback=3, volatility_period=3)
    with pytest.raises(ValueError, match="gapped"):
        futures_weights({"ESZ6": rows}, **params)
    result = futures_weights({"ESZ6": rows}, expected_timestamps=grid, **params)
    assert result["weights"] == {"ESZ6": 1.0}
    with pytest.raises(ValueError, match="session grid"):
        futures_weights({"ESZ6": rows}, expected_timestamps=(0,SECOND,2*SECOND,5*SECOND), **params)


@pytest.mark.parametrize("name,strikes,premium,loss,profit", [
    ("bull_call_spread", (100,105), 2, 200, 300),
    ("bear_put_spread", (100,105), 2, 200, 300),
    ("bull_put_spread", (100,105), -2, 300, 200),
    ("bear_call_spread", (100,105), -2, 300, 200),
    ("long_straddle", (100,), 10, 1000, None),
    ("long_strangle", (95,105), 5, 500, None),
    ("long_call_butterfly", (100,105,110), 1, 100, 400),
    ("long_put_butterfly", (100,105,110), 1, 100, 400),
    ("credit_iron_butterfly", (95,100,105), -2, 300, 200),
    ("credit_iron_condor", (90,95,105,110), -2, 300, 200),
])
def test_expiry_risk_matches_piecewise_payoffs(name, strikes, premium, loss, profit):
    structure = SPXStructure(name, strikes, 10*SECOND)
    risk = structure.risk(net_debit_points=premium)
    assert risk["maximum_loss"] == loss
    assert risk["maximum_profit"] == profit
    assert not risk["loss_unbounded"]
    assert structure.risk(net_debit_points=premium, fees=7)["maximum_loss"] == loss+7
    for spot in (0, 90, 95, 100, 105, 110, 1000):
        pnl = structure.expiry_pnl(spot, net_debit_points=premium)
        assert pnl >= -loss
        if profit is not None:
            assert pnl <= profit


def test_combined_gamma_sign_and_surface():
    expiry = 7*86_400_000_000_000
    long = SPXStructure("long_straddle", (7000,), expiry).gamma_scenario(at_ns=0, forward=7000, volatility_pct=20)
    short = SPXStructure("credit_iron_butterfly", (6900,7000,7100), expiry).gamma_scenario(at_ns=0, forward=7000, volatility_pct=20)
    assert long["position_gamma"] > 0 > short["position_gamma"]
    assert len(long["surface"]["position_gamma"]) == 25
    assert len(long["surface"]["position_gamma"][0]) == 41
    assert long["mode"] == "model_only"


def test_option_contract_assumptions_are_explicit():
    with pytest.raises(ValueError):
        SPXStructure("bull_call_spread", (105,100), 100)
    with pytest.raises(ValueError):
        SPXStructure("bull_call_spread", (100,105), 100, settlement="american_physical")
    with pytest.raises(ValueError):
        SPXStructure("bull_call_spread", (100,105), 100, multiplier=50)


@pytest.mark.parametrize("kind", BOOK_FAMILIES)
def test_research_command_exports_registered_book_strategy(tmp_path, kind):
    history = bars()
    csv = "symbol,timestamp,open,high,low,close,volume\n" + "\n".join(
        f"SPY,2024-01-02T00:00:{i:02}Z,{b.open},{b.high},{b.low},{b.close},{b.volume}" for i, b in enumerate(history))
    (tmp_path/"bars.csv").write_text(csv)
    from dataclasses import asdict
    spec = asdict(config(kind)); spec.pop("kind")
    settings = {"data": {"adapter": "csv", "path": "bars.csv", "interval_ns": SECOND, "action_free": True},
                "strategy": {"type": kind, "symbol": "SPY", **spec}, "engine": {"initial_cash": 100000}}
    (tmp_path/"run.json").write_text(json.dumps(settings))
    run_research(tmp_path/"run.json", tmp_path/"result")
    manifest = json.loads((tmp_path/"result/manifest.json").read_text())
    assert manifest["strategy"]["type"] == kind
    assert json.loads((tmp_path/"result/report.json").read_text())["fill_count"] >= 2
