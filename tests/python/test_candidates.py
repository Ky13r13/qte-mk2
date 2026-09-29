from dataclasses import asdict, replace
import math

import pytest

import qte
import qte.strategies.candidates as candidates
from qte.strategies.candidates import CandidateConfig, CandidateStrategy, FAMILIES, _atr
from qte.synthetic import SyntheticConfig, generate_case


SECOND = 1_000_000_000
PATHS = {
    "trend": ([100, 100, 100, 101, 102, 102, 99, 98], (4, 7), (102, 98), 99),
    "breakout": ([100, 100, 100, 101, 102, 102, 99, 98], (4, 7), (102, 98), 99),
    "contraction": ([100, 100, 100, 100, 100, 100, 100, 101, 102, 102, 99, 98],
                    (8, 11), (102, 98), 99),
    "pullback": ([100, 103, 106, 109, 108, 110, 111, 100, 99], (6, 8), (111, 99), 90),
    "mean_reversion": ([100, 101, 99, 100, 100, 97, 99, 100, 101], (6, 7), (99, 100), 103),
    "rebound": ([100, 100, 100, 95, 96, 97, 98, 99], (5, 6), (97, 98), 104),
}


def config(kind="trend", **kwargs):
    return replace(CandidateConfig(
        kind=kind, fast_period=2, slow_period=4, breakout_period=3, exit_period=2,
        atr_period=2, contraction_window=3, contraction_memory=2, entry_z=1,
        min_atr_fraction=.01, max_atr_fraction=.05,
        stop_atr_multiple=100, max_holding_bars=50,
    ), **kwargs)


def bars(prices, width=.1):
    return [qte.Bar("SPY", i * SECOND, (i + 1) * SECOND,
                    price, price + width, price - width, price, 1000)
            for i, price in enumerate(prices)]


def run(prices, cfg, strategy=None, **engine_kwargs):
    data = qte.Dataset.from_bars(bars(prices), interval_ns=SECOND, source_id="synthetic-candidates")
    engine_kwargs.setdefault("history_capacity", cfg.history_required)
    result = qte.BacktestEngine(qte.BacktestConfig(100_000, **engine_kwargs)).run(
        data, strategy if strategy is not None else CandidateStrategy("SPY", cfg))
    return result


def fills(result):
    return [(fill.side, fill.quantity, fill.effective_ns, fill.executed_price)
            for fill in result.fills]


@pytest.mark.parametrize("kind", FAMILIES)
def test_each_family_has_exact_causal_entry_exit_and_allocation(kind):
    prices, indices, executed_prices, quantity = PATHS[kind]
    result = run(prices, config(kind))
    assert fills(result) == [
        (qte.OrderSide.BUY, quantity, indices[0] * SECOND, executed_prices[0]),
        (qte.OrderSide.SELL, quantity, indices[1] * SECOND, executed_prices[1]),
    ]
    assert result.positions[0].quantity == 0
    assert len(result.trades) == 1
    assert result.trades[0].net_realized_pnl == pytest.approx(
        quantity * (executed_prices[1] - executed_prices[0]))


@pytest.mark.parametrize("kind", FAMILIES)
def test_each_family_waits_for_complete_history_and_handles_flat_prices(kind):
    cfg = config(kind)
    assert not run([100] * (cfg.history_required - 1), cfg).orders
    assert not run([100] * (cfg.history_required * 3), cfg).orders
    # Zero ATR and zero standard deviation must not create division-by-zero signals.
    flat = qte.Dataset.from_bars(bars([100] * (cfg.history_required * 2), width=0),
                                interval_ns=SECOND, source_id="synthetic-zero-range")
    result = qte.BacktestEngine(qte.BacktestConfig(100_000)).run(flat, CandidateStrategy("SPY", cfg))
    assert not result.orders


