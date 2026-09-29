from dataclasses import asdict, replace
import json

import pytest

import qte
from qte.regimes import DealerObservation, FEATURES, MacroObservation, MacroRegimeFilter, RegimeConfig
from qte.strategies.candidates import CandidateConfig, FAMILIES
from qte.strategies.router import MacroRouterStrategy, RouterConfig


S = 1_000_000_000


def candidate(kind="trend", **kwargs):
    return replace(CandidateConfig(
        kind=kind, fast_period=2, slow_period=3, atr_period=1, breakout_period=2,
        exit_period=2, contraction_window=2, contraction_memory=1,
        max_holding_bars=100, stop_atr_multiple=100, entry_z=1,
        min_atr_fraction=.01, max_atr_fraction=.10), **kwargs)


def release(at=0, *, score=1, vol=12):
    return tuple(MacroObservation(feature, value, at, at,
                                  f"synthetic:{feature}", f"v{at}")
                 for feature, value in zip(FEATURES, (3 * score, 0, 0, vol)))


def gate(*events, **kwargs):
    return MacroRegimeFilter(tuple(item for event in events for item in event), **kwargs)


def dataset(prices):
    return qte.Dataset.from_bars(
        [qte.Bar("SPY", i * S, (i + 1) * S, price, price + .1, price - .1, price, 1000)
         for i, price in enumerate(prices)], interval_ns=S, source_id="synthetic-router")


def run(prices, router, **kwargs):
    kwargs.setdefault("history_capacity", router.history_required)
    return qte.BacktestEngine(qte.BacktestConfig(100_000, **kwargs)).run(dataset(prices), router)


def actual_fills(result):
    return [(fill.side, fill.quantity, fill.effective_ns, fill.executed_price) for fill in result.fills]


def test_real_engine_exits_before_family_switch_with_exact_one_book_accounting():
    router = MacroRouterStrategy("SPY", [candidate(), candidate("mean_reversion")],
                                 gate(release(), release(4 * S, score=0)))
    result = run([100, 101, 102, 103, 100, 97, 99, 101, 102, 101], router)
    assert actual_fills(result) == [
        (qte.OrderSide.BUY, 98, 3 * S, 103),
        (qte.OrderSide.SELL, 98, 4 * S, 100),
        (qte.OrderSide.BUY, 99, 5 * S, 97),
        (qte.OrderSide.SELL, 99, 8 * S, 102),
    ]
    assert len(result.trades) == 2 and result.positions[0].quantity == 0
    assert result.equity_curve[-1].equity == 100_201
    assert router.active_family == "mean_reversion" and router.pending_order_id is None
    assert [(event.timestamp_ns, event.active_family, event.phase) for event in router.route_events] == [
        (S, "trend", "active"), (4 * S, "trend", "retiring"),
        (5 * S, "mean_reversion", "active")]
    json.dumps([asdict(event) for event in router.route_events])


def test_risk_off_forces_exit_and_never_buys_from_fill_callback():
    router = MacroRouterStrategy("SPY", [candidate()], gate(release(), release(4 * S, score=-1)))
    result = run([100, 101, 102, 103, 100, 105, 106], router)
    assert [(fill.side, fill.effective_ns) for fill in result.fills] == [
        (qte.OrderSide.BUY, 3 * S), (qte.OrderSide.SELL, 4 * S)]
    assert router.active_family is None
    assert router.route_events[-1].phase == "cash"


@pytest.mark.parametrize("filter_", [MacroRegimeFilter(()), gate(release(score=-1)), gate(release(vol=40))])
def test_unknown_risk_off_and_extreme_are_cash_even_when_prices_trend(filter_):
    router = MacroRouterStrategy("SPY", [candidate(kind) for kind in FAMILIES], filter_)
    result = run(list(range(100, 120)), router)
    assert not result.orders and not result.fills
    assert router.active_family is None


