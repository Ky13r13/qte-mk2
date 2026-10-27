"""Owned timing bindings and actual-engine publication/decision boundaries."""
import gc
from dataclasses import replace

import pytest

import qte


def bar(start=0, end=10, price=100, symbol="SPY"):
    return qte.Bar(symbol, start, end, price, price + 1, price - 1, price, 1000)


def timed(observations, source="declared-timing-v1"):
    return qte.Dataset.from_observations(
        observations, source_id="synthetic-timing", timing_source_id=source)


@pytest.mark.parametrize("invalid", [True, False, 10.0, "10", None, 1 << 63, -(1 << 63) - 1])
def test_availability_requires_exact_int64(invalid):
    with pytest.raises(ValueError, match="signed 64-bit"):
        qte.BarObservation(bar(), invalid)


def test_envelopes_dataset_and_returned_bars_own_immutable_values():
    observation = qte.BarObservation(bar(), 12)
    saved = observation.bar
    with pytest.raises(AttributeError):
        observation.available_at_ns = 14
    with pytest.raises(AttributeError):
        observation.bar.close = 200
    values = [observation, qte.BarObservation(bar(10, 25), 30)]
    data = timed(values)
    values.clear()
    del observation
    gc.collect()
    assert saved.end_ns == 10 and saved.close == 100
    assert data.bar_count == 2 and data.start_ns == 0
    assert data.observation_end_ns == 25 and data.end_ns == 30
    assert data.interval_ns is None
    assert data.timing_policy == "explicit_availability_v1"
    assert data.timing_source_id == "declared-timing-v1"
    assert data.hash_algorithm == "fnv1a64-timed-v1"


def test_early_duplicate_and_out_of_order_publication_are_rejected():
    with pytest.raises(ValueError, match="bar end"):
        qte.BarObservation(bar(), 9)
    for available in (29, 30):
        with pytest.raises(ValueError, match="publication"):
            timed([qte.BarObservation(bar(), 30), qte.BarObservation(bar(10, 20), available)])
    with pytest.raises(ValueError):
        timed([])


@pytest.mark.parametrize("invalid", ["", " ", "x\n", "x" * 257])
def test_timing_source_is_required_and_bounded(invalid):
    with pytest.raises(ValueError, match="timing source"):
        timed([qte.BarObservation(bar(), 10)], invalid)


def test_delay_and_timing_source_change_hash_without_changing_legacy_contract():
    legacy = qte.Dataset.from_bars([bar()], 10, "synthetic-timing")
    zero = timed([qte.BarObservation(bar(), 10)])
    delayed = timed([qte.BarObservation(bar(), 11)])
    revision = timed([qte.BarObservation(bar(), 10)], "revision-2")
    assert len({legacy.hash, zero.hash, delayed.hash, revision.hash}) == 4
    assert legacy.timing_policy == "fixed_interval_v1"
    assert legacy.timing_source_id == ""
    assert legacy.hash_algorithm == "fnv1a64-v1"
    assert legacy.end_ns == legacy.observation_end_ns == 10
    assert legacy.interval_ns == 10
    with pytest.raises(ValueError, match="interval"):
        qte.Dataset.from_bars([bar(), bar(10, 25)], 10, "legacy-still-fixed")


class Recorder(qte.Strategy):
    def __init__(self):
        super().__init__()
        self.events = []
        self.saved_context = None

    def on_start(self, ctx):
        self.events.append(("start", ctx.now_ns, len(ctx.history("SPY"))))

    def on_bar(self, ctx, value):
        self.events.append(("bar", ctx.now_ns, value.end_ns, len(ctx.history("SPY"))))
        self.saved_context = ctx
        if len(ctx.history("SPY")) == 1:
            ctx.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 1))

    def on_order_update(self, ctx, update):
        assert ctx.now_ns == update.timestamp_ns

    def on_fill(self, ctx, fill):
        self.events.append(("fill", ctx.now_ns, fill.effective_ns))

    def on_end(self, ctx):
        self.events.append(("end", ctx.now_ns))


