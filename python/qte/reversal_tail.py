"""Bounded reversal replay and retrospective 0DTE tail diagnostics.

Observed SPX candles, hypothetical derivative marks. No broker/execution claims.
"""
from collections import Counter
from datetime import datetime
import math
from statistics import fmean, pstdev
from zoneinfo import ZoneInfo

from .regime_study import _price, option_trade

NY = ZoneInfo("America/New_York")


def bands(bars):
    out = []
    for i, bar in enumerate(bars):
        recent = bars[max(0, i-19):i+1]
        valid = len(recent) == 20 and recent[-1]["time"]-recent[0]["time"] == 19*60
        mean = fmean(b["close"] for b in recent) if valid else None
        sd = pstdev(b["close"] for b in recent) if valid else None
        out.append(dict(bar, bb_lower=None if mean is None else mean-2*sd,
                        bb_upper=None if mean is None else mean+2*sd))
    return out


def reversal_signal(previous, current, recent, daily, bound):
    """Close re-enters after a lower-band close; within .25 daily sigma of support."""
    if previous["bb_lower"] is None or current["bb_lower"] is None:
        return False
    level = daily["anchor"]-bound*daily["expected_move"]
    return (previous["close"] < previous["bb_lower"]
            and current["close"] >= current["bb_lower"]
            and current["close"] > current["open"]
            and min(b["low"] for b in recent) <= level
            and abs(current["close"]-level) <= .25*daily["expected_move"])


def session_bars(daily, bars):
    out, stress = [], False
    for bar in bands(bars):
        stress |= bar["close"] <= daily["anchor"]-2.5*daily["expected_move"] or (
            bar["close"] <= daily["lower2"] and daily["vix"] >= 25)
        out.append(dict(bar, stress=stress, regime="stress_selloff" if stress else daily["regime"]))
    return out


def reversals(rows, minutes):
    trades, skipped = [], Counter()
    for daily in rows:
        raw = minutes.get(daily["date"], [])
        if daily["regime"] == "unclassified" or not raw:
            continue
        bars = session_bars(daily, raw)
        by_time = {b["time"]: b for b in bars}
        for bound in (1, 2):
            for veto in (True, False):
                count, available = 0, 0
                for i, b in enumerate(bars):
                    if i < 20 or b["time"] < available or count >= 5:
                        continue
                    if not reversal_signal(bars[i-1], b, bars[i-4:i+1], daily, bound):
                        continue
                    if veto and (b["stress"] or daily["regime"] in ("stress_selloff", "high_vol_down")):
                        skipped[f"{bound}sigma_veto"] += 1
                        continue
                    entry_time, exit_time = b["time"]+60, b["time"]+360
                    # Do not schedule a five-minute hold beyond 15:59 ET.
                    local = datetime.fromtimestamp(exit_time, NY)
                    if local.hour*60+local.minute > 15*60+59:
                        continue
                    entry = by_time.get(entry_time)
                    if entry is None:
                        skipped["missing_entry"] += 1
                        continue
                    ending = by_time.get(exit_time)
                    count += 1
                    available = exit_time
                    for scenario in ("base_frozen_iv", "wide_frozen_iv"):
                        model = option_trade(entry["close"], ending["close"] if ending else entry["close"],
                                             daily["vix"]/100, daily["vix"]/100, 1, "long", 5/60, scenario)
                        if ending is None:
                            model["pnl"] = -model["max_loss"]
                            model["exit_value"] = None
                        points = ending["close"]-entry["close"] if ending else None
                        trades.append({**model, "date": daily["date"], "regime": daily["regime"],
                                       "bound": bound, "veto": veto, "scenario": scenario,
                                       "signal_time": b["time"], "entry_time": entry_time,
                                       "exit_time": exit_time, "entry_spot": entry["close"],
                                       "exit_spot": ending["close"] if ending else None,
                                       "points": points, "missing_exit": ending is None,
                                       # Arithmetic translation only, not a futures fill or margin model.
                                       "mes_illustration": None if points is None else round(points*5-5, 2)})
    return trades, dict(skipped)