def test_delayed_release_never_enters_early_and_staleness_forces_exit():
    delayed = tuple(replace(item, available_ns=4 * S) for item in release())
    router = MacroRouterStrategy("SPY", [candidate()], gate(delayed))
    result = run([100, 101, 102, 103, 104, 105], router)
    assert result.fills[0].effective_ns == 4 * S
    assert router.route_events[0].phase == "cash"
    assert router.route_events[1].timestamp_ns == 4 * S
    stale = MacroRouterStrategy("SPY", [candidate()], gate(release(), config=RegimeConfig(
        max_macro_age_ns=3 * S, max_volatility_age_ns=3 * S)))
    result = run([100, 101, 102, 103, 104, 105], stale)
    assert [fill.effective_ns for fill in result.fills] == [3 * S, 4 * S]
    assert any("stale:growth_z" in event.reasons for event in stale.route_events)


@pytest.mark.parametrize("score,vol,expected", [
    (1, 12, "contraction"), (1, 20, "pullback"), (1, 30, "trend"),
    (0, 12, "mean_reversion"), (0, 20, "mean_reversion"), (0, 30, None),
])
def test_priority_table_picks_family_without_scanning_entry_signals(score, vol, expected):
    router = MacroRouterStrategy("SPY", [candidate(kind) for kind in FAMILIES], gate(release(score=score, vol=vol)))
    result = run([100] * 15, router)
    assert router.active_family == expected
    assert not result.orders


@pytest.mark.parametrize("score,gamma,expected", [(1, 100, "trend"), (0, -100, None)])
def test_dealer_permissions_can_remove_priorities_but_never_override_macro(score, gamma, expected):
    dealer = DealerObservation(gamma, 0, 0, "synthetic-dealer", "v1", "script", "SPX", "observed")
    filter_ = gate(release(score=score), config=RegimeConfig(dealer_mode="required"), dealer_observations=[dealer])
    router = MacroRouterStrategy("SPY", [candidate(kind) for kind in FAMILIES], filter_)
    run([100] * 10, router)
    assert router.active_family == expected


