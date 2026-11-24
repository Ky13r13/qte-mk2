"""Run short-hold, bar-derived trading intent through QTE's native ETF engine."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import math

import qte
from .adapters.schwab_research import MINUTE_NS
from .strategies.minute_signals import direction

CASH = 100_000
SLEEVE = 10_000
HOLD = 5
LIMIT = 5
COSTS = {
    'base': (0, dict(commission_bps=1, spread_bps=2, slippage_bps=1)),
    'stress': (0, dict(commission_bps=2, spread_bps=6, slippage_bps=4)),
    'stress_delayed': (1, dict(commission_bps=2, spread_bps=6, slippage_bps=4)),
}


@dataclass(frozen=True)
class Intent:
    day: str
    decision_ns: int
    entry_ns: int
    exit_ns: int
    sign: int
    quantity: int
    entry_open: float
    exit_open: float
    regime: str
    annual_vol_proxy: float


def plan(sessions, minutes, policy, delay, *, signal=direction):
    """Generate intents without consulting entry/exit prices for any decision."""
    intents = []
    skips = Counter()
    for day, bars in sessions.items():
        busy = count = 0
        for minute in minutes[day]:
            sign = signal(minute, policy)
            if not sign:
                continue
            i = minute.index
            entry = i+1+delay
            exit_ = entry+HOLD
            if i < busy or count == LIMIT or exit_ >= len(bars):
                skips['overlap' if i < busy else 'max_entries' if count == LIMIT else 'late'] += 1
                continue
            quantity = math.floor(SLEEVE/minute.close)
            if not quantity:
                skips['zero_quantity'] += 1
                continue
            regime = ('shock_up' if minute.shock_direction > 0 else 'shock_down' if minute.shock_direction < 0
                      else 'expanded_range' if minute.high_range else 'ordinary_range')
            intents.append(Intent(day, bars[i].end_ns, bars[entry].start_ns, bars[exit_].start_ns,
                                  sign, quantity, bars[entry].open, bars[exit_].open, regime, minute.annual_vol_proxy))
            busy, count = exit_, count+1
    return intents, dict(skips)


class TimedOrders(qte.Strategy):
    def __init__(self, intents):
        super().__init__()
        self.events = {}
        for intent in intents:
            if intent.entry_ns in self.events or intent.exit_ns in self.events:
                raise ValueError('Duplicate order clock')
            self.events[intent.entry_ns] = intent.sign, intent.quantity
            self.events[intent.exit_ns] = -intent.sign, intent.quantity

    def on_bar(self, ctx, bar):
        event = self.events.get(ctx.now_ns)
        if event is not None:
            sign, quantity = event
            side = qte.OrderSide.BUY if sign > 0 else qte.OrderSide.SELL
            ctx.submit_order(qte.market_order(bar.symbol, side, quantity))


def dataset(symbol, sessions):
    return qte.Dataset.from_bars([b for rows in sessions.values() for b in rows], interval_ns=MINUTE_NS,
                                 tick_size=.01, source_id=f'schwab_retail_minute_{symbol}')


def replay(data, sessions, minutes, policy, cost, *, signal=direction):
    delay, costs = COSTS[cost]
    intents, skips = plan(sessions, minutes, policy, delay, signal=signal)
    result = qte.BacktestEngine(qte.BacktestConfig(CASH, execution_costs=qte.ExecutionCosts(**costs),
        risk_limits=qte.RiskLimits(allow_short=True), history_capacity=32,
        build_identity='schwab-minute-refinement-v1')).run(data, TimedOrders(intents))
    if result.open_trades or any(p.quantity for p in result.positions) or len(result.fills) != 2*len(intents):
        raise ValueError('ETF plan failed to reconcile; inspect rejected, missing or unclosed fills')
    if any(o.status == qte.OrderStatus.REJECTED for o in result.orders):
        raise ValueError('Unexpected rejection in benchmark run')
    records = []
    for intent, entry, exit_ in zip(intents, result.fills[::2], result.fills[1::2], strict=True):
        expected_entry_side = qte.OrderSide.BUY if intent.sign > 0 else qte.OrderSide.SELL
        expected_exit_side = qte.OrderSide.SELL if intent.sign > 0 else qte.OrderSide.BUY
        if ((entry.effective_ns, exit_.effective_ns) != (intent.entry_ns, intent.exit_ns)
                or (entry.quantity, exit_.quantity) != (intent.quantity, intent.quantity)
                or (entry.side, exit_.side) != (expected_entry_side, expected_exit_side)):
            raise AssertionError('ETF fills did not match the declared order schedule')
        net = intent.sign*intent.quantity*(exit_.executed_price-entry.executed_price) - entry.commission-exit_.commission
        gross = intent.sign*intent.quantity*(intent.exit_open-intent.entry_open)
        records.append(dict(day=intent.day, decision_ns=intent.decision_ns, entry_ns=intent.entry_ns,
            exit_ns=intent.exit_ns, entry_open=intent.entry_open, exit_open=intent.exit_open,
            sign=intent.sign, quantity=intent.quantity, regime=intent.regime, vol_proxy=intent.annual_vol_proxy,
            entry_fill=entry.executed_price, exit_fill=exit_.executed_price,
            gross_open_pnl=gross, net_pnl=net, execution_drag=gross-net))
    net = sum(row['net_pnl'] for row in records)
    if abs(net - (result.equity_curve[-1].equity-CASH)) > 1e-6 or abs(net-sum(t.net_realized_pnl for t in result.trades)) > 1e-6:
        raise AssertionError('ETF fill ledger, equity and closed trades disagree')
    return records, skips


def summarize(days, records):
    by_day = dict.fromkeys(days, 0.0)
    by_regime = defaultdict(lambda: {'net_pnl': 0.0, 'trades': 0})
    for row in records:
        by_day[row['day']] += row['net_pnl']
        bucket = by_regime[row['regime']]
        bucket['net_pnl'] += row['net_pnl']
        bucket['trades'] += 1
    net = sum(by_day.values())
    active = len({r['day'] for r in records})
    return dict(net_pnl=net, gross_open_pnl=sum(r['gross_open_pnl'] for r in records),
        execution_drag=sum(r['execution_drag'] for r in records), trades=len(records), active_dates=active,
        no_trade_dates=len(days)-active, wrong_way=sum(r['gross_open_pnl'] < 0 for r in records),
        friction_flips=sum(r['gross_open_pnl'] > 0 >= r['net_pnl'] for r in records),
        net_without_best_date=net-max([0.0, *by_day.values()]),
        worst_dates=sorted(by_day.items(), key=lambda pair: (pair[1], pair[0]))[:3],
        regime=dict(by_regime), daily=by_day)