@pytest.mark.parametrize("kind", FAMILIES)
def test_future_perturbations_do_not_change_earlier_fills(kind):
    prices, indices, _, _ = PATHS[kind]
    cutoff = indices[0] + 1
    changed = prices[:cutoff] + [200 + 10 * index for index in range(len(prices) - cutoff)]
    first = run(prices, config(kind))
    second = run(changed, config(kind))
    assert [fill for fill in fills(first) if fill[2] < cutoff * SECOND] == [
        fill for fill in fills(second) if fill[2] < cutoff * SECOND]
    assert first.fills[0].effective_ns < cutoff * SECOND


@pytest.mark.parametrize("kind", FAMILIES)
def test_same_instance_can_be_reused_without_state_leaking_between_runs(kind):
    prices = PATHS[kind][0]
    cfg = config(kind)
    strategy = CandidateStrategy("SPY", cfg)
    first, second = run(prices, cfg, strategy), run(prices, cfg, strategy)
    assert fills(first) == fills(second)
    assert [p.equity for p in first.equity_curve] == [p.equity for p in second.equity_curve]


@pytest.mark.parametrize("kind", FAMILIES)
def test_each_family_accounts_for_transaction_costs_through_engine(kind):
    prices = PATHS[kind][0]
    cfg = config(kind)
    baseline = run(prices, cfg)
    costly = run(prices, cfg, execution_costs=qte.ExecutionCosts(5, 10, 5))
    assert len(costly.fills) == 2
    assert all(fill.commission > 0 for fill in costly.fills)
    assert costly.trades[0].net_realized_pnl < baseline.trades[0].net_realized_pnl
    assert costly.equity_curve[-1].equity < baseline.equity_curve[-1].equity


class Observed(CandidateStrategy):
    def __init__(self, cfg, cancel_first=False):
        super().__init__("SPY", cfg)
        self.updates = []
        self.cancel_first = cancel_first

    def on_order_update(self, context, update):
        self.updates.append(update)
        super().on_order_update(context, update)

    def on_bar(self, context, bar):
        super().on_bar(context, bar)
        if self.cancel_first and self._pending_order_id is not None:
            context.cancel_order(self._pending_order_id)
            self.cancel_first = False


@pytest.mark.parametrize("kind", FAMILIES)
def test_rejections_clear_pending_without_duplicate_or_recursive_submissions(kind):
    cfg = config(kind)
    strategy = Observed(cfg)
    prices = PATHS[kind][0] * 2
    result = run(prices, cfg, strategy, risk_limits=qte.RiskLimits(max_order_quantity=1))
    assert len(result.orders) >= 2
    assert not result.fills
    assert all(update.status == qte.OrderStatus.REJECTED for update in strategy.updates)
    assert len({update.timestamp_ns for update in strategy.updates}) == len(strategy.updates)
    assert strategy._pending_order_id is None


@pytest.mark.parametrize("kind", FAMILIES)
def test_each_family_recovers_after_cancellation_and_can_fill_again(kind):
    cfg = config(kind)
    strategy = Observed(cfg, cancel_first=True)
    result = run(PATHS[kind][0] * 2, cfg, strategy)
    assert strategy.updates[1].status == qte.OrderStatus.CANCELED
    assert strategy.updates[1].reason == "user_requested"
    assert result.fills and result.fills[0].order_id != strategy.updates[1].order_id
    assert strategy._pending_order_id is None
    for order in result.orders:
        statuses = [update.status for update in strategy.updates if update.order_id == order.id]
        assert statuses in ([qte.OrderStatus.OPEN, qte.OrderStatus.CANCELED],
                            [qte.OrderStatus.OPEN, qte.OrderStatus.FILLED])


def test_atr_includes_gaps_and_uses_simple_mean_not_wilder_smoothing():
    history = [qte.Bar("SPY", 0, 1, 100, 101, 99, 100, 100),
               qte.Bar("SPY", 1, 2, 110, 112, 109, 111, 100),
               qte.Bar("SPY", 2, 3, 100, 102, 98, 101, 100)]
    # TRs = max(3,12,9) = 12; max(4,9,13) = 13.
    assert _atr(history, 2) == 12.5


