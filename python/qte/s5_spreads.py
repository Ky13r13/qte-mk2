"""S5 independent one-lot SPXW vertical probes, using QTE's synchronized pair pricing."""
from datetime import date

from .adapters.schwab_option_stream import _contracts as validate_keys
from .adapters.schwab_research import MINUTE_NS
from .spx_spreads import (_Contract, _fields, _finite, _first_pair, _ns, _pair_price, _pair_series,
                          _quotes, MULTIPLIER)

WIDTH = 5
MAX_GAP = 2 * MINUTE_NS


def _chain(payload, session, schedule):
    _fields(payload, ("source_ns", "available_ns", "calls", "quotes"), "S5 chain and quote tape")
    source, received = _ns(payload["source_ns"]), _ns(payload["available_ns"])
    at = session["decision_ns"]
    if not source <= received <= at or at - source > MINUTE_NS:
        return None, "missing_fresh_decision_chain"
    calls = payload["calls"]
    if type(calls) is not list or type(payload["quotes"]) is not list:
        raise ValueError("Expected S5 call and quote arrays")
    if not calls:
        return None, "no_calls"
    for item in calls:
        _fields(item, ("contract","delta"), "S5 chain call")
    validate_keys([c["contract"] for c in calls])
    contracts, deltas = {}, {}
    for item in calls:
        key = item["contract"]
        if key[:6] != "SPXW  " or key[12] != "C":
            raise ValueError("Expected exact standard SPXW call keys")
        expiry = date(2000+int(key[6:8]),int(key[8:10]),int(key[10:12]))
        dte = (expiry-date.fromisoformat(session["date"])).days
        if not 2 <= dte <= 5:
            raise ValueError("S5 chain input requires 2–5 calendar DTE")
        end = schedule.day(expiry.isoformat()).close_ns
        delta = _finite(item["delta"], "decision call delta")
        if end is None or not 0 <= delta <= 1:
            raise ValueError("Invalid PM expiry date or call delta")
        contracts[key] = _Contract(key, "call", int(key[13:])/1000, end, received)
        deltas[key] = delta
    expiry = max(c.expiry for c in contracts.values())
    chain = [c for c in contracts.values() if c.expiry == expiry]
    near_half = [c for c in chain if .45 <= deltas[c.id] <= .55]
    if not near_half:
        return None, "no_near_half_delta_long"
    long = min(near_half, key=lambda c:(abs(deltas[c.id]-.50), c.strike))
    short = next((c for c in chain if c.strike == long.strike + WIDTH), None)
    if short is None:
        return None, "missing_short_strike"
    return (contracts,long,short), None


def _close_updates(series):
    # Pair selection/pricing is identical to the existing SPXW vertical replay.
    observations = []
    for ts in series[0]:
        long, short = series[1][ts], series[2][ts]
        price = _pair_price(long, short, WIDTH, 0, closing=True)
        if price is not None:
            observations.append((max(long.available,short.available), ts, *price))
    return sorted(observations)


def _monitor(series, entry, sessions, by_day, expiry, fee):
    opened, _, debit, _ = entry
    observations = _close_updates(series)
    cursor = 0
    reason = None
    for age, day in enumerate(sessions):
        start, end = max(opened, day.open_ns), day.close_ns
        if age > 5 or end > expiry:
            break
        row = by_day.get(day.trading_date)
        cutoff = end - 15 * MINUTE_NS
        forced = "expiry" if end == expiry else ("five_sessions" if age == 5 else None)
        last_seen = start
        latest_source = start - 1
        while cursor < len(observations) and observations[cursor][0] <= end:
            receipt, source, credit, prices = observations[cursor]
            cursor += 1
            if source <= latest_source or source <= opened:
                continue
            if receipt - last_seen > MAX_GAP:
                return dict(status="unresolved", reason="quote_monitoring_gap")
            last_seen = receipt
            latest_source = source
            if age > 0 and receipt >= cutoff and reason is None:
                if forced:
                    reason = forced, cutoff
                elif row is None or row["features"] is None:
                    return dict(status="unresolved", reason="missing_spy_exit_observation")
                elif row["features"]["exit_signal"]:
                    reason = "spy_indicator", cutoff
            if reason and source > reason[1]:
                return dict(status="closed", reason=reason[0], exit_trigger_ns=reason[1],
                            exit_ns=receipt, exit_source_ns=source, exit_credit=credit,
                            exit_long_bid=prices[0], exit_short_ask=prices[1],
                            fees_usd=4*fee, net_pnl_usd=(credit-debit)*MULTIPLIER-4*fee)
            if reason is None and credit <= .5 * debit:
                reason = "half_debit_stop", receipt
        if end - last_seen > MAX_GAP or forced:
            return dict(status="unresolved", reason="quote_monitoring_gap" if not forced else "missing_exit_quote")
    return dict(status="unresolved", reason="end_of_capture")


