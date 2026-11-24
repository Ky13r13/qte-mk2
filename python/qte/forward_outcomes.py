"""Causal event selection and descriptive forward moves for frozen reversal rules.

This is an underlying event study, not executable SPX/option portfolio returns.
"""
from collections import Counter, defaultdict
from datetime import datetime
import math
from statistics import fmean

from .reversal_tail import NY, reversal_signal, session_bars

HORIZONS = (1, 5, 15, 30)
MIN_CONTROL_SESSIONS = 3


def cutoff(timestamp):
    return int(datetime.fromtimestamp(timestamp, NY).replace(
        hour=15, minute=59, second=0, microsecond=0).timestamp())


def clock_bucket(timestamp):
    local = datetime.fromtimestamp(timestamp, NY)
    return (local.hour*60+local.minute-570)//30


def validate_session(daily, bars):
    if daily["asof"] >= daily["date"]:
        raise ValueError("Daily features must precede the observed session")
    for key in ("anchor", "expected_move", "vix"):
        if not math.isfinite(daily[key]) or daily[key] <= 0:
            raise ValueError("Positive finite daily features required")
    previous = None
    for bar in bars:
        ts = bar["time"]
        if type(ts) is not int or ts % 60 or (previous is not None and ts <= previous):
            raise ValueError("Unique increasing whole-minute availability times required")
        local = datetime.fromtimestamp(ts, NY)
        minute = local.hour*60+local.minute
        if local.date().isoformat() != daily["date"] or not 571 <= minute <= 960:
            raise ValueError("Bar availability outside its declared regular session")
        o, h, l, c = (bar[k] for k in ("open", "high", "low", "close"))
        if any(not math.isfinite(x) or x <= 0 for x in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError("Invalid observed OHLC")
        previous = ts


def forward_move(by_time, signal_time, horizon):
    if horizon not in HORIZONS:
        raise ValueError("Unsupported fixed horizon")
    entry_time, exit_time = signal_time+60, signal_time+(horizon+1)*60
    result = {"horizon_minutes": horizon, "entry_time": entry_time, "exit_time": exit_time}
    if exit_time > cutoff(signal_time):
        return {**result, "status": "outside_cutoff"}
    if entry_time not in by_time:
        return {**result, "status": "missing_entry"}
    if exit_time not in by_time:
        return {**result, "status": "missing_exit"}
    if any(t not in by_time for t in range(entry_time, exit_time+1, 60)):
        return {**result, "status": "missing_path"}
    entry = by_time[entry_time]["close"]
    points = [by_time[t]["close"]-entry for t in range(entry_time, exit_time+1, 60)]
    return {**result, "status": "observed", "entry_spot": entry,
            "exit_spot": by_time[exit_time]["close"], "points": points[-1],
            "return_bps": points[-1]/entry*10000,
            "close_mae_bps": min(points)/entry*10000,
            "close_mfe_bps": max(points)/entry*10000}


def selected_events(daily, bars):
    """Same signal and five-minute cooldown for every evaluation horizon.

    Reserve an intent even if a future entry is missing; future availability must
    not change later signal selection. No holdings or fills are created here.
    """
    events = []
    for bound in (1, 2):
        for veto in (True, False):
            count, available = 0, 0
            for i, bar in enumerate(bars):
                ts = bar["time"]
                if i < 20 or ts < available or count >= 5 or ts+360 > cutoff(ts):
                    continue
                if not reversal_signal(bars[i-1], bar, bars[i-4:i+1], daily, bound):
                    continue
                if veto and (bar["stress"] or daily["regime"] in ("stress_selloff", "high_vol_down")):
                    continue
                count += 1
                available = ts+360
                events.append({"event_id": f"{daily['date']}:{ts}:{bound}:{int(veto)}",
                               "date": daily["date"], "bound": bound, "veto": veto,
                               "signal_time": ts, "regime": bar["regime"],
                               "daily_regime": daily["regime"], "bucket": clock_bucket(ts)})
    return sorted(events, key=lambda e: (e["signal_time"], e["bound"], e["veto"]))


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": None, "median": None, "p10": None, "p90": None,
                "positive_fraction": None}
    def percentile(q):
        position = q*(len(ordered)-1)
        left, right = math.floor(position), math.ceil(position)
        return ordered[left]+(ordered[right]-ordered[left])*(position-left)
    return {"count": len(ordered), "mean": fmean(ordered), "median": percentile(.5),
            "p10": percentile(.1), "p90": percentile(.9),
            "positive_fraction": sum(v > 0 for v in ordered)/len(ordered)}