def test_regime_gate_blocks_entries_and_forces_next_open_exit():
    seen = []
    def gate(timestamp_ns, family):
        seen.append((timestamp_ns, family))
        return timestamp_ns < 5 * SECOND
    cfg = config()
    strategy = CandidateStrategy("SPY", cfg, gate)
    result = run([100, 100, 100, 101, 102, 103, 104], cfg, strategy)
    assert [fill.effective_ns for fill in result.fills] == [4 * SECOND, 5 * SECOND]
    assert all(family == "trend" for _, family in seen)
    assert not run(PATHS["trend"][0], cfg, CandidateStrategy("SPY", cfg, lambda t, k: False)).orders
    with pytest.raises(TypeError, match="return bool"):
        run(PATHS["trend"][0], cfg, CandidateStrategy("SPY", cfg, lambda t, k: "yes"))


def test_holding_limit_counts_from_actual_fill_not_signal():
    cfg = config(max_holding_bars=1)
    result = run([100, 100, 100, 101, 102, 103], cfg)
    assert [fill.effective_ns for fill in result.fills] == [4 * SECOND, 5 * SECOND]


def test_close_stop_uses_actual_fill_price_and_next_open_not_signal_price_or_intrabar_low():
    cfg = config(stop_atr_multiple=2)
    # Signal close=101, ATR=(.2+1.1)/2=.65; fill=110 so close stop=108.7.
    # The same bar closes at 108, requiring exit at the next actual open, 107.
    history = bars([100, 100, 100, 101, 108, 107])
    history[4] = qte.Bar("SPY", 4 * SECOND, 5 * SECOND, 110, 110.1, 107.9, 108, 1000)
    data = qte.Dataset.from_bars(history, interval_ns=SECOND, source_id="synthetic-stop-gap")
    result = qte.BacktestEngine(qte.BacktestConfig(100_000)).run(data, CandidateStrategy("SPY", cfg))
    assert [(fill.side, fill.executed_price) for fill in result.fills] == [
        (qte.OrderSide.BUY, 110), (qte.OrderSide.SELL, 107)]
    # An intrabar low below that stop with a close above it must not trigger a stop.
    history[4] = qte.Bar("SPY", 4 * SECOND, 5 * SECOND, 110, 112, 90, 111, 1000)
    history[5] = qte.Bar("SPY", 5 * SECOND, 6 * SECOND, 112, 113, 111, 112, 1000)
    data = qte.Dataset.from_bars(history, interval_ns=SECOND, source_id="synthetic-no-intrabar-stop")
    result = qte.BacktestEngine(qte.BacktestConfig(100_000)).run(data, CandidateStrategy("SPY", cfg))
    assert len(result.fills) == 1


def test_execution_gap_cancellation_recovers():
    cfg = config(allocation_fraction=.9)
    strategy = Observed(cfg)
    history = bars([100, 100, 100, 101, 102, 103, 104])
    history[4] = qte.Bar("SPY", 4 * SECOND, 5 * SECOND, 200, 201, 101, 102, 1000)
    data = qte.Dataset.from_bars(history, interval_ns=SECOND, source_id="synthetic-risk-gap")
    result = qte.BacktestEngine(qte.BacktestConfig(100_000)).run(data, strategy)
    assert any(update.status == qte.OrderStatus.CANCELED and "execution_risk" in update.reason
               for update in strategy.updates)
    assert result.fills[0].effective_ns == 5 * SECOND
    assert strategy._pending_order_id is None


def test_insufficient_history_capacity_fails_explicitly():
    with pytest.raises(ValueError, match="history_capacity"):
        run(PATHS["contraction"][0], config("contraction"), history_capacity=3)


