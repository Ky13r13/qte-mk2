"""Pre-registered study: opening-range breakout traded with BTO 0DTE SPXW calls/puts.

See docs/strategies/orb-bto-options.md. Observed SPY 5-minute paths scaled to SPX;
option prices modeled with Black-76 at IV = prior VIX x m (study time units).
Sessions end at the locked validation end; the holdout is never loaded.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
import itertools
import json
import math
from pathlib import Path
import random
from statistics import fmean
import sys

from qte import truth_db
from qte.gamma_surface import _black76
from qte.lowvol_call_scalp import SPLIT, _minutes, load, stats

FEE, MULT = 1.25, 100
PRIMARY = dict(range_bars=2, max_range_em=0.4, volume_ratio=2.0, lookback=20, last_entry=48,
               strike="atm", target=None, time_stop=None, iv_mult=0.60, half_spread=0.05)


def tick(price):
    return 0.10 if price >= 3.0 else 0.05


def buy_fill(mid, h):
    p = mid + h
    t = tick(p)
    return max(0.05, math.ceil(round(p / t, 9)) * t)


def sell_fill(mid, h):
    p = max(mid - h, 0.0)
    t = tick(p)
    return max(0.0, math.floor(round(p / t, 9)) * t)


def option_mid(spot, strike, minutes_left, iv, kind):
    if minutes_left <= 0:
        return max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)
    return _black76(spot, strike, iv, minutes_left / (390 * 252), 0.0, kind)["price"]


def load_volume(db, last_session):
    rows = db.execute("SELECT session_date, start_ns, volume FROM bars WHERE instrument='SPY' AND interval='5m'"
                      " AND provider='alpaca' AND session='rth' AND quality!='provisional' AND session_date <= ?",
                      (last_session,)).fetchall()
    return {(d, _minutes(s)): v or 0.0 for d, s, v in rows}


def signal(bars, c, volume, baseline, p):
    """Return (bar index t, side) for the first qualifying breakout, or None. Uses completed bars only."""
    by_min = {b[0]: i for i, b in enumerate(bars)}
    opening = [by_min.get(570 + 5 * k) for k in range(p["range_bars"])]
    if None in opening:
        return None
    hi = max(bars[i][2] for i in opening)
    lo = min(bars[i][3] for i in opening)
    if (hi - lo) * c["ratio"] >= p["max_range_em"] * c["em"]:
        return None
    for t, (m, o, h, l, cl) in enumerate(bars):
        slot = (m - 570) // 5
        if slot < p["range_bars"] or slot > p["last_entry"]:
            continue
        side = 1 if cl > hi else -1 if cl < lo else 0
        if not side:
            continue
        hist = baseline.get(m)
        if not hist or len(hist) < p["lookback"]:
            continue
        base = fmean(hist)
        if base > 0 and volume.get(m, 0.0) / base > p["volume_ratio"]:
            return t, side, hi, lo
    return None


def trade(bars, c, t, side, hi, lo, p):
    if t + 1 >= len(bars) or bars[t + 1][0] != bars[t][0] + 5:
        return None
    r, iv = c["ratio"], c["vix"] / 100 * p["iv_mult"]
    kind = "call" if side > 0 else "put"
    m0 = bars[t + 1][0]
    spot = bars[t + 1][1] * r
    if p["strike"] == "atm":
        strike = 5 * round(spot / 5)
    else:
        strike = 5 * math.floor(spot / 5) + 5 if side > 0 else 5 * math.ceil(spot / 5) - 5
    paid = buy_fill(option_mid(spot, strike, 960 - m0, iv, kind), p["half_spread"])
    mid_level = (hi + lo) / 2 * r
    for j in range(t + 1, len(bars)):
        m, _, _, _, cl = bars[j]
        close_time = m + 5
        s = cl * r
        mid = option_mid(s, strike, 960 - close_time, iv, kind)
        reason = None
        if (side > 0 and s < mid_level) or (side < 0 and s > mid_level):
            reason = "stop"
        elif p["target"] is not None and mid >= paid * (1 + p["target"]):
            reason = "target"
        elif p["time_stop"] is not None and close_time - m0 >= p["time_stop"]:
            reason = "time"
        elif close_time >= 955 or j == len(bars) - 1:
            reason = "close"
        if reason:
            got = sell_fill(mid, p["half_spread"])
            return dict(entry_min=m0, exit_min=close_time, side=side, strike=strike, spot=round(spot, 2),
                        paid=round(paid, 2), sold=round(got, 2), reason=reason,
                        pnl=round((got - paid) * MULT - 2 * FEE, 2), points=round(side * (s - spot), 2))
    return None


def run(sessions, ctx, volumes, p, *, coin=None):
    trades, baseline = [], {}
    for d in sorted(sessions):
        bars = sessions[d]
        c = ctx.get(d)
        today = {b[0]: volumes.get((d, b[0]), 0.0) for b in bars}
        if c is not None:
            found = signal(bars, c, today, baseline, p)
            if found:
                t, side, hi, lo = found
                if coin is not None:
                    side = coin.choice((1, -1))
                tr = trade(bars, c, t, side, hi, lo, p)
                if tr:
                    tr["date"] = d
                    trades.append(tr)
        for m, v in today.items():  # update the volume baseline only after today's decisions
            baseline.setdefault(m, deque(maxlen=p["lookback"])).append(v)
    return trades


def verdict(s, coin_mean, stressed):
    checks = {
        "positive_dev_and_validation": s["dev"]["net"] > 0 and s["validation"]["net"] > 0,
        "dev_trades_100_and_t_2": s["dev"]["trades"] >= 100 and (s["dev"]["t"] or 0) >= 2,
        "years_profitable_60pct": (s["years_profitable"] or 0) >= 0.6,
        "beats_coin_flip": s["all"]["mean"] is not None and coin_mean is not None and s["all"]["mean"] > coin_mean,
        "survives_iv_0.66_and_spread_0.10": stressed["iv_0.66"]["all"]["net"] > 0
        and stressed["half_spread_0.10"]["all"]["net"] > 0,
    }
    return dict(checks=checks, passed=all(checks.values()))


def study(db, *, seeds=20):
    split = json.loads(SPLIT.read_text())
    sessions, ctx = load(db, last_session=split["validation_end"])
    volumes = load_volume(db, split["validation_end"])
    dev_end = split["dev_end"]
    primary_trades = run(sessions, ctx, volumes, PRIMARY)
    primary = stats(primary_trades, dev_end)
    points = [t["points"] for t in primary_trades]
    stressed = {name: stats(run(sessions, ctx, volumes, dict(PRIMARY, **chg)), dev_end) for name, chg in
                (("iv_0.54", {"iv_mult": 0.54}), ("iv_0.66", {"iv_mult": 0.66}), ("iv_1.0", {"iv_mult": 1.0}),
                 ("half_spread_0.10", {"half_spread": 0.10}))}
    coin_means = [fmean(x["pnl"] for x in tr) for tr in
                  (run(sessions, ctx, volumes, PRIMARY, coin=random.Random(20261124 + k)) for k in range(seeds)) if tr]
    coin_mean = round(fmean(coin_means), 2) if coin_means else None
    grid = []
    for strike, exit_rule, last in itertools.product(("atm", "otm"), ("hold", "target_100", "time_60"), (48, 24)):
        p = dict(PRIMARY, strike=strike, last_entry=last, target=1.0 if exit_rule == "target_100" else None,
                 time_stop=60 if exit_rule == "time_60" else None)
        s = stats(run(sessions, ctx, volumes, p), dev_end)
        grid.append(dict(params=dict(strike=strike, exit=exit_rule, last_entry=last), all=s["all"], dev=s["dev"],
                         validation=s["validation"], years_profitable=s["years_profitable"]))
    return dict(schema="qte-orb-bto-options-v1", sessions=len(sessions), first=min(sessions), last=max(sessions),
                dev_end=dev_end, primary_spec=PRIMARY, primary=primary,
                primary_verdict=verdict(primary, coin_mean, stressed), stressed=stressed,
                coin_flip_control_mean_per_trade=coin_mean,
                underlying_points=dict(trades=len(points), mean=round(fmean(points), 2) if points else None,
                                       win_rate=round(sum(x > 0 for x in points) / len(points), 3) if points else None),
                grid=grid, primary_trades=primary_trades,
                caveats=["Option prices are modeled (Black-76, IV = prior VIX x m, frozen per trade), not quoted.",
                         "Daily SPXW expiries exist only since 2022; earlier years assume a 0DTE contract existed.",
                         "SPY bars scaled to SPX by the prior-day close ratio; SPY volume is the confirmation proxy."])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True, help="New report directory")
    args = parser.parse_args(argv)
    with closing(truth_db.connect()) as db:
        report = study(db)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    show = {k: report[k] for k in ("sessions", "primary", "primary_verdict", "coin_flip_control_mean_per_trade",
                                   "underlying_points")}
    show["stressed_mean"] = {k: v["all"]["mean"] for k, v in report["stressed"].items()}
    print(json.dumps(show, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
