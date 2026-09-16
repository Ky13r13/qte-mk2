from __future__ import annotations

import gc

import pytest

import qte


HOUR_NS = 3_600_000_000_000


def bars() -> list[qte.Bar]:
    prices = (100.0, 110.0, 120.0)
    return [
        qte.Bar(
            "SPY",
            index * HOUR_NS,
            (index + 1) * HOUR_NS,
            price,
            price + 1.0,
            price - 1.0,
            price,
            1_000.0,
        )
        for index, price in enumerate(prices)
    ]


def dataset() -> qte.Dataset:
    return qte.Dataset.from_bars(
        bars(), interval_ns=HOUR_NS, source_id="python-fixture"
    )


def engine() -> qte.BacktestEngine:
    return qte.BacktestEngine(
        qte.BacktestConfig(1_000.0, random_seed=42, build_identity="python-test")
    )


class RoundTrip(qte.Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.bought = False
        self.history_sizes: list[int] = []
        self.saved_bar = None
        self.saved_context = None

    def on_bar(self, context, bar) -> None:
        self.saved_bar = bar
        self.saved_context = context
        self.history_sizes.append(len(context.history("SPY")))
        if not self.bought:
            context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 5))
            self.bought = True

    def on_fill(self, context, fill) -> None:
        if fill.side == qte.OrderSide.BUY:
            context.submit_order(qte.market_order("SPY", qte.OrderSide.SELL, 5))


def test_python_strategy_matches_reference_and_values_outlive_engine() -> None:
    strategy = RoundTrip()
    result = engine().run(dataset(), strategy)
    assert [fill.executed_price for fill in result.fills] == [110.0, 120.0]
    assert result.equity_curve[-1].equity == 1_050.0
    assert strategy.history_sizes == [1, 2, 3]
    assert result.manifest.source_id == "python-fixture"
    assert result.manifest.random_seed == 42
    saved_bar = strategy.saved_bar
    del result
    gc.collect()
    assert saved_bar.symbol == "SPY"
    assert saved_bar.end_ns == 3 * HOUR_NS
    with pytest.raises(RuntimeError, match="context"):
        strategy.saved_context.portfolio


def test_nanoseconds_round_trip_exactly_and_input_is_copied() -> None:
    exact = 1_234_567_890_123_456_789
    bar = qte.Bar("SPY", exact, exact + HOUR_NS, 10.0, 11.0, 9.0, 10.0, 0.0)
    data = qte.Dataset.from_bars(
        [bar], interval_ns=HOUR_NS, source_id="nanoseconds"
    )
    assert bar.start_ns == exact
    original_hash = data.hash
    source = [bar]
    source.clear()
    assert data.hash == original_hash
    assert data.bar_count == 1


def test_invalid_configuration_and_callback_exception_propagate() -> None:
    with pytest.raises((TypeError, ValueError)):
        qte.BacktestConfig(-1.0)
    with pytest.raises(TypeError):
        qte.BacktestConfig(1_000.0, unknown_key=True)

    class Broken(qte.Strategy):
        def on_bar(self, context, bar) -> None:
            raise LookupError("original-python-failure")

    with pytest.raises(LookupError, match="original-python-failure"):
        engine().run(dataset(), Broken())


def test_same_inputs_and_fresh_strategies_repeat_exactly() -> None:
    first = engine().run(dataset(), RoundTrip())
    second = engine().run(dataset(), RoundTrip())
    assert [point.equity for point in first.equity_curve] == [
        point.equity for point in second.equity_curve
    ]
    assert [fill.id for fill in first.fills] == [fill.id for fill in second.fills]
    assert first.manifest.dataset_hash == second.manifest.dataset_hash


def test_reentrant_run_is_rejected() -> None:
    runner = engine()
    data = dataset()

    class Reentrant(qte.Strategy):
        def on_bar(self, context, bar) -> None:
            runner.run(data, RoundTrip())

    with pytest.raises(RuntimeError, match="not reentrant"):
        runner.run(data, Reentrant())


def test_metrics_binding_returns_owned_report() -> None:
    report = qte.analyze(engine().run(dataset(), RoundTrip()))
    assert report.total_return.value == pytest.approx(0.05)
    assert report.trade_count == 1
    assert report.expectancy.value == pytest.approx(50.0)
