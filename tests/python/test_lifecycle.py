import pytest
import qte
from qte.strategies import MovingAverageConfig, MovingAverageRegimeStrategy

def data(prices):
    return qte.Dataset.from_bars([
        qte.Bar('SPY', i*3600000000000, (i+1)*3600000000000,
                p, p+1, p-1, p, 100) for i,p in enumerate(prices)
    ], interval_ns=3600000000000, source_id='lifecycle')

class Observed(MovingAverageRegimeStrategy):
    def __init__(self, quantity):
        super().__init__('SPY', MovingAverageConfig(1, 2, quantity))
        self.updates = []
    def on_order_update(self, ctx, update):
        self.updates.append(update)
        with pytest.raises(RuntimeError, match='forbidden'):
            ctx.cancel_order(update.order_id)
        super().on_order_update(ctx, update)

def test_rejection_recovers_on_next_bar_without_recursive_retry():
    s = Observed(100)
    r = qte.BacktestEngine(qte.BacktestConfig(1000)).run(data([100,101,102,103]), s)
    assert len(r.orders) == 3
    assert all(u.status == qte.OrderStatus.REJECTED for u in s.updates)
    assert len({u.timestamp_ns for u in s.updates}) == 3
    assert s._pending_order_id is None

def test_execution_cancellation_recovers_and_fills_later():
    s = Observed(5)
    r = qte.BacktestEngine(qte.BacktestConfig(600)).run(data([100,101,200,90,91,92]), s)
    assert any(u.status == qte.OrderStatus.CANCELED and 'execution_risk' in u.reason for u in s.updates)
    assert len(r.fills) == 1
    assert r.fills[0].executed_price == 92
    assert s._pending_order_id is None
    for order in r.orders:
        states = [u.status for u in s.updates if u.order_id == order.id]
        assert states in ([qte.OrderStatus.REJECTED],
                          [qte.OrderStatus.OPEN, qte.OrderStatus.CANCELED],
                          [qte.OrderStatus.OPEN, qte.OrderStatus.FILLED])

def test_explicit_cancel_and_end_of_data_notifications():
    class Cancel(qte.Strategy):
        def __init__(self):
            super().__init__(); self.updates=[]; self.done=False
        def on_bar(self, ctx, bar):
            if not self.done:
                order=ctx.submit_order(qte.market_order('SPY',qte.OrderSide.BUY,1))
                ctx.cancel_order(order.order_id); self.done=True
        def on_order_update(self,ctx,u): self.updates.append(u)
    s=Cancel()
    qte.BacktestEngine(qte.BacktestConfig(1000)).run(data([100,101]),s)
    assert [u.status for u in s.updates] == [qte.OrderStatus.OPEN,qte.OrderStatus.CANCELED]
    s=Observed(1)
    qte.BacktestEngine(qte.BacktestConfig(1000)).run(data([100,101]),s)
    assert s.updates[-1].reason == 'end_of_data'
    assert s._pending_order_id is None
