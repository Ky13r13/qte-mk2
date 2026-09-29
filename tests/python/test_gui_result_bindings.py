from __future__ import annotations

import gc

import pytest

import qte
from qte import _core as core


INTERVAL = 3_600_000_000_000
START = 9_007_199_254_740_993


def _dataset():
    prices = [100.0, 101.0, 103.0, 104.0, 105.0]
    bars = [qte.Bar("SPY", START + i * INTERVAL, START + (i + 1) * INTERVAL,
                    price, price + 1, price - 1, price, 1_000.0)
            for i, price in enumerate(prices)]
    return qte.Dataset.from_bars(bars, interval_ns=INTERVAL, source_id="g3b-bindings")


class OwnedResults(qte.Strategy):
    def __init__(self):
        super().__init__()
        self.started = False

    def on_bar(self, context, bar):
        if self.started:
            return
        self.started = True
        context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 5))
        context.submit_order(qte.limit_order("SPY", qte.OrderSide.BUY, 1, 1.0))
        context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 2**53 + 1))
        context.cancel_order(2**64 - 1)

    def on_fill(self, context, fill):
        if fill.side == qte.OrderSide.BUY and fill.quantity == 5:
            context.submit_order(qte.market_order("SPY", qte.OrderSide.SELL, 5))
        elif fill.side == qte.OrderSide.SELL:
            context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 2))


def _results():
    limits = qte.RiskLimits(max_order_quantity=10)
    config = qte.BacktestConfig(10_000.0, risk_limits=limits,
                                random_seed=2**64 - 1, build_identity="g3b-test")
    return qte.BacktestEngine(config).run(_dataset(), OwnedResults())


def test_enums_and_order_request_are_read_only_values():
    assert qte.OrderType is core.OrderType
    assert qte.TimeInForce is core.TimeInForce
    assert qte.OrderRejectionReason is core.OrderRejectionReason
    assert qte.OrderCancellationReason is core.OrderCancellationReason
    assert qte.OrderEventKind is core.OrderEventKind
    assert qte.PositionDirection is core.PositionDirection
    assert qte.TradeOutcome is core.TradeOutcome
    request = qte.stop_limit_order("SPY", qte.OrderSide.SELL, 7, 99.0, 98.0)
    assert request.symbol == "SPY"
    assert request.side == qte.OrderSide.SELL
    assert request.quantity == 7
    assert request.type == core.OrderType.STOP_LIMIT
    assert request.stop_price == 99.0 and request.limit_price == 98.0
    assert request.time_in_force == core.TimeInForce.GOOD_TIL_CANCELED
    with pytest.raises(AttributeError):
        request.quantity = 8
    with pytest.raises(TypeError):
        core.OrderRequest()
    assert core.OrderRejectionReason.RISK is not None
    assert core.OrderCancellationReason.END_OF_DATA is not None
    assert core.OrderEventKind.CANCEL_UNKNOWN is not None
    assert core.PositionDirection.LONG is not None
    assert core.TradeOutcome.BREAKEVEN is not None


def test_owned_result_fields_and_exact_integers():
    result = _results()
    assert result.manifest.random_seed == 2**64 - 1
    assert result.equity_curve[0].timestamp_ns == START
    assert type(result.equity_curve[0].timestamp_ns) is int
    assert type(result.equity_curve[0].sequence) is int

    filled = next(order for order in result.orders if order.status == qte.OrderStatus.FILLED)
    rejected = next(order for order in result.orders if order.status == qte.OrderStatus.REJECTED)
    canceled = next(order for order in result.orders if order.status == qte.OrderStatus.CANCELED)
    assert filled.symbol == "SPY" and filled.side == qte.OrderSide.BUY
    assert filled.type == core.OrderType.MARKET
    assert filled.limit_price is None and filled.stop_price is None
    assert filled.time_in_force == core.TimeInForce.GOOD_TIL_CANCELED
    assert filled.filled_quantity == filled.quantity and filled.remaining_quantity == 0
    assert filled.rejection_reason is None and filled.cancellation_reason is None
    assert type(filled.submitted_ns) is int and filled.submitted_ns == START + INTERVAL
    assert type(filled.submission_sequence) is int
    assert type(filled.eligible_after_sequence) is int
    assert rejected.rejection_reason == core.OrderRejectionReason.RISK
    assert rejected.quantity == 2**53 + 1
    assert rejected.filled_quantity == 0
    assert canceled.cancellation_reason == core.OrderCancellationReason.END_OF_DATA
    assert canceled.limit_price == 1.0

    fill = result.fills[0]
    assert type(fill.id) is int and type(fill.order_id) is int
    assert type(fill.effective_sequence) is int
    assert fill.effective_ns == START + INTERVAL
    assert fill.reference_open == 101.0

    kinds = {event.kind for event in result.order_events}
    assert {core.OrderEventKind.ACCEPTED, core.OrderEventKind.REJECTED,
            core.OrderEventKind.CANCELED, core.OrderEventKind.CANCEL_UNKNOWN,
            core.OrderEventKind.FILLED} <= kinds
    assert all(type(event.sequence) is int and event.timestamp_ns > 2**53 for event in result.order_events)
    assert any(event.order_id is not None and type(event.order_id) is int for event in result.order_events)
    unknown = next(event for event in result.order_events if event.kind == core.OrderEventKind.CANCEL_UNKNOWN)
    assert unknown.order_id == 2**64 - 1


