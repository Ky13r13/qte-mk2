"""Replay outright SPXW model prices on observed regular-session index bars."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
import csv
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import io
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .gamma_surface import _black76
from .spx_spreads import FIVE_MIN_NS, MAX_ENTRIES, MULTIPLIER, REGIMES, YEAR_NS, _News, _decisions

NY = ZoneInfo("America/New_York")
SECOND_NS = 1_000_000_000
INITIAL_CENTS = 10_000_000
TICKS_PER_POINT = 10
POINT_CENTS = MULTIPLIER * 100
CENTS_PER_TICK = POINT_CENTS // TICKS_PER_POINT
MAX_CONTRACTS = 5
RATE = .04
DIVIDEND_YIELD = .013
SIZING = {"1 contract": (1, 0), "2 contracts": (2, 0), "5 contracts": (5, 0),
          "2% risk budget": (0, 200), "8% risk budget": (0, 800), "16% risk budget": (0, 1_600)}
COSTS = {"base": {"half_width_points": .25, "fee_cents": 125, "exit_iv_shift": 0.0},
         "wide costs": {"half_width_points": 1.0, "fee_cents": 250, "exit_iv_shift": 0.0},
         "wide costs and IV drop": {"half_width_points": 1.0, "fee_cents": 250, "exit_iv_shift": -.01}}


def load_volatility(path):
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != ["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]:
        raise ValueError("expected the Cboe volatility history CSV")
    closes = {}
    for row in reader:
        day = datetime.strptime(row["DATE"], "%m/%d/%Y").date()
        close = float(row["CLOSE"]) / 100
        if day in closes or not math.isfinite(close) or not 0 < close <= 5:
            raise ValueError("invalid or duplicate volatility close")
        closes[day] = close
    if not closes:
        raise ValueError("empty volatility history")
    return closes, {"sha256": sha256(raw).hexdigest(), "first": min(closes).isoformat(),
                    "last": max(closes).isoformat()}


def _previous_close(series, days, day):
    index = bisect_left(days, day) - 1
    if index < 0 or (day - days[index]).days > 4:
        return None
    return days[index], series[days[index]]


def _model_cents(bar, strike, expiry, right, annual_iv, cost, *, closing):
    years = (expiry - bar["timestamp_ns"]) / YEAR_NS
    forward = bar["close"] * math.exp((RATE - DIVIDEND_YIELD) * years)
    iv = annual_iv + cost["exit_iv_shift"] if closing else annual_iv
    mid = _black76(forward, strike, max(.01, iv), years, RATE, right)["price"]
    width = cost["half_width_points"]
    if closing:
        ticks = math.floor(max(0, mid - width) * TICKS_PER_POINT)
    else:
        ticks = math.ceil((mid + width) * TICKS_PER_POINT)
    return ticks * CENTS_PER_TICK


def _session_opportunities(session, daily, weekly):
    day = date.fromisoformat(session["id"])
    start, end = session["open_ns"], session["end_ns"]
    bars = session["bars"]
    anchor = bars[0]["open"]
    move = anchor * daily[1] / math.sqrt(252)
    expiry_day = day + timedelta(days=7)
    expiry = int(datetime.combine(expiry_day, time(16), NY).timestamp()) * SECOND_NS
    observations = [{"timestamp_ns": start, "available_ns": bars[0]["available_ns"], "close": anchor}]
    observations.extend({key: row[key] for key in ("timestamp_ns", "available_ns", "close")} for row in bars)
    decisions = list(_decisions(observations, start, end, bars[0]["available_ns"], anchor, move, _News(())))
    opportunities = []
    times = [row["timestamp_ns"] for row in bars]
    for signal in decisions:
        if signal.right is None:
            continue
        # Use the first bar strictly after the decision becomes available.
        entry_index = bisect_right(times, signal.available)
        if entry_index >= len(bars):
            continue
        entry = bars[entry_index]
        # The regular-session end is known at the decision, independent of prices.
        target_exit = entry["available_ns"] + FIVE_MIN_NS
        if target_exit > end:
            continue
        exit_index = bisect_left(times, target_exit)
        exit_bar = bars[exit_index] if exit_index < len(bars) else None
        # Select on signal spot, with lower strike first on a tie.
        strike = 5 * math.ceil(signal.spot / 5 - .5)
        opportunities.append((signal, strike, expiry, entry, exit_bar))
    return {"id": session["id"], "bar_count": len(bars), "opening_spot": anchor, "expected_move": move,
            "daily_vol_date": daily[0].isoformat(), "daily_vol": daily[1],
            "weekly_vol_date": weekly[0].isoformat(), "weekly_vol": weekly[1],
            "regime_observations": dict(Counter(d.regime for d in decisions)),
            "regime_opportunities": dict(Counter(d.regime for d in decisions if d.right))}, opportunities


def _quantity(cash, per_contract_risk, fixed, budget_bps):
    if fixed:
        return (fixed, None) if cash >= fixed * per_contract_risk else (0, "cash")
    budget = cash * budget_bps // 10_000
    lots = min(MAX_CONTRACTS, budget // per_contract_risk)
    return (lots, None) if lots else (0, "budget_below_one_contract")


def _candidate(label, fixed, budget, scenario, sessions):
    cost = COSTS[scenario]
    fee = cost["fee_cents"]
    cash = peak = INITIAL_CENTS
    drawdown = 0.0
    trades, daily_equity = [], []
    skips = Counter()
    for session, opportunities in sessions:
        exit_available = 0
        entries = 0
        day_start_cash = cash
        for signal, strike, expiry, entry, exit_bar in opportunities:
            if signal.regime == "shock":
                skips["shock_veto"] += 1
                continue
            if signal.available < exit_available:
                skips["position_open"] += 1
                continue
            if entries >= MAX_ENTRIES:
                skips["session_trade_limit"] += 1
                continue
            ask = _model_cents(entry, strike, expiry, signal.right, session["weekly_vol"], cost, closing=False)
            risk = ask + 2 * fee
            count, reason = _quantity(cash, risk, fixed, budget)
            if not count:
                skips[reason] += 1
                continue
            entry_cost = (ask + fee) * count
            cash -= entry_cost
            entries += 1
            unresolved = exit_bar is None
            if unresolved:
                close_credit = 0  # No executed exit. Conservative recovery assumption.
                exit_available = expiry
            else:
                bid = _model_cents(exit_bar, strike, expiry, signal.right, session["weekly_vol"], cost, closing=True)
                close_credit = (bid - fee) * count
                cash += close_credit
                exit_available = exit_bar["available_ns"]
            pnl = close_credit - entry_cost
            peak = max(peak, cash)
            drawdown = max(drawdown, (peak - cash) / peak)
            trades.append({"session": session["id"], "regime": signal.regime, "side": signal.right,
                           "strike": strike, "expiry_ns": expiry, "contracts": count,
                           "signal_ns": signal.reference, "signal_available_ns": signal.available,
                           "entry_ns": entry["available_ns"], "exit_ns": None if unresolved else exit_available,
                           "entry_spot": entry["close"], "exit_spot": None if unresolved else exit_bar["close"],
                           "entry_premium_points": ask / POINT_CENTS,
                           "exit_premium_points": None if unresolved else bid / POINT_CENTS,
                           "entry_debit": entry_cost / 100, "risk_reserved": risk * count / 100,
                           "net_pnl": pnl / 100, "unresolved": unresolved})
        daily_equity.append({"session": session["id"], "equity": cash / 100, "day_pnl": (cash - day_start_cash) / 100})

    def breakdown(field, label):
        return _aggregate([trade for trade in trades if trade[field] == label])

    return {"sizing": label, "scenario": scenario, "fixed_contracts": fixed, "budget_bps": budget,
            "summary": _aggregate(trades), "skips": dict(skips), "closed_equity_max_drawdown": drawdown,
            "worst_session_pnl": min(day["day_pnl"] for day in daily_equity),
            "session_equity": daily_equity, "trades": trades,
            "by_regime": {regime: breakdown("regime", regime) for regime in REGIMES},
            "by_option": {side: breakdown("side", side) for side in ("call", "put")}}


def _aggregate(trades):
    pnl = round(math.fsum(trade["net_pnl"] for trade in trades), 2)
    realized = round(math.fsum(trade["net_pnl"] for trade in trades if not trade["unresolved"]), 2)
    risk = round(math.fsum(trade["risk_reserved"] for trade in trades), 2)
    return {"entries": len(trades), "contracts": sum(t["contracts"] for t in trades),
            "unresolved": sum(t["unresolved"] for t in trades), "closed_net_pnl": realized, "net_pnl": pnl,
            "risk_deployed": risk, "return_on_debit_at_risk": pnl / risk if risk else None}


def study(sessions, daily, weekly, sources):
    daily_days, weekly_days = sorted(daily), sorted(weekly)
    opportunities = []
    omitted = []
    for session in sessions:
        day = date.fromisoformat(session["id"])
        vols = (_previous_close(daily, daily_days, day), _previous_close(weekly, weekly_days, day))
        if None in vols:
            omitted.append(session["id"])
            continue
        opportunities.append(_session_opportunities(session, *vols))
    if not opportunities:
        raise ValueError("no SPX sessions with prior-session volatility inputs")
    return {"schema_version": 1, "evidence": "observed_spx_modeled_spxw_options", "sources": sources,
            "initial_cash": INITIAL_CENTS / 100, "costs": COSTS, "rate": RATE, "dividend_yield": DIVIDEND_YIELD,
            "liquidity": "Modeled price at selected quantity, with no option size or fill evidence.",
            "news_coverage": "unavailable",
            "omitted_missing_volatility": omitted, "sessions": [row for row, _ in opportunities],
            "candidates": [_candidate(label, fixed, budget, scenario, opportunities) for scenario in COSTS
                           for label, (fixed, budget) in SIZING.items()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spx", required=True, type=Path, help="FirstRate SPX sample ZIP")
    parser.add_argument("--vix1d", required=True, type=Path, help="Cboe VIX1D daily CSV")
    parser.add_argument("--vix9d", required=True, type=Path, help="Cboe VIX9D daily CSV")
    parser.add_argument("--output", required=True, type=Path, help="New JSON report")
    args = parser.parse_args()
    from .spx_public_data import load_firstrate_spx

    try:
        sessions, spot_provenance = load_firstrate_spx(args.spx)
        daily, daily_provenance = load_volatility(args.vix1d)
        weekly, weekly_provenance = load_volatility(args.vix9d)
        daily = {day: value for day, value in daily.items() if day >= date(2023, 4, 24)}
        report = study(sessions, daily, weekly, {"spx": spot_provenance, "vix1d": daily_provenance,
                                                "vix9d": weekly_provenance})
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False, sort_keys=True, indent=2)
            stream.write("\n")
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"qte: {exc}\n")
    print(args.output)


if __name__ == "__main__":
    main()