class ObservedRouter(MacroRouterStrategy):
    def __init__(self, *args, cancel_first=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.updates = []
        self.cancel_first = cancel_first

    def on_order_update(self, ctx, update):
        self.updates.append(update)
        super().on_order_update(ctx, update)

    def on_bar(self, ctx, bar):
        super().on_bar(ctx, bar)
        if self.cancel_first and self.pending_order_id is not None:
            ctx.cancel_order(self.pending_order_id)
            self.cancel_first = False


def test_rejections_recover_pending_state_without_duplicate_submissions():
    router = ObservedRouter("SPY", [candidate()], gate(release()))
    result = run([100, 101, 102, 103, 104, 105, 106], router,
                 risk_limits=qte.RiskLimits(max_order_quantity=1))
    assert len(result.orders) == 5 and not result.fills
    assert all(update.status == qte.OrderStatus.REJECTED for update in router.updates)
    assert len({update.timestamp_ns for update in router.updates}) == len(router.updates)
    assert router.pending_order_id is None


def test_cancellation_recovers_pending_state_and_retries_next_bar():
    router = ObservedRouter("SPY", [candidate()], gate(release()), cancel_first=True)
    result = run([100, 101, 102, 103, 104, 105, 106], router)
    assert result.orders[0].status == qte.OrderStatus.CANCELED
    assert result.fills[0].order_id == 2 and result.fills[0].effective_ns == 4 * S
    assert len(result.fills) == 1  # No pyramiding while the held trend persists.
    assert router.pending_order_id is None


def test_retirement_stays_latched_if_regime_reverts_after_exit_cancellation():
    class CancelFirstExit(ObservedRouter):
        def on_bar(self, ctx, bar):
            super().on_bar(ctx, bar)
            if bar.end_ns == 4 * S and self.pending_order_id is not None:
                ctx.cancel_order(self.pending_order_id)

    router = CancelFirstExit("SPY", [candidate(), candidate("mean_reversion")],
                             gate(release(), release(4 * S, score=0), release(5 * S)))
    result = run([100, 101, 102, 103, 104, 105, 106, 107], router)
    assert [(fill.side, fill.effective_ns) for fill in result.fills] == [
        (qte.OrderSide.BUY, 3 * S), (qte.OrderSide.SELL, 5 * S),
        (qte.OrderSide.BUY, 6 * S)]
    assert result.orders[1].status == qte.OrderStatus.CANCELED
    assert any(event.timestamp_ns == 5 * S and event.phase == "retiring"
               and event.target_family == event.active_family == "trend"
               for event in router.route_events)
    assert router.route_events[-1].timestamp_ns == 6 * S
    assert router.route_events[-1].phase == "active"


def test_rejected_gap_entry_recovers_through_public_lifecycle_callbacks():
    router = ObservedRouter("SPY", [candidate(allocation_fraction=.9)], gate(release()))
    bars = [qte.Bar("SPY", i * S, (i + 1) * S, p, p + .1, p - .1, p, 1000)
            for i, p in enumerate((100, 101, 102, 103, 104, 105))]
    bars[3] = qte.Bar("SPY", 3 * S, 4 * S, 200, 201, 102, 103, 1000)
    data = qte.Dataset.from_bars(bars, interval_ns=S, source_id="synthetic-router-gap")
    result = qte.BacktestEngine(qte.BacktestConfig(100_000)).run(data, router)
    assert any(update.status == qte.OrderStatus.CANCELED and "execution_risk" in update.reason
               for update in router.updates)
    assert result.fills[0].effective_ns == 4 * S and router.pending_order_id is None


def test_same_router_instance_resets_children_and_route_audit_between_runs():
    router = MacroRouterStrategy("SPY", [candidate(), candidate("mean_reversion")],
                                 gate(release(), release(4 * S, score=0)))
    prices = [100, 101, 102, 103, 100, 97, 99, 101, 102, 101]
    first = run(prices, router)
    events = router.route_events
    second = run(prices, router)
    assert actual_fills(first) == actual_fills(second)
    assert [point.equity for point in first.equity_curve] == [point.equity for point in second.equity_curve]
    assert router.route_events == events


def test_future_macro_release_or_prices_do_not_change_earlier_fills():
    first = MacroRouterStrategy("SPY", [candidate()], gate(release(), release(5 * S, score=-1)))
    second = MacroRouterStrategy("SPY", [candidate()], gate(release(), release(5 * S, score=1)))
    a = run([100, 101, 102, 103, 104, 1, 2], first)
    b = run([100, 101, 102, 103, 104, 200, 300], second)
    assert [row for row in actual_fills(a) if row[2] < 5 * S] == [
        row for row in actual_fills(b) if row[2] < 5 * S]


def test_insufficient_history_capacity_is_explicit_even_after_family_switches():
    router = MacroRouterStrategy("SPY", [candidate(), candidate("contraction")], gate(release()))
    with pytest.raises(ValueError, match="history_capacity"):
        run(list(range(100, 120)), router, history_capacity=2)


@pytest.mark.parametrize("priorities", ["trend", ("trend", "trend"), ("unknown",), (True,)])
def test_invalid_priority_config_is_rejected(priorities):
    with pytest.raises(ValueError):
        RouterConfig(risk_on_low=priorities)


def test_config_validation_and_immutable_priority_snapshot():
    priorities = ["trend"]
    config = RouterConfig(risk_on_low=priorities)
    priorities.clear()
    assert config.risk_on_low == ("trend",)
    with pytest.raises(ValueError, match="at most one"):
        MacroRouterStrategy("SPY", [candidate(), candidate()], gate(release()))
    with pytest.raises(ValueError, match="at least one"):
        MacroRouterStrategy("SPY", [], gate(release()))
    with pytest.raises(TypeError, match="MacroRegimeFilter"):
        MacroRouterStrategy("SPY", [candidate()], lambda t, k: True)


def test_empty_priority_is_explicit_cash_not_fallback_to_any_permitted_family():
    router = MacroRouterStrategy("SPY", [candidate()], gate(release()), RouterConfig(risk_on_low=()))
    assert not run(list(range(100, 115)), router).orders
    assert router.active_family is None
