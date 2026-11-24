"""Offline, long-only S7 limit-order replay with a single shared research account."""
from dataclasses import dataclass
from decimal import Decimal

from .volatility import MINUTE_NS
from .s7_tape import FRESH, select

MULTIPLIER = 100
MAX_GAP = 2 * MINUTE_NS


def money(value):
    return Decimal(str(value))


@dataclass
class Account:
    cash: Decimal
    month: str = ""
    remaining: Decimal = Decimal(0)
    unresolved: bool = False

    def budget(self, day, days_left):
        if day[:7] != self.month:
            self.month = day[:7]
            self.remaining = self.cash / 10
        return min(self.cash / 100, 2*self.remaining/days_left, self.remaining, self.cash)


def replay(signal_row, day, index, tape, account, days_left, fee):
    signal = signal_row["signal"]
    at, direction = signal["at_ns"], signal["right"]
    cap = account.budget(day.trading_date, days_left)
    trade = dict(status="skipped", budget_usd=float(cap), fills=[])
    selected = select(tape, at, signal["spot"], direction)
    if selected is None:
        return dict(trade, reason="no_eligible_contract")
    trade["contract"] = selected.key
    order_quantity = int(cap // (money(selected.ask) * MULTIPLIER + fee))
    trade.update(order_quantity=order_quantity, decision_delta=selected.delta,
                 decision_bid=selected.bid, decision_ask=selected.ask)
    if not order_quantity:
        return dict(trade, reason="one_contract_exceeds_budget")
    # Two committed limit prices. Cancel the balance after the first executable quote.
    closes_at = day.close_ns - 15*MINUTE_NS
    for snap in tape.snapshots:
        if snap.receipt > min(at+FRESH, closes_at):
            break
        if snap.source <= at or snap.receipt-snap.source > FRESH:
            continue
        book = next((b for b in snap.books if b.key == selected.key), None)
        if book is None or min(book.bid_size, book.ask_size) <= 0 or book.ask-book.bid > .1*book.mid+1e-12:
            continue
        limit = selected.mid if snap.source <= at+FRESH//2 else selected.ask
        if book.ask > limit:
            continue
        unit = money(book.ask)*MULTIPLIER + fee
        quantity = min(book.ask_size, order_quantity)
        debit = quantity * unit
        account.cash -= debit
        account.remaining -= debit
        trade["fills"].append(dict(at_ns=snap.receipt, source_ns=snap.source, side="buy",
                                   quantity=quantity, price=book.ask, fee_usd=float(quantity*fee)))
        break
    if not trade["fills"]:
        return dict(trade, reason="unfilled_entry_limit")
    premium = money(book.ask)
    opened = trade["fills"][0]["at_ns"]
    remaining, proceeds = quantity, Decimal(0)
    targets = [(3*premium, quantity//3), (6*premium, (2*quantity)//3-quantity//3)]
    midpoint = signal_row["midpoint"]
    next_bar = (at-day.open_ns)//MINUTE_NS + 5
    stop, sell_limit, last_seen = None, None, opened
    revisions = 0
    last_bid = money(book.bid)

    def sell(n, price, snap_ns, source, reason, cost):
        nonlocal remaining, proceeds
        credit = n*(price*MULTIPLIER-cost)
        proceeds += credit
        account.cash += credit
        remaining -= n
        trade["fills"].append(dict(at_ns=snap_ns, source_ns=source, side="sell", quantity=n,
                                   price=float(price), fee_usd=float(n*cost), reason=reason))

    def incomplete(reason):
        account.unresolved = True
        return dict(trade, status="unresolved", reason=reason, open_quantity=remaining)

    for snap in tape.snapshots:
        if snap.source <= opened or snap.receipt-snap.source > FRESH:
            continue
        current = next((b for b in snap.books if b.key == selected.key), None)
        if current is None:
            continue
        if snap.receipt-last_seen > MAX_GAP:
            return incomplete("quote_monitoring_gap")
        last_seen = snap.receipt
        while stop is None and day.open_ns + next_bar*MINUTE_NS <= snap.receipt:
            trigger = day.open_ns + next_bar*MINUTE_NS
            if trigger >= closes_at:
                stop = ("time", closes_at)
            elif next_bar not in index:
                return incomplete("index_monitoring_gap")
            else:
                close = index[next_bar][2]
                midpoint_hit = close <= midpoint if direction == "C" else close >= midpoint
                if midpoint_hit:
                    stop = ("range_midpoint", trigger)
            if stop:
                sell_limit = last_bid
            next_bar += 5
        bid, size = money(current.bid), current.bid_size
        if stop:
            if snap.source > stop[1] and bid >= sell_limit and bid > 0 and size:
                sell(min(size, remaining), bid, snap.receipt, snap.source, stop[0], fee)
            # Replace a missed or partly executed exit limit for the next source update.
            if remaining and snap.source > stop[1] and 0 < bid < sell_limit and revisions < 2:
                sell_limit = bid
                stop = (stop[0], snap.receipt)
                revisions += 1
        else:
            for i, (limit, n) in enumerate(targets):
                if n and size and bid >= limit:
                    sold = min(n, size)
                    sell(sold, limit, snap.receipt, snap.source, ("3x", "6x")[i], fee)
                    targets[i] = (limit, n-sold)
                    size -= sold
        if not remaining:
            break
        if bid > 0:
            last_bid = bid
    if remaining:
        if last_seen != day.close_ns:
            return incomplete("quote_monitoring_gap")
        if tape.settlement is None:
            return incomplete("missing_official_settlement")
        intrinsic = (money(tape.settlement)-money(selected.strike))*(1 if direction == "C" else -1)
        sell(remaining, max(Decimal(0), intrinsic), day.close_ns, None, "pm_cash_settlement", Decimal(0))
    return dict(trade, status="closed", net_pnl_usd=float(proceeds-debit),
                premium_and_entry_fees_usd=float(debit), cash_after_usd=float(account.cash),
                remaining_monthly_budget_usd=float(account.remaining))
