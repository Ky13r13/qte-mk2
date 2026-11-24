from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest
import qte
from qte.cli import run_research
from qte.strategies.volume import (VOLUME_FAMILIES, VolumeConfig, VolumeStrategy,
                                   _weighted_price, _cmf, _mfi, volume_signals)

SECOND = 1_000_000_000


def config(kind, **changes):
    return replace(VolumeConfig(kind=kind, fast_period=2, slow_period=3, volume_period=2,
                                breakout_period=2, exit_period=2, mfi_period=2, atr_period=2,
                                max_holding_bars=2, stop_atr_multiple=100), **changes)


def bars(kind):
    prices = [100, 100, 100, 102, 103, 104, 99, 98]
    volumes = [100, 100, 100, 300, 100, 100, 100, 100]
    if kind == "mfi_reversal":
        prices, volumes = [100, 98, 96, 97, 98, 100, 95, 94], [1000, 1000, 1000, 400, 1000, 1000, 1000, 1000]
    return [qte.Bar("SPY", i*SECOND, (i+1)*SECOND, p, p+.1,
                    p-(1 if kind == "cmf_trend" else .1), p, v)
            for i, (p, v) in enumerate(zip(prices, volumes))]


def run(history, cfg, strategy=None, **kwargs):
    data = qte.Dataset.from_bars(history, interval_ns=SECOND, source_id="synthetic-volume-mechanics")
    return qte.BacktestEngine(qte.BacktestConfig(100000, **kwargs)).run(
        data, strategy if strategy is not None else VolumeStrategy("SPY", cfg))


@pytest.mark.parametrize("kind", VOLUME_FAMILIES)
def test_engine_next_open_lifecycle_costs_and_reuse(kind):
    cfg, history = config(kind), bars(kind)
    strategy = VolumeStrategy("SPY", cfg)
    result = run(history, cfg, strategy)
    assert len(result.fills) == 2
    assert result.fills[0].effective_ns == 4*SECOND
    assert result.fills[0].executed_price == history[4].open
    assert result.fills[1].effective_ns == (5 if kind == "mfi_reversal" else 6)*SECOND
    assert result.fills[1].side == qte.OrderSide.SELL
    repeated = run(history, cfg, strategy)
    assert [p.equity for p in repeated.equity_curve] == [p.equity for p in result.equity_curve]
    costly = run(history, cfg, execution_costs=qte.ExecutionCosts(5, 10, 5))
    assert len(costly.fills) == 2
    assert costly.equity_curve[-1].equity < result.equity_curve[-1].equity


@pytest.mark.parametrize("kind", VOLUME_FAMILIES)
def test_zero_volume_blocks_entries_and_future_bars_do_not_change_first_order(kind):
    cfg, history = config(kind), bars(kind)
    empty = [qte.Bar(b.symbol, b.start_ns, b.end_ns, b.open, b.high, b.low, b.close, 0) for b in history]
    assert not run(empty, cfg).orders
    baseline = run(history, cfg)
    changed = history[:5]+[qte.Bar("SPY", i*SECOND, (i+1)*SECOND, 500, 510, 490, 500, 1000000)
                           for i in range(5, 8)]
    result = run(changed, cfg)
    assert result.fills[0].effective_ns == baseline.fills[0].effective_ns
    assert result.fills[0].executed_price == baseline.fills[0].executed_price
    assert result.fills[0].quantity == baseline.fills[0].quantity


def test_indicator_formulas_and_degenerate_cases():
    make = lambda i, c, v: qte.Bar("SPY", i*SECOND, (i+1)*SECOND, c, c+1, c-1, c, v)
    history = [make(0, 10, 1), make(1, 20, 3)]
    assert _weighted_price(history) == 17.5
    flat = [make(i, 100, 100) for i in range(3)]
    assert _weighted_price(flat) == _weighted_price(flat[-2:]) == 100
    assert not volume_signals(flat, config("vwma_trend"))[0]
    assert _cmf(history) == 0
    assert _mfi(history) == 100
    assert _mfi(list(reversed(history))) == 0
    assert _mfi([make(0, 10, 1), make(1, 10, 3)]) is None
    assert _cmf([qte.Bar("SPY", 0, SECOND, 10, 10, 10, 10, 100)]) == 0
    assert _weighted_price([make(0, 10, 0)]) is None
    mixed = [make(0, 10, 1), make(1, 20, 3), make(2, 10, 2)]
    assert _mfi(mixed) == 75  # +60 / (+60 + 20)
    cfg = config("volume_breakout", relative_volume=2.5)
    assert volume_signals(bars(cfg.kind)[:4], cfg)[0]  # 300 / prior mean 100, not inclusive mean


