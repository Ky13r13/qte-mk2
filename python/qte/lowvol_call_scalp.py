"""Pre-registered study: low-vol chop-up 0DTE SPX call scalp (docs/strategies/lowvol-call-scalp.md).

Observed SPY 5-minute paths scaled to SPX; option prices are *modeled* (Black-76,
IV = prior VIX x m, frozen per trade). Reads only sessions up to the locked
validation end of SPY:5m:alpaca; the holdout is never loaded.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from contextlib import closing
from datetime import datetime
import itertools
import json
import math
from pathlib import Path
import random
from statistics import fmean, pstdev
import sys
from zoneinfo import ZoneInfo

from qte import truth_db
from qte.gamma_surface import _black76
from qte.regime_study import features

NY = ZoneInfo("America/New_York")
BAR = 5
TICK = 0.05
FEE = 1.25          # per contract per side
MULT = 100
SPLIT = Path("build/search/splits/SPY_5m_alpaca.json")
DAILY_EXPIRY_START = "2022-05-11"

PRIMARY = dict(signal="R5", calm=True, target=0.50, hold=30, stop=None, iv_mult=1.0, half_spread=0.05)


def _minutes(start_ns):
    t = datetime.fromtimestamp(start_ns / 1e9, NY)
    return t.hour * 60 + t.minute


def call_mid(spot, strike, minutes_left, iv):
    if minutes_left <= 0:
        return max(spot - strike, 0.0)
    return _black76(spot, strike, iv, minutes_left / (390 * 252), 0.0, "call")["price"]


def buy_price(mid, half_spread):
    return max(TICK, math.ceil(round((mid + half_spread) / TICK, 9)) * TICK)


def sell_price(mid, half_spread):
    return max(0.0, math.floor(round((mid - half_spread) / TICK, 9)) * TICK)


# ---------------------------------------------------------------- data

def load(db, *, last_session):
    """Sessions (date -> bars) and pre-open context, sessions <= last_session only."""
    rows = db.execute(
        "SELECT session_date, start_ns, open, high, low, close FROM bars WHERE instrument='SPY' AND interval='5m'"
        " AND provider='alpaca' AND session='rth' AND quality!='provisional' AND session_date <= ?"
        " ORDER BY start_ns", (last_session,)).fetchall()
    sessions = {}
    for d, s, o, h, l, c in rows:
        sessions.setdefault(d, []).append((_minutes(s), o, h, l, c))
    spx, spy, vix = (truth_db.daily(db, symbol, end=last_session) for symbol in ("SPX", "SPY", "VIX"))
    regimes = {r["date"]: r["regime"] for r in features({"SPX": spx, "VIX": vix, "SPY": spy})}
    days = {k: sorted(v) for k, v in (("spx", spx), ("spy", spy), ("vix", vix))}
    ctx = {}
    for d in sessions:
        prior = [bisect_left(days[k], d) - 1 for k in ("spx", "spy", "vix")]
        if min(prior) < 0:
            continue
        p_spx = spx[days["spx"][prior[0]]]["close"]
        p_spy = spy[days["spy"][prior[1]]]["close"]
        p_vix = vix[days["vix"][prior[2]]]["close"]
        ctx[d] = dict(ratio=p_spx / p_spy, vix=p_vix, em=p_spx * p_vix / 100 / math.sqrt(252),
                      regime=regimes.get(d, "unclassified"))
    return sessions, ctx


# ---------------------------------------------------------------- simulation

def signals(bars, c, kind, calm):
    """Indices t whose completed bar is a signal (decision at bar t's close)."""
    out, hi, lo = [], -math.inf, math.inf
    r, em = c["ratio"], c["em"]
    for t, (m, o, h, l, cl) in enumerate(bars):
        hi, lo = max(hi, h), min(lo, l)
        close_time = m + BAR
        if not 12 * 60 <= close_time <= 15 * 60:
            continue
        if calm and (hi - lo) * r > 0.6 * em:
            continue
        if kind == "R5":
            if t < 7 or m - bars[t - 7][0] != 7 * BAR:
                continue
            if bars[t - 1][4] * r <= bars[t - 7][4] * r - 0.10 * em and cl > bars[t - 1][2]:
                out.append(t)
        elif kind == "R30":
            if (close_time - 570) % 30 or t < 11 or m - bars[t - 11][0] != 11 * BAR:
                continue
            prev, cur = bars[t - 11:t - 5], bars[t - 5:t + 1]
            if prev[-1][4] < prev[0][1] and cur[-1][4] > max(b[2] for b in prev):
                out.append(t)
        else:
            raise ValueError("signal must be R5 or R30")
    return out


def calm_bars(bars, c):
    out, hi, lo = [], -math.inf, math.inf
    for t, (m, o, h, l, cl) in enumerate(bars):
        hi, lo = max(hi, h), min(lo, l)
        if 12 * 60 <= m + BAR <= 15 * 60 and (hi - lo) * c["ratio"] <= 0.6 * c["em"]:
            out.append(t)
    return out


def trade_from(bars, c, t, p):
    """Enter at bar t+1 open; exit on 5-minute closes. Returns a trade dict or None."""
    if t + 1 >= len(bars) or bars[t + 1][0] != bars[t][0] + BAR:
        return None
    r, iv = c["ratio"], c["vix"] / 100 * p["iv_mult"]
    m0, o0 = bars[t + 1][0], bars[t + 1][1]
    spot = o0 * r
    strike = 5 * math.floor(spot / 5) + 5
    paid = buy_price(call_mid(spot, strike, 960 - m0, iv), p["half_spread"])
    for j in range(t + 1, len(bars)):
        m, _, _, _, cl = bars[j]
        close_time = m + BAR
        mid = call_mid(cl * r, strike, 960 - close_time, iv)
        reason = None
        if mid >= paid * (1 + p["target"]):
            reason = "target"
        elif p["stop"] is not None and mid <= paid * (1 - p["stop"]):
            reason = "stop"
        elif close_time - m0 >= p["hold"]:
            reason = "time"
        elif close_time >= 955 or j == len(bars) - 1:
            reason = "close"
        if reason:
            got = sell_price(mid, p["half_spread"])
            return dict(entry_min=m0, exit_min=close_time, exit_index=j, strike=strike, spot=round(spot, 2),
                        paid=paid, sold=got, pnl=round((got - paid) * MULT - 2 * FEE, 2), reason=reason)
    return None


def run(sessions, ctx, p, *, regimes=("low_vol_trend",), rng=None):
    trades = []
    for d in sorted(sessions):
        c = ctx.get(d)
        if c is None or (regimes and c["regime"] not in regimes):
            continue
        bars = sessions[d]
        if rng is None:
            candidates = signals(bars, c, p["signal"], p["calm"])
        else:
            pool = calm_bars(bars, c) if p["calm"] else [t for t, b in enumerate(bars) if 720 <= b[0] + BAR <= 900]
            candidates = sorted(rng.sample(pool, min(2, len(pool)))) if pool else []
        free_after, taken = -1, 0
        for t in candidates:
            if t <= free_after or taken >= 2:
                continue
            tr = trade_from(bars, c, t, p)
            if tr is None:
                continue
            tr["date"] = d
            trades.append(tr)
            free_after, taken = tr["exit_index"], taken + 1
    return trades


# ---------------------------------------------------------------- statistics

def stats(trades, dev_end):
    def block(ts):
        if not ts:
            return dict(trades=0, net=0.0, mean=None, t=None, win_rate=None)
        pnl = [x["pnl"] for x in ts]
        sd = pstdev(pnl) * math.sqrt(len(pnl) / (len(pnl) - 1)) if len(pnl) > 1 else 0.0
        return dict(trades=len(pnl), net=round(sum(pnl), 2), mean=round(fmean(pnl), 2),
                    t=round(fmean(pnl) / (sd / math.sqrt(len(pnl))), 2) if sd > 0 else None,
                    win_rate=round(sum(x > 0 for x in pnl) / len(pnl), 3))
    by_year = {}
    for x in trades:
        by_year[x["date"][:4]] = by_year.get(x["date"][:4], 0.0) + x["pnl"]
    days = {}
    for x in trades:
        days[x["date"]] = days.get(x["date"], 0.0) + x["pnl"]
    streak = worst_streak = 0
    for x in trades:
        streak = streak + 1 if x["pnl"] <= 0 else 0
        worst_streak = max(worst_streak, streak)
    equity = peak = dd = 0.0
    for d in sorted(days):
        equity += days[d]
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dict(
        all=block(trades),
        dev=block([x for x in trades if x["date"] <= dev_end]),
        validation=block([x for x in trades if x["date"] > dev_end]),
        daily_expiry_era=block([x for x in trades if x["date"] >= DAILY_EXPIRY_START]),
        by_year={k: round(v) for k, v in sorted(by_year.items())},
        years_profitable=round(sum(v > 0 for v in by_year.values()) / len(by_year), 2) if by_year else None,
        worst_trade=min((x["pnl"] for x in trades), default=None),
        worst_day=round(min(days.values()), 2) if days else None,
        max_losing_streak=worst_streak, max_drawdown=round(dd, 2),
        exits={k: sum(x["reason"] == k for x in trades) for k in ("target", "stop", "time", "close")},
        mean_paid=round(fmean(x["paid"] for x in trades), 2) if trades else None,
    )


def verdict(primary, random_mean, stressed):
    s = primary
    checks = {
        "positive_dev_and_validation": s["dev"]["net"] > 0 and s["validation"]["net"] > 0,
        "dev_trades_100_and_t_2": s["dev"]["trades"] >= 100 and (s["dev"]["t"] or 0) >= 2,
        "years_profitable_60pct": (s["years_profitable"] or 0) >= 0.6,
        "beats_random_entry": s["all"]["mean"] is not None and random_mean is not None and s["all"]["mean"] > random_mean,
        "survives_wider_spread_or_higher_iv": any(x["all"]["net"] > 0 for x in stressed.values()),
    }
    return dict(checks=checks, passed=all(checks.values()))


def study(db, *, seeds=20):
    split = json.loads(SPLIT.read_text())
    sessions, ctx = load(db, last_session=split["validation_end"])
    if max(sessions) > split["validation_end"]:
        raise RuntimeError("holdout session loaded")  # defensive; the query already bounds it
    dev_end = split["dev_end"]
    primary_trades = run(sessions, ctx, PRIMARY)
    primary = stats(primary_trades, dev_end)
    grid = []
    for sig, calm, target, hold, stop in itertools.product(("R5", "R30"), (True, False), (0.25, 0.5, 1.0),
                                                          (15, 30, 60), (None, 0.5)):
        p = dict(PRIMARY, signal=sig, calm=calm, target=target, hold=hold, stop=stop)
        s = stats(run(sessions, ctx, p), dev_end)
        grid.append(dict(params={k: p[k] for k in ("signal", "calm", "target", "hold", "stop")},
                         all=s["all"], dev=s["dev"], validation=s["validation"], years_profitable=s["years_profitable"]))
    stressed = {name: stats(run(sessions, ctx, dict(PRIMARY, **chg)), dev_end) for name, chg in
                (("half_spread_0.10", {"half_spread": 0.10}), ("iv_x1.2", {"iv_mult": 1.2}),
                 ("iv_x0.8", {"iv_mult": 0.8}))}
    all_regimes = stats(run(sessions, ctx, PRIMARY, regimes=None), dev_end)
    random_means = []
    for seed in range(seeds):
        rt = run(sessions, ctx, PRIMARY, rng=random.Random(20261124 + seed))
        if rt:
            random_means.append(fmean(x["pnl"] for x in rt))
    random_mean = round(fmean(random_means), 2) if random_means else None
    qualifying = sum(1 for d in sessions if ctx.get(d, {}).get("regime") == "low_vol_trend")
    return dict(
        schema="qte-lowvol-call-scalp-v1", sessions=len(sessions), first=min(sessions), last=max(sessions),
        dev_end=dev_end, low_vol_trend_sessions=qualifying, primary_spec=PRIMARY, primary=primary,
        primary_verdict=verdict(primary, random_mean, {k: v for k, v in stressed.items() if k != "iv_x0.8"}),
        stressed=stressed, all_regimes_control=all_regimes, random_entry_control_mean_per_trade=random_mean,
        grid=grid, primary_trades=primary_trades,
        caveats=["Option prices are modeled (Black-76, IV = prior VIX x m, frozen per trade), not quoted.",
                 "Daily SPXW expiries exist only since 2022; earlier years assume a 0DTE contract existed.",
                 "SPY bars are scaled to SPX by the prior-day close ratio."])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True, help="New report directory")
    args = parser.parse_args(argv)
    with closing(truth_db.connect()) as db:
        report = study(db)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps({k: report[k] for k in ("sessions", "first", "last", "low_vol_trend_sessions",
                                             "primary", "primary_verdict", "random_entry_control_mean_per_trade")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