@pytest.mark.parametrize("changes", [
    {"kind": "unknown"}, {"fast_period": True}, {"slow_period": 1},
    {"atr_period": 0}, {"contraction_memory": -1}, {"allocation_fraction": 0},
    {"allocation_fraction": 1.1}, {"entry_z": math.nan}, {"stop_atr_multiple": math.inf},
    {"entry_z": 10 ** 1000}, {"max_atr_fraction": True},
    {"shock_fraction": 1}, {"contraction_quantile": 1.1},
    {"min_atr_fraction": .2, "max_atr_fraction": .1},
])
def test_invalid_parameters_are_rejected(changes):
    with pytest.raises(ValueError):
        config(**changes)


def test_config_serializes_round_trips_and_sizing_never_submits_zero_shares():
    cfg = config(allocation_fraction=.00001)
    assert CandidateConfig(**asdict(cfg)) == cfg
    assert not run(PATHS["trend"][0], cfg).orders


def reference_contraction_signals(history, cfg):
    """Original unconditional percentile calculation, kept as a test oracle."""
    price = history[-1].close
    entry = price > max(bar.high for bar in history[-cfg.breakout_period - 1:-1])
    exit_ = price < min(bar.low for bar in history[-cfg.exit_period - 1:-1])
    contracted = False
    for end in range(len(history) - cfg.contraction_memory, len(history)):
        value = _atr(history, cfg.atr_period, end)
        distribution = sorted(_atr(history, cfg.atr_period, prior_end)
                              for prior_end in range(end - cfg.contraction_window, end))
        threshold = distribution[math.ceil(cfg.contraction_quantile * len(distribution)) - 1]
        if value > 0 and value <= threshold:
            contracted = True
            break
    return entry and contracted, exit_, _atr(history, cfg.atr_period)


@pytest.mark.parametrize("regime", ["contraction_breakout", "gap_shock", "low_vol_range"])
@pytest.mark.parametrize("seed", [0, 17, 42, 817])
def test_short_circuit_contraction_matches_original_formula_for_every_seeded_prefix(regime, seed):
    cfg = config("contraction")
    case = generate_case(SyntheticConfig(regime, seed, bars=128))
    for end in range(cfg.history_required, len(case.bars) + 1):
        history = case.bars[max(0, end - cfg.history_required):end]
        assert candidates._signals(history, cfg) == reference_contraction_signals(history, cfg)


@pytest.mark.parametrize("quantile", [.20, .5, 1.0])
@pytest.mark.parametrize("width", [.1, .10000000000001])
def test_contraction_ties_and_near_ties_preserve_prior_only_ranking(quantile, width):
    cfg = config("contraction", contraction_quantile=quantile)
    history = bars([100] * (cfg.history_required - 1) + [101], width=width)
    signal = candidates._signals(history, cfg)
    assert signal == reference_contraction_signals(history, cfg)
    assert signal[0] is True


def test_contraction_without_breakout_only_computes_current_stop_atr(monkeypatch):
    cfg = config("contraction")
    original = candidates._atr
    calls = []

    def counted_atr(history, period, end=None):
        calls.append(end)
        return original(history, period, end)

    monkeypatch.setattr(candidates, "_atr", counted_atr)
    entry, exit_, atr = candidates._signals(bars([100] * cfg.history_required), cfg)
    assert not entry and not exit_ and atr > 0
    assert calls == [None]


def test_default_contraction_engine_fills_and_equity_match_reference_before_optimization(monkeypatch):
    cfg = CandidateConfig(kind="contraction")
    case = generate_case(SyntheticConfig("contraction_breakout", 817, bars=512))

    def engine_run():
        return qte.BacktestEngine(qte.BacktestConfig(100_000, history_capacity=256)).run(
            case.data, CandidateStrategy("QTE_SYNTH", cfg))

    optimized = engine_run()
    monkeypatch.setattr(candidates, "_signals", reference_contraction_signals)
    original = engine_run()
    assert len(optimized.fills) == 6
    assert fills(optimized) == fills(original)
    assert [(p.timestamp_ns, p.equity, p.gross_exposure) for p in optimized.equity_curve] == [
        (p.timestamp_ns, p.equity, p.gross_exposure) for p in original.equity_curve]
