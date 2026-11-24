"""One-position underlying proxy for S5, with the native QTE execution and cash ledger."""
import qte

from .adapters.schwab_research import MINUTE_NS
from .minute_replay import COSTS, dataset


class OversoldBounceStrategy(qte.Strategy):
    """Only consumes the frozen decision for the current clock. Orders fill through QTE."""

    def __init__(self, observations):
        super().__init__()
        self.decisions = {
            row["mark_ns"]: (index, row["standard_session"] and row["features"]["signal"],
                             row["features"]["exit_signal"])
            for index, row in enumerate(observations) if row["features"] is not None
        }
        self.entry_index = None

    def on_bar(self, ctx, bar):
        if bar.symbol != "SPY" or ctx.now_ns not in self.decisions:
            return
        index, entry, exit_signal = self.decisions[ctx.now_ns]
        position = ctx.position(bar.symbol)
        if not position or not position.quantity:
            if entry:
                ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.BUY, 1))
        elif exit_signal or index - self.entry_index >= 5:
            ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.SELL, 1))

    def on_fill(self, ctx, fill):
        self.entry_index = (self.decisions[fill.effective_ns][0]
                            if fill.side == qte.OrderSide.BUY else None)


def equity_proxy(minutes, rows, schedule):
    for row in rows:
        session = schedule.day(row["date"])
        bars = minutes.get(row["date"], ())
        if len(bars)*MINUTE_NS != session.close_ns-session.open_ns or row["features"] is None:
            return dict(status="unavailable", reason="incomplete_session_or_indicator_history")
    data = dataset("SPY", minutes)
    cash = 10_000
    cases = {}
    for case in ("base", "stress"):
        _, costs = COSTS[case]
        engine = qte.BacktestEngine(qte.BacktestConfig(cash, execution_costs=qte.ExecutionCosts(**costs),
                                    history_capacity=1, build_identity="s5-one-share-proxy-v1"))
        result = engine.run(data, OversoldBounceStrategy(rows))
        cases[case] = dict(costs_bps=costs, final_equity_usd=result.equity_curve[-1].equity,
                           pnl_usd=result.equity_curve[-1].equity-cash,
                           closed_trades=len(result.trades), open_trades=len(result.open_trades),
                           orders=[dict(id=o.id,status=o.status.name) for o in result.orders],
                           fills=[dict(time_ns=f.effective_ns,side=f.side.name,quantity=f.quantity,
                                       price=f.executed_price,commission=f.commission) for f in result.fills])
    return dict(status="simulated", initial_cash_usd=cash, shares_per_position=1, cases=cases)
