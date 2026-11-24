"""Pre-registered study: calm-regime 0DTE SPX iron condor (docs/strategies/calm-regime-condor.md).

Observed SPY 5-minute paths scaled to SPX; option legs are modeled with Black-76 at
IV = prior VIX x m. Regimes come from qte.regime_lab (fit 2008-2018). Sessions end
at the locked validation end; the holdout is never loaded.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import itertools
import json
import math
from pathlib import Path
from statistics import fmean, pstdev
import sys

from qte import _core, regime_lab, truth_db
from qte.gamma_surface import _black76
from qte.lowvol_call_scalp import SPLIT, load

TICK, FEE, MULT = 0.05, 1.25, 100
PRIMARY = dict(k=1.0, wing=25, structure="condor", stop=False, iv_mult=1.0, half_spread=0.05, entry=600)
CALM = ("R01", "R02", "R03")
HIGH_RANGE = ("R07", "R08", "R09", "R10", "R11", "R12")


def leg_mid(spot, strike, minutes_left, iv, kind):
    if minutes_left <= 0:
        return max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)
    return _black76(spot, strike, iv, minutes_left / (390 * 252), 0.0, kind)["price"]


def sell_fill(mid, h):
    return max(0.0, math.floor(round((mid - h) / TICK, 9)) * TICK)


def buy_fill(mid, h):
    return max(TICK, math.ceil(round((mid + h) / TICK, 9)) * TICK)


def structure_legs(spot, minutes_left, iv, p):
    em_rem = spot * iv * math.sqrt(minutes_left / (390 * 252))
    sp = 5 * math.floor((spot - p["k"] * em_rem) / 5)
    legs = [(-1, "put", sp), (+1, "put", sp - p["wing"])]
    if p["structure"] == "condor":
        sc = 5 * math.ceil((spot + p["k"] * em_rem) / 5)
        legs += [(-1, "call", sc), (+1, "call", sc + p["wing"])]
    return legs


def value(legs, spot, minutes_left, iv):
    """Mark-to-model value of the position (negative of what it costs to close at mid)."""
    return sum(q * leg_mid(spot, k, minutes_left, iv, kind) for q, kind, k in legs)


def trade_day(bars, c, p):
    """One iron condor / put spread for the session, or None if data is missing."""
    by_min = {b[0]: b for b in bars}
    entry_bar = by_min.get(p["entry"])
    last = by_min.get(955)
    if entry_bar is None or last is None:
        return None
    r, iv = c["ratio"], c["vix"] / 100 * p["iv_mult"]
    spot = entry_bar[1] * r
    left = 960 - p["entry"]
    legs = structure_legs(spot, left, iv, p)
    credit = sum(sell_fill(leg_mid(spot, k, left, iv, kind), p["half_spread"]) if q < 0
                 else -buy_fill(leg_mid(spot, k, left, iv, kind), p["half_spread"]) for q, kind, k in legs)
    fees = FEE * len(legs)
    shorts = [k for q, kind, k in legs if q < 0]
    lo_short = min(k for q, kind, k in legs if q < 0 and kind == "put")
    hi_short = max((k for q, kind, k in legs if q < 0 and kind == "call"), default=math.inf)
    if p["stop"]:
        for m in sorted(x for x in by_min if p["entry"] <= x < 955):
            s = by_min[m][4] * r
            if s < lo_short or s > hi_short:
                t = 960 - (m + 5)
                cost = sum(buy_fill(leg_mid(s, k, t, iv, kind), p["half_spread"]) if q < 0
                           else -sell_fill(leg_mid(s, k, t, iv, kind), p["half_spread"]) for q, kind, k in legs)
                pnl = (credit - cost) * MULT - fees - FEE * len(legs)
                return dict(credit=round(credit, 2), settle=None, pnl=round(pnl, 2), reason="stop",
                            shorts=shorts, spot=round(spot, 2), max_loss=_max_loss(legs, credit, fees))
    settle_spot = last[4] * r
    payoff = -value(legs, settle_spot, 0, iv)  # cash we owe at settlement for the short structure
    pnl = (credit - payoff) * MULT - fees
    return dict(credit=round(credit, 2), settle=round(settle_spot, 2), pnl=round(pnl, 2),
                reason="settle", shorts=shorts, spot=round(spot, 2), max_loss=_max_loss(legs, credit, fees))


def _max_loss(legs, credit, fees):
    wing = max(abs(a[2] - b[2]) for a, b in zip(legs[0::2], legs[1::2]))
    return round((wing - credit) * MULT + fees, 2)


def trade_day_native(bars, context, spec):
    return _core.condor_day([(b[0], b[1], b[4]) for b in bars], context["ratio"], context["vix"],
                            spec["k"], spec["wing"], spec["structure"] == "put_spread", spec["stop"],
                            spec["iv_mult"], spec["half_spread"], spec["entry"])


def run(sessions, ctx, regimes, p, allowed, *, simulator=trade_day_native):
    trades = []
    for d in sorted(sessions):
        c = ctx.get(d)
        if c is None or (allowed is not None and regimes.get(d) not in allowed):
            continue
        t = simulator(sessions[d], c, p)
        if t:
            t["date"], t["regime"] = d, regimes.get(d)
            trades.append(t)
    return trades


def stats(trades, dev_end):
    def block(ts):
        if not ts:
            return dict(trades=0, net=0.0, mean=None, t=None, win_rate=None)
        x = [t["pnl"] for t in ts]
        sd = pstdev(x) * math.sqrt(len(x) / (len(x) - 1)) if len(x) > 1 else 0.0
        return dict(trades=len(x), net=round(sum(x), 2), mean=round(fmean(x), 2),
                    t=round(fmean(x) / (sd / math.sqrt(len(x))), 2) if sd else None,
                    win_rate=round(sum(v > 0 for v in x) / len(x), 3))
    years = {}
    for t in trades:
        years[t["date"][:4]] = years.get(t["date"][:4], 0.0) + t["pnl"]
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dict(all=block(trades), dev=block([t for t in trades if t["date"] <= dev_end]),
                validation=block([t for t in trades if t["date"] > dev_end]),
                since_2019=block([t for t in trades if t["date"] >= "2019-01-01"]),
                by_year={k: round(v) for k, v in sorted(years.items())},
                years_profitable=round(sum(v > 0 for v in years.values()) / len(years), 2) if years else None,
                worst_trade=min((t["pnl"] for t in trades), default=None),
                max_loss_bound=max((t["max_loss"] for t in trades), default=None),
                max_drawdown=round(dd, 2), mean_credit=round(fmean(t["credit"] for t in trades), 2) if trades else None,
                breaches=sum(t["pnl"] < 0 for t in trades))


def verdict(s, every_day, high_range, stressed):
    net = s["all"]["net"]
    checks = {
        "positive_dev_validation_and_since_2019": s["dev"]["net"] > 0 and s["validation"]["net"] > 0
        and s["since_2019"]["net"] > 0,
        "dev_trades_150_and_t_2": s["dev"]["trades"] >= 150 and (s["dev"]["t"] or 0) >= 2,
        "years_profitable_70pct": (s["years_profitable"] or 0) >= 0.7,
        "regime_filter_matters": (s["all"]["mean"] or -1e9) > (every_day["all"]["mean"] or 1e9)
        and (high_range["all"]["mean"] or 1e9) < s["all"]["mean"],
        "survives_iv_0.8_and_spread_0.10": stressed["iv_x0.8"]["all"]["net"] > 0
        and stressed["half_spread_0.10"]["all"]["net"] > 0,
        "drawdown_within_25pct_of_net": net > 0 and s["max_drawdown"] <= 0.25 * net,
    }
    return dict(checks=checks, passed=all(checks.values()))


def study(db):
    split = json.loads(SPLIT.read_text())
    sessions, ctx = load(db, last_session=split["validation_end"])
    report = regime_lab.run(db)
    regimes = {r["date"]: regime_lab.classify(r, report) for r in regime_lab.build_rows(db)}
    dev_end = split["dev_end"]
    primary_trades = run(sessions, ctx, regimes, PRIMARY, CALM)
    primary = stats(primary_trades, dev_end)
    stressed = {name: stats(run(sessions, ctx, regimes, dict(PRIMARY, **chg), CALM), dev_end) for name, chg in
                (("iv_x0.8", {"iv_mult": 0.8}), ("iv_x1.2", {"iv_mult": 1.2}),
                 ("half_spread_0.10", {"half_spread": 0.10}), ("entry_12:00", {"entry": 720}))}
    every_day = stats(run(sessions, ctx, regimes, PRIMARY, None), dev_end)
    high_range = stats(run(sessions, ctx, regimes, PRIMARY, HIGH_RANGE), dev_end)
    by_regime = {}
    for rid in sorted({r for r in regimes.values() if r}):
        s = stats(run(sessions, ctx, regimes, PRIMARY, (rid,)), dev_end)["all"]
        by_regime[rid] = s
    grid = []
    for k, wing, structure, stop in itertools.product((0.75, 1.0, 1.25), (10, 25, 50), ("condor", "put_spread"),
                                                      (False, True)):
        p = dict(PRIMARY, k=k, wing=wing, structure=structure, stop=stop)
        s = stats(run(sessions, ctx, regimes, p, CALM), dev_end)
        grid.append(dict(params=dict(k=k, wing=wing, structure=structure, stop=stop), all=s["all"], dev=s["dev"],
                         validation=s["validation"], since_2019=s["since_2019"],
                         years_profitable=s["years_profitable"], max_drawdown=s["max_drawdown"]))
    return dict(schema="qte-calm-condor-v1", sessions=len(sessions), first=min(sessions), last=max(sessions),
                dev_end=dev_end, primary_spec=PRIMARY, calm_regimes=CALM, primary=primary,
                primary_verdict=verdict(primary, every_day, high_range, stressed), stressed=stressed,
                every_day_control=every_day, high_range_control=high_range, by_regime_all_trades=by_regime,
                grid=grid, primary_trades=primary_trades,
                caveats=["Legs modeled with flat Black-76 at prior VIX x m (no skew, no intraday IV changes).",
                         "SPY 5-minute closes scaled to SPX; settlement uses the 15:55-16:00 bar close.",
                         "Daily SPXW expiries exist only since 2022; earlier years assume a 0DTE contract."])


# Schwab quotes option IV on calendar time (minutes / 525,600). This study prices with VIX on
# trading time (minutes / (390 x 252)). Equal remaining standard deviations require
# IV_trading = IV_calendar x sqrt(390 x 252 / 525,600) = IV_calendar x 0.4324.
# Verified 2026-11-24 12:26 ET: calendar-time Black-76 reproduced SPXW 0DTE mids within ~$0.70;
# trading time was off by 3-7x. (Regression: earlier reports compared calendar IV with VIX directly.)
CALENDAR_TO_TRADING = math.sqrt(390 * 252 / 525_600)


def iv_check(chain, vix, *, k=1.0, minutes_left=None):
    """Real IV at the strategy's short strikes from a Schwab optionChain response, as multiples of VIX.

    ``minutes_left`` defaults to the chain's own days-to-expiration (0DTE: minutes to 16:00 ET
    are not in the payload, so pass them explicitly during the session)."""
    spot = chain.get("underlyingPrice") or (chain.get("underlying") or {}).get("last")
    out = {}
    for side, kind in (("putExpDateMap", "put"), ("callExpDateMap", "call")):
        for expiry, strikes in (chain.get(side) or {}).items():
            quotes = {float(K): q[0] for K, q in strikes.items() if q}
            ivs = {K: q.get("volatility") for K, q in quotes.items()
                   if isinstance(q.get("volatility"), (int, float)) and q.get("volatility") > 0}
            if not ivs:
                continue
            atm = min(ivs, key=lambda K: abs(K - spot))
            left = minutes_left if minutes_left is not None else 390
            em_rem = spot * ivs[atm] / 100 * math.sqrt(left / 525_600)  # Schwab IV is calendar time
            target = spot - k * em_rem if kind == "put" else spot + k * em_rem
            short = min(ivs, key=lambda K: abs(K - target))
            q = quotes[short]
            out.setdefault(expiry, {})[kind] = dict(
                atm_strike=atm, atm_iv_calendar_over_vix=round(ivs[atm] / vix, 3),
                atm_model_multiple=round(ivs[atm] / vix * CALENDAR_TO_TRADING, 3), short_strike=short,
                short_iv_calendar_over_vix=round(ivs[short] / vix, 3),
                short_model_multiple=round(ivs[short] / vix * CALENDAR_TO_TRADING, 3),
                bid=q.get("bid"), ask=q.get("ask"))
    return dict(spot=spot, vix=vix, k=k, expiries=out,
                note="*_model_multiple is directly comparable to the study's IV multiplier m (break-even ~0.48, gate 0.6)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="New report directory (run the study)")
    parser.add_argument("--iv-check", type=Path, help="Schwab optionChain capture: real IV vs VIX at short strikes")
    parser.add_argument("--vix", type=float, help="VIX level for --iv-check (default: latest truth-database close)")
    parser.add_argument("--minutes-left", type=int, help="Minutes to the 16:00 ET close for 0DTE --iv-check")
    args = parser.parse_args(argv)
    if args.iv_check:
        chain = json.loads(args.iv_check.read_text())["response"]
        vix = args.vix
        if vix is None:
            with closing(truth_db.connect()) as db:
                daily = truth_db.daily(db, "VIX")
                vix = daily[max(daily)]["close"]
        print(json.dumps(iv_check(chain, vix, minutes_left=args.minutes_left), indent=1))
        return 0
    if args.output is None:
        parser.error("--output or --iv-check is required")
    with closing(truth_db.connect()) as db:
        report = study(db)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    show = {k: report[k] for k in ("sessions", "primary", "primary_verdict")}
    show["controls_mean"] = {"every_day": report["every_day_control"]["all"]["mean"],
                             "high_range": report["high_range_control"]["all"]["mean"]}
    show["stressed_net"] = {k: v["all"]["net"] for k, v in report["stressed"].items()}
    print(json.dumps(show, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
