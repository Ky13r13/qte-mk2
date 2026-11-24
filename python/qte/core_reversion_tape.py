"""Point-in-time regime and catalyst gates shared by the two CORE studies."""
from .s7_tape import parse_session
from .spx_spreads import _fields, _ns
from .volatility import MINUTE_NS as M


def parse_day(raw, day):
    _fields(raw, ("snapshots", "regimes", "settlement", "calendar"), "CORE session")
    tape = parse_session({k: raw[k] for k in ("snapshots", "regimes", "settlement")}, day, "SPX")
    calendar = raw["calendar"]
    if not isinstance(calendar, list):
        raise ValueError("Expected calendar releases as an array")
    last = None
    for row in calendar:
        _fields(row, ("available_ns", "event_day", "top10_catalyst", "macro_times_ns"), "calendar declaration")
        available = _ns(row["available_ns"])
        if available > day.close_ns or (last is not None and available <= last):
            raise ValueError("Calendar declarations must be in receipt order for the named session")
        for key in ("event_day", "top10_catalyst"):
            if row[key] is not None and type(row[key]) is not bool:
                raise ValueError("Expected true, false or unknown calendar state")
        times = row["macro_times_ns"]
        if times is not None:
            if type(times) is not list:
                raise ValueError("Expected scheduled macro release times or null")
            for t in times:
                _ns(t)
        last = available
    return tape, calendar


def gates(strategy, day, at, tape, calendar):
    reasons = []
    quarter = 15*M
    bucket = day.open_ns + (at-day.open_ns)//quarter*quarter
    regime = tape.regimes.get(bucket) if tape else None
    if regime is None or regime["available_ns"] > at:
        reasons.append("missing_regime")
    else:
        if regime["gamma"] not in ("positive", "estimated_positive"):
            reasons.append("positive_gamma_unconfirmed")
        if regime["vix"] is None or regime["vix"] > (22 if strategy == "S1" else 30):
            reasons.append("vix_ineligible_or_missing")
    cal = next((r for r in reversed(calendar) if r["available_ns"] <= at), None)
    if cal is None:
        reasons.append("missing_catalyst_calendar")
    else:
        if cal["event_day"] is not False:
            reasons.append("event_day_or_unknown")
        if strategy == "S2" and cal["top10_catalyst"] is not False:
            reasons.append("top10_catalyst_or_unknown")
        times = cal["macro_times_ns"]
        if times is None or any(t-10*M <= at <= t+15*M for t in times):
            reasons.append("macro_blackout_or_unknown")
    return reasons