def put_mark(spot, strike, iv, remaining_minutes):
    if min(spot, strike, iv, remaining_minutes) <= 0:
        raise ValueError("Positive option inputs required")
    # Explicit variance clock: one RTH session = 1/252 year. Not a calibrated smile.
    return _price(spot, strike, remaining_minutes/(252*390), iv, "put")["price"]


def entry_cost(mid):
    tick = .05 if mid+.10 < 3 else .10
    return round(math.ceil((mid+.10)/tick-1e-10)*tick*100+1.25, 2)


def exit_proceeds(mid):
    price = max(0, mid-.10)
    tick = .05 if price < 3 else .10
    return round(math.floor(price/tick+1e-10)*tick*100-1.25, 2)


def tail_candidates(rows, minutes, days):
    """Enumerate all fixed entry/OTM-distance choices, including losers.

    The best future exit is an explicitly hindsight diagnostic. A separate first
    10x trigger exits at the next observed complete minute, never at the peak.
    """
    daily_by_date = {r["date"]: r for r in rows}
    out, excluded = [], Counter()
    for day in days:
        daily, bars = daily_by_date.get(day), minutes.get(day, [])
        if not daily or daily["regime"] == "unclassified":
            excluded["no_prior_features"] += 1
            continue
        eligible = {b["time"]: b for b in bars if (lambda t: 15*60 <= t.hour*60+t.minute <= 15*60+59)(datetime.fromtimestamp(b["time"], NY))}
        if len(eligible) != 60 or max(eligible)-min(eligible) != 59*60:
            excluded["incomplete_last_hour"] += 1
            continue
        session_end = max(eligible)+60
        for entry_time in range(min(eligible), min(eligible)+51*60, 5*60):
            entry = eligible[entry_time]
            remaining = (session_end-entry_time)/60
            # Strike chosen once from contemporaneous spot and prior daily VIX.
            expected = entry["close"]*daily["vix"]/100*math.sqrt(remaining/(252*390))
            strikes = sorted({5*math.floor((entry["close"]-z*expected)/5) for z in (.5, 1, 1.5, 2)})
            for strike in strikes:
                for iv_scale in (.75, 1.0, 1.25, 2.0, 3.0):
                    iv = daily["vix"]/100*iv_scale
                    mid = put_mark(entry["close"], strike, iv, remaining)
                    if mid < .10:
                        excluded["entry_mid_below_010"] += 1
                        continue
                    cost = entry_cost(mid)
                    marks = [(t, exit_proceeds(put_mark(b["close"], strike, iv, (session_end-t)/60)))
                             for t, b in eligible.items() if t > entry_time]
                    peak_time, peak = max(marks, key=lambda p: p[1])
                    trigger = next((t for t, proceeds in marks if proceeds/cost >= 10), None)
                    target_exit = next(((t, p) for t, p in marks if trigger is not None and t == trigger+60), None)
                    out.append({"date": day, "regime": daily["regime"], "entry_time": entry_time,
                                "entry_spot": entry["close"], "strike": strike, "iv_scale": iv_scale,
                                "entry_iv": iv, "entry_cost": cost, "peak_time": peak_time,
                                "peak_ratio": peak/cost, "close_ratio": marks[-1][1]/cost,
                                "close_pnl": round(marks[-1][1]-cost, 2),
                                "target_trigger": trigger, "target_exit_time": target_exit[0] if target_exit else None,
                                "target_ratio": target_exit[1]/cost if target_exit else None,
                                "exit_spot": eligible[peak_time]["close"],
                                "reason": "OTM debit becomes intrinsic value during a late decline; peak selected after the fact. IV is assumed, not observed."})
    return out, dict(excluded)