def test_closed_open_trades_positions_and_optionals():
    result = _results()
    closed = result.trades[0]
    opened = result.open_trades[0]
    assert closed.direction == core.PositionDirection.LONG
    assert closed.is_closed and closed.outcome == core.TradeOutcome.WINNING
    assert type(closed.opening_fill_id) is int and type(closed.closing_fill_id) is int
    assert closed.opening_fill_id == 1 and closed.closing_fill_id == 2
    assert closed.opened_ns == START + INTERVAL and closed.closed_ns == START + 2 * INTERVAL
    assert type(closed.opening_sequence) is int and type(closed.closing_sequence) is int
    assert closed.opened_quantity == closed.closed_quantity == 5
    assert closed.remaining_quantity == 0
    assert closed.net_realized_pnl == pytest.approx(closed.realized_gross_pnl - closed.allocated_commissions)

    assert not opened.is_closed and opened.outcome is None
    assert opened.closing_fill_id is None and opened.closed_ns is None and opened.closing_sequence is None
    assert opened.opened_quantity == opened.remaining_quantity == 2 and opened.closed_quantity == 0
    position = result.positions[0]
    assert position.symbol == "SPY" and position.quantity == 2
    assert position.mark_price is not None and position.mark_ns is not None
    assert position.mark_ns == START + 5 * INTERVAL
    assert type(position.mark_sequence) is int


def test_result_children_are_owned_read_only_and_outlive_results():
    result = _results()
    copied_orders = result.orders
    copied_orders.clear()
    assert len(result.orders) == 5
    saved = (result.equity_curve[0], result.orders[0], result.order_events[0], result.fills[0],
             result.trades[0], result.open_trades[0], result.positions[0])
    manifest = result.manifest
    expected = (saved[0].sequence, saved[1].id, saved[2].sequence, saved[3].effective_sequence,
                saved[4].opening_fill_id, saved[5].opening_fill_id, saved[6].quantity)
    del result
    gc.collect()
    assert manifest.random_seed == 2**64 - 1
    assert (saved[0].sequence, saved[1].id, saved[2].sequence, saved[3].effective_sequence,
            saved[4].opening_fill_id, saved[5].opening_fill_id, saved[6].quantity) == expected
    for value, attribute in ((saved[0], "sequence"), (saved[1], "detail"), (saved[2], "kind"),
                             (saved[3], "commission"), (saved[4], "opened_quantity"),
                             (saved[6], "quantity")):
        with pytest.raises(AttributeError):
            setattr(value, attribute, 0)
    for cls in (core.EquityPoint, core.OrderSnapshot, core.OrderEvent, core.Fill,
                core.TradeEpisode, core.PositionSnapshot):
        with pytest.raises(TypeError):
            cls()


def test_sampled_equity_retains_latest_source_sequence_across_carry():
    result = _results()
    grid = [START, START + INTERVAL // 2, START + INTERVAL // 2 + 1, START + 5 * INTERVAL]
    sampled = qte.sample_equity(result, qte.SamplingConfig(grid, max_staleness_ns=INTERVAL))
    assert [point.timestamp_ns for point in sampled] == grid
    assert [point.sequence for point in sampled[1:3]] == [3, 3]
