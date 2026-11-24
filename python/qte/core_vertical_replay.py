"""One-lot, defined-risk SPXW vertical replay for CORE mean-reversion studies."""
from dataclasses import dataclass
from decimal import Decimal

from .core_reversion_tape import gates
from .spx_spreads import MULTIPLIER, _pair_price
from .strategies.core_reversion import underlying_exit
from .volatility import MINUTE_NS as M


def usd(points):
    return Decimal(str(round(points, 9))) * MULTIPLIER


@dataclass(frozen=True)
class Vertical:
    long: str
    short: str
    width: float
    mid: float
    limit: float

    def pair(self, snapshot):
        legs = {b.key: b for b in snapshot.books if b.key in (self.long, self.short)}
        if len(legs) == 2:
            return legs[self.long], legs[self.short]
        return None


def tight(book):
    return (book.bid > 0 and min(book.bid_size, book.ask_size) > 0
            and book.ask - book.bid <= .1 * book.mid + 1e-12)


def choose(strategy, tape, signal, fee):
    snap = tape.asof(signal["at_ns"])
    if snap is None or snap.kind != "chain":
        return None, "missing_decision_chain"
    right, target = signal["right"], signal["target"]
    delta = .40 if strategy == "S1" else .45
    legs = [b for b in snap.books if b.key[12] == right]
    if not legs or not min(b.strike for b in legs) <= target <= max(b.strike for b in legs):
        return None, "uncovered_target_strike"
    short = min(legs, key=lambda b: (abs(b.strike-target), b.key))
    longs = [b for b in legs if b.delta is not None and round(abs(abs(b.delta)-delta), 9) <= .05
             and (b.strike < short.strike if right == "C" else b.strike > short.strike)]
    if not longs:
        return None, "missing_long_delta"
    long = min(longs, key=lambda b: (round(abs(abs(b.delta)-delta), 9), abs(b.strike-signal["spot"]), b.key))
    width = abs(long.strike-short.strike)
    price = _pair_price(long, short, width, 0, closing=False)
    if not tight(long) or not tight(short) or price is None:
        return None, "untradeable_entry_pair"
    debit = price[0]
    max_profit = usd(width-debit) - 4*fee
    max_loss = usd(debit) + 4*fee
    if strategy == "S1" and max_profit < Decimal("1.5")*max_loss:
        return None, "insufficient_reward_risk"
    return Vertical(long.key, short.key, width, long.mid-short.mid, debit), None


def replay(strategy, signal, day, minutes, tape, calendar, cash, risk_fraction, fee):
    vertical, reason = choose(strategy, tape, signal, fee)
    if reason:
        return dict(status="skipped", reason=reason)
    at = signal["at_ns"]
    trade = dict(long=vertical.long, short=vertical.short, width=vertical.width, quantity=1,
                 decision_ns=at, max_risk_usd=float(usd(vertical.limit)+4*fee))
    if usd(vertical.limit)+4*fee > cash*risk_fraction:
        return dict(trade, status="skipped", reason="one_lot_exceeds_budget")
    for snap in tape.snapshots:
        if snap.receipt >= at+M:
            break
        if snap.source <= at:
            continue
        if gates(strategy, day, snap.receipt, tape, calendar):
            return dict(trade, status="skipped", reason="entry_gate_withdrawn")
        pair = vertical.pair(snap)
        if not pair or not all(tight(b) for b in pair):
            continue
        opening = _pair_price(*pair, vertical.width, 0, closing=False)
        limit = vertical.mid if snap.source <= at+M//2 else vertical.limit
        if opening and opening[0] <= limit + 1e-12:
            trade.update(entry_ns=snap.receipt, entry_source_ns=snap.source, debit=round(opening[0], 9),
                         entry_long_ask=opening[1][0], entry_short_bid=opening[1][1])
            return monitor(strategy, signal, day, minutes, tape, vertical, trade, pair, fee)
    return dict(trade, status="skipped", reason="unfilled_entry_limit")


def monitor(strategy, signal, day, minutes, tape, vertical, trade, previous_pair, fee):
    opened, debit = trade["entry_ns"], trade["debit"]
    deadline = (min(opened+45*M, day.close_ns-15*M) if strategy == "S1"
                else min(day.open_ns+150*M, day.close_ns-15*M))
    next_bar = signal["at_ns"]+M
    last_seen = opened
    trigger = None

    def close(credit, snap, reason, ts, legs=None):
        fees = (2 if reason == "settlement" else 4)*fee
        return dict(trade, status="closed", reason=reason, exit_trigger_ns=ts, exit_ns=snap.receipt,
                    exit_source_ns=None if reason == "settlement" else snap.source, credit=round(credit, 9),
                    exit_leg_prices=legs, fees_usd=float(fees), net_pnl_usd=float(usd(credit)-usd(debit)-fees))

    def exit_intent(reason, ts, pair):
        mid = min(vertical.width, max(0., pair[0].mid-pair[1].mid))
        bid = max(0., pair[0].bid-pair[1].ask)
        return reason, ts, mid, min(bid, mid)

    for snap in tape.snapshots:
        if snap.source <= opened or snap.receipt-snap.source > M:
            continue
        pair = vertical.pair(snap)
        if not pair:
            continue
        if snap.receipt-last_seen > 2*M:
            return dict(trade, status="unresolved", reason="missing_quote_coverage")
        last_seen = snap.receipt
        while trigger is None and min(next_bar, deadline) <= snap.receipt:
            if deadline <= next_bar:
                trigger = exit_intent("time_stop", deadline, previous_pair)
                break
            if next_bar not in minutes:
                return dict(trade, status="unresolved", reason="missing_underlying_exit_bar")
            reason = underlying_exit(
                strategy, signal, minutes[next_bar], minutes[next_bar-M], next_bar-M >= opened)
            if reason:
                trigger = exit_intent(reason, next_bar, previous_pair)
            next_bar += M
        price = _pair_price(*pair, vertical.width, 0, closing=True)
        if trigger:
            reason, ts, mid, natural = trigger
            age = snap.source-ts
            limit = mid if age <= M//2 else natural if age <= M else 0.
            if age > 0 and price and price[0] + 1e-12 >= limit:
                return close(price[0], snap, reason, ts, price[1])
        elif strategy == "S1" and min(pair[0].bid_size, pair[1].ask_size) > 0:
            raw_bid = pair[0].bid-pair[1].ask
            if raw_bid <= .5*debit:
                trigger = exit_intent("half_debit_stop", snap.receipt, pair)
            elif price and price[0] >= debit + .70*(vertical.width-debit):
                trigger = exit_intent("profit_target", snap.receipt, pair)
        previous_pair = pair
        if snap.receipt == day.close_ns and tape.settlement is not None:
            direction = 1 if signal["right"] == "C" else -1
            long_strike = int(vertical.long[13:])/1000
            value = min(vertical.width, max(0., direction*(tape.settlement-long_strike)))
            return close(value, snap, "settlement", None)
    return dict(trade, status="unresolved", reason="end_of_quote_coverage")