@pytest.mark.parametrize("release,fill_time", [(9, 10), (10, 10), (11, 20)])
def test_before_at_and_after_next_open_use_delivery_time(release, fill_time):
    observations = [qte.BarObservation(bar(0, 8), release),
                    qte.BarObservation(bar(10, 18, 110), 19),
                    qte.BarObservation(bar(20, 28, 120), 31)]
    data = timed(observations)
    strategy = Recorder()
    result = qte.BacktestEngine(qte.BacktestConfig(1000)).run(data, strategy)
    assert strategy.events[0] == ("start", 0, 0)
    assert [e for e in strategy.events if e[0] == "bar"] == [
        ("bar", release, 8, 1), ("bar", 19, 18, 2), ("bar", 31, 28, 3)]
    assert [(f.effective_ns, f.executed_price) for f in result.fills] == [
        (fill_time, 110 if fill_time == 10 else 120)]
    assert ("fill", fill_time, fill_time) in strategy.events
    assert strategy.events[-1] == ("end", 31)
    assert result.orders[0].submitted_ns == release
    assert result.manifest.dataset_hash == data.hash
    assert result.manifest.dataset_hash_algorithm == data.hash_algorithm
    assert result.manifest.timing_policy == data.timing_policy
    assert result.manifest.timing_source_id == data.timing_source_id
    assert result.manifest.observation_end_ns == 28
    assert result.manifest.replay_end_ns == 31
    assert result.manifest.opening_availability == "at_bar_start_v1"
    assert result.manifest.release_order == "strict_per_symbol_v1"
    with pytest.raises(RuntimeError, match="context"):
        _ = strategy.saved_context.now_ns


def test_final_delayed_submission_is_canceled_not_backfilled():
    strategy = Recorder()
    result = qte.BacktestEngine(qte.BacktestConfig(1000)).run(
        timed([qte.BarObservation(bar(), 50)]), strategy)
    assert not result.fills
    assert len(result.orders) == 1
    assert result.orders[0].submitted_ns == 50
    assert result.orders[0].status == qte.OrderStatus.CANCELED
    assert result.orders[0].cancellation_reason == qte.OrderCancellationReason.END_OF_DATA
    assert strategy.events[-1] == ("end", 50)


def test_pre_epoch_run_reports_actual_observation_and_replay_end():
    strategy = Recorder()
    result = qte.BacktestEngine(qte.BacktestConfig(1000)).run(
        timed([qte.BarObservation(bar(-30, -20), -10)]), strategy)
    assert strategy.events[0] == ("start", -30, 0)
    assert strategy.events[-1] == ("end", -10)
    assert result.manifest.observation_end_ns == -20
    assert result.manifest.replay_end_ns == -10


def test_replay_int64_boundaries_do_not_overflow_analytics_durations():
    class Passive(qte.Strategy):
        def on_bar(self, ctx, value):
            pass

    for end in (-1, 0):
        data = timed([qte.BarObservation(bar(-(1 << 63), end), end)])
        result = qte.BacktestEngine(qte.BacktestConfig(1000)).run(data, Passive())
        if end == -1:
            assert qte.analyze(result).total_return.value == 0
        else:
            with pytest.raises(ValueError, match="elapsed span"):
                qte.analyze(result)


def test_fixed_interval_research_comparison_does_not_guess_session_frequency():
    from qte.research import compare_moving_average
    from qte.strategies import MovingAverageConfig
    data = timed([qte.BarObservation(bar(), 10)])
    with pytest.raises(ValueError, match="fixed-interval"):
        compare_moving_average(in_sample=data, out_of_sample=data, symbol="SPY",
                               strategy_config=MovingAverageConfig(1, 2, 1))


def test_fixed_interval_experiments_reject_timing_profile_before_strategy_runs():
    from qte.experiments import run_experiment
    from test_experiments import candidate, config, windows
    values = list(windows())
    values[0] = replace(values[0], data=timed([qte.BarObservation(bar(), 10)]))
    with pytest.raises(ValueError, match="fixed-interval"):
        run_experiment([candidate()], tuple(values), config())