def session_distribution(rows, field):
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row[field])
    return distribution([fmean(v) for _, v in sorted(by_date.items())])


def summarize(outcomes):
    result = []
    for bound in (1, 2):
        for veto in (True, False):
            for horizon in HORIZONS:
                candidates = [r for r in outcomes if (r["bound"], r["veto"], r["horizon_minutes"]) ==
                              (bound, veto, horizon)]
                for regime in ["all", *sorted({r["regime"] for r in candidates})]:
                    rows = [r for r in candidates if regime == "all" or r["regime"] == regime]
                    valid = [r for r in rows if r["status"] == "observed"]
                    paired = [r for r in valid if r["baseline"]["status"] == "matched"]
                    result.append({"bound": bound, "veto": veto, "horizon_minutes": horizon,
                                   "regime": regime, "signals": len(rows),
                                   "signal_sessions": len({r["date"] for r in rows}),
                                   "status_counts": dict(Counter(r["status"] for r in rows)),
                                   "baseline_status_counts": dict(Counter(r["baseline"]["status"] for r in rows)),
                                   "event_return_bps": distribution([r["return_bps"] for r in valid]),
                                   "session_return_bps": session_distribution(valid, "return_bps"),
                                   "paired_events": len(paired),
                                   "paired_session_return_bps": session_distribution(paired, "return_bps"),
                                   "paired_session_excess_bps": session_distribution(paired, "excess_bps")})
    return result


def study(rows, minutes):
    daily_by_date = {r["date"]: r for r in rows}
    if len(daily_by_date) != len(rows):
        raise ValueError("Duplicate daily feature date")
    history = defaultdict(list)
    outcomes, controls, coverage = [], [], []
    for day, raw in sorted(minutes.items()):
        daily = daily_by_date.get(day)
        if not raw or not daily or daily["regime"] == "unclassified":
            coverage.append({"date": day, "status": "no_bars_or_prior_features"})
            continue
        validate_session(daily, raw)
        bars = session_bars(daily, raw)
        by_time = {b["time"]: b for b in bars}
        events = selected_events(daily, bars)
        coverage.append({"date": day, "status": "included", "bars": len(bars), "signals": len(events)})
        for event in events:
            for horizon in HORIZONS:
                move = forward_move(by_time, event["signal_time"], horizon)
                key = (event["regime"], event["bucket"], horizon)
                prior = history[key]
                baseline = {"status": "matched" if len(prior) >= MIN_CONTROL_SESSIONS else "insufficient_prior_sessions",
                            "sessions": len(prior), "observations": sum(r["observations"] for r in prior),
                            "dates": [r["date"] for r in prior],
                            "mean_bps": fmean(r["mean_bps"] for r in prior) if len(prior) >= MIN_CONTROL_SESSIONS else None}
                outcome = {**event, **move, "baseline": baseline}
                if move["status"] == "observed" and baseline["status"] == "matched":
                    outcome["excess_bps"] = move["return_bps"]-baseline["mean_bps"]
                outcomes.append(outcome)
        # Publish control outcomes only after the session: no same/future-day
        # outcomes can enter a target's reference. Each prior session gets one vote.
        daily_controls = defaultdict(list)
        for i, bar in enumerate(bars):
            if i < 20 or bars[i-1]["bb_lower"] is None or bar["bb_lower"] is None or bar["time"]+360 > cutoff(bar["time"]):
                continue
            for horizon in HORIZONS:
                move = forward_move(by_time, bar["time"], horizon)
                if move["status"] == "observed":
                    daily_controls[(bar["regime"], clock_bucket(bar["time"]), horizon)].append(move["return_bps"])
        for (regime, bucket, horizon), values in sorted(daily_controls.items()):
            control = {"date": day, "regime": regime, "bucket": bucket, "horizon_minutes": horizon,
                       "observations": len(values), "mean_bps": fmean(values)}
            controls.append(control)
            history[(regime, bucket, horizon)].append(control)
    return {"summary": summarize(outcomes), "outcomes": outcomes,
            "baseline_sessions": controls, "coverage": coverage}