def replay_options(report, schedule, tape):
    _fields(tape, ("schema","evidence","fee_per_leg_usd","max_risk_usd","sessions"), "S5 options tape")
    if tape["schema"] != "qte-s5-spxw-v1" or tape["evidence"] not in ("observed","synthetic"):
        raise ValueError("Unknown S5 quote schema or evidence")
    fee = _finite(tape["fee_per_leg_usd"], "all-in fee per leg")
    cap = _finite(tape["max_risk_usd"], "maximum one-lot risk USD")
    if fee < 0 or cap <= 0 or type(tape["sessions"]) is not dict:
        raise ValueError("Invalid S5 quote configuration")
    by_day = {r["date"]:r for r in report["rows"]}
    days = [d for d in schedule.days if d.open_ns is not None]
    indexes = {d.trading_date:i for i,d in enumerate(days)}
    probes = []
    for row in report["rows"]:
        if not (row["standard_session"] and row["features"] and row["features"]["signal"]):
            continue
        day, at = row["date"], row["decision_ns"]
        probe = dict(date=day, decision_ns=at, status="skipped")
        probes.append(probe)
        if day not in tape["sessions"]:
            probe["reason"] = "missing_chain_and_quotes"
            continue
        payload = tape["sessions"][day]
        selection, reason = _chain(payload, row, schedule)
        if reason:
            probe["reason"] = reason
            continue
        contracts, long, short = selection
        future = days[indexes[day]:indexes[day]+6]
        quotes = _quotes(payload["quotes"], contracts, at, max(c.expiry for c in contracts.values()))
        series = _pair_series(quotes, long, short)
        # Eligibility at entry requires both spreads <= 5% of their respective mids.
        ticks, bids, asks = series
        entry_series = ([ts for ts in ticks if all(q.ask-q.bid <= .05*(q.ask+q.bid)/2
                         for q in (bids[ts], asks[ts]))], bids, asks)
        probe.update(long=long.id, short=short.id, expiry_ns=long.expiry)
        entry = _first_pair(entry_series, row["mark_ns"], min(at+10*MINUTE_NS,future[0].close_ns), WIDTH, 0, closing=False)
        if entry is None:
            probe["reason"] = "no_executable_entry_pair"
            continue
        opened, source, debit, prices = entry
        risk = debit*MULTIPLIER + 4*fee
        if risk > cap:
            probe["reason"] = "one_lot_exceeds_risk_cap"
            continue
        probe.update(entry_ns=opened, entry_source_ns=source, entry_debit=debit,
                     entry_long_ask=prices[0], entry_short_bid=prices[1], risk_usd=risk)
        probe.update(_monitor(series, entry, future, by_day, long.expiry, fee))
    closed = [p for p in probes if p["status"] == "closed"]
    unresolved = [p for p in probes if p["status"] == "unresolved"]
    return dict(evidence=tape["evidence"], probes=probes, closed=len(closed), unresolved=len(unresolved),
                closed_only_pnl_usd=sum(p["net_pnl_usd"] for p in closed) if closed else None,
                fee_per_leg_usd=fee, max_risk_usd=cap)