def test_obv_ties_do_not_invent_buying_volume_and_missing_history_fails():
    cfg = config("obv_confirmation")
    history = [qte.Bar("SPY", i*SECOND, (i+1)*SECOND, p, p+.1, p-.1, p, v)
               for i, (p, v) in enumerate([(100, 10), (102, 100), (101, 10), (101, 10000)])]
    assert not volume_signals(history, cfg)[0]  # Last two changes: -10 then zero.
    with pytest.raises(ValueError, match="history"):
        run(bars(cfg.kind), cfg, history_capacity=2)


class CancelOnce(VolumeStrategy):
    def __init__(self, cfg):
        super().__init__("SPY", cfg)
        self.cancel = True

    def on_bar(self, ctx, bar):
        super().on_bar(ctx, bar)
        if self.cancel and self._pending_order_id is not None:
            ctx.cancel_order(self._pending_order_id)
            self.cancel = False


def test_rejected_and_canceled_orders_recover_with_one_pending_intent():
    cfg, history = config("vwma_trend"), bars("vwma_trend")
    rejected_strategy = VolumeStrategy("SPY", cfg)
    rejected = run(history, cfg, rejected_strategy, risk_limits=qte.RiskLimits(max_order_quantity=1))
    assert len(rejected.orders) >= 2 and not rejected.fills
    assert rejected_strategy._pending_order_id is None
    canceled = run(history, cfg, CancelOnce(cfg))
    assert canceled.orders[0].status == qte.OrderStatus.CANCELED
    assert canceled.fills[0].effective_ns == 5*SECOND
    assert canceled.fills[0].order_id != canceled.orders[0].id


@pytest.mark.parametrize("changes", [{"volume_period": 0}, {"mfi_period": True},
    {"relative_volume": float("nan")}, {"cmf_entry": 1}, {"mfi_entry": 50, "mfi_exit": 20},
    {"allocation_fraction": 2}, {"stop_atr_multiple": 10**1000}])
def test_invalid_config(changes):
    with pytest.raises(ValueError):
        config("volume_breakout", **changes)


@pytest.mark.parametrize("kind", VOLUME_FAMILIES)
def test_cli_config_and_comparison_factory_are_registered(tmp_path, kind):
    from qte.comparison_config import _candidate, _build_candidate
    from qte.gui.research_forms import form_options, compile_configuration
    from qte.gui.comparison_forms import comparison_form_options
    cfg = config(kind)
    history = bars(kind)
    csv = "symbol,timestamp,open,high,low,close,volume\n"+"".join(
        f"SPY,2024-01-02T14:00:0{i}Z,{b.open},{b.high},{b.low},{b.close},{b.volume}\n"
        for i, b in enumerate(history))
    (tmp_path/"bars.csv").write_text(csv)
    params = asdict(cfg)
    params.pop("kind")
    conf = {"data": {"adapter": "csv", "path": "bars.csv", "interval_ns": SECOND, "action_free": True},
            "strategy": {"type": kind, "symbol": "SPY", **params}, "engine": {"initial_cash": 100000}}
    path = tmp_path/"config.json"
    path.write_text(json.dumps(conf))
    output = run_research(path, tmp_path/"result")
    assert json.loads((output/"report.json").read_text())["fill_count"] == 2
    assert json.loads((output/"manifest.json").read_text())["strategy"]["kind"] == kind
    candidate, compared_config = _build_candidate(_candidate({"name": "volume", "type": kind,
                                                "symbol": "SPY", "parameters": params}))
    assert compared_config == cfg
    assert any(x["type"] == kind for x in comparison_form_options()["strategies"])
    # The existing form renderer consumes this metadata; no browser/layout suite.
    choice = next(x for x in form_options()["research_run"]["strategies"] if x["type"] == kind)
    fields = choice["fields"]
    wire = {**conf, "data": {**conf["data"], "interval_ns": str(SECOND)},
            "strategy": {"type": kind, "symbol": "SPY", **{name: str(params[name])
                         if fields[name]["type"] == "integer_string" else params[name] for name in fields}}}
    assert compile_configuration("research_run", wire)["strategy"]["type"] == kind
