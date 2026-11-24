"""Causal minute observations for the SPX VWAP reversion and opening gap fade studies."""
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from statistics import pstdev

from qte.volatility import MINUTE_NS as M


@dataclass(frozen=True)
class Minute:
    at: int
    high: float
    low: float
    close: float
    vwap: float | None = None
    z: float | None = None


@dataclass(frozen=True)
class VwapRules:
    lookback: int = 30
    entry_z: float = 2.
    stop_z: float = 3.

    def __post_init__(self):
        if (type(self.lookback) is not int or not 2 <= self.lookback <= 300
                or not all(isfinite(v) for v in (self.entry_z, self.stop_z))
                or not 0 < self.entry_z < self.stop_z):
            raise ValueError("S1 requires a 2–300 minute lookback and 0 < entry z < stop z")


def observations(day, bars, volumes, strategy, rules=VwapRules()):
    """Stop at the first missing required minute. VWAP starts at the cash open."""
    items = {}
    deviations = deque(maxlen=rules.lookback)
    total = weighted = 0.
    for start in range(day.open_ns, day.close_ns, M):
        if start not in bars or (strategy == "S1" and start not in volumes):
            break
        _, high, low, close = bars[start]
        vwap = z = None
        if strategy == "S1":
            volume = volumes[start]
            weighted += (high + low + close) / 3 * volume
            total += volume
            if total:
                vwap = weighted / total
                deviations.append(close - vwap)
                if len(deviations) == deviations.maxlen:
                    sigma = pstdev(deviations)
                    if sigma > 0:
                        z = deviations[-1] / sigma
        at = start + M
        items[at] = Minute(at, high, low, close, vwap, z)
    return items


def vwap_setups(day, minutes, rules=VwapRules()):
    candidates = []
    for at, bar in minutes.items():
        if not day.open_ns+30*M <= at <= min(day.open_ns+300*M, day.close_ns-15*M):
            continue
        prev = minutes[at-M]
        if (prev.z is None or abs(prev.z) < rules.entry_z or bar.z is None
                or not rules.entry_z <= abs(bar.z) < rules.stop_z):
            continue
        diff = bar.close - bar.vwap
        old = prev.close - prev.vwap
        if (diff * old > 0 and abs(diff) < abs(old)
                and (bar.close - prev.close) * diff < 0):
            candidates.append(dict(at_ns=at, spot=bar.close, right="C" if diff < 0 else "P",
                                   target=bar.vwap, z=bar.z, stop_z=rules.stop_z))
    return candidates


def gap_setup(day, minutes, opening, previous_close):
    at = day.open_ns + 15*M
    if at not in minutes:
        return [], {"status": "missing_opening_minutes"}
    if previous_close is None:
        return [], {"status": "missing_prior_session_close"}
    gap = Decimal(str(opening)) / Decimal(str(previous_close)) - 1
    target = (opening + previous_close) / 2
    bars = [minutes[t] for t in range(day.open_ns+M, at+1, M)]
    high, low = max(b.high for b in bars), min(b.low for b in bars)
    facts = dict(gap_pct=float(100*gap), opening=opening, previous_close=previous_close,
                 opening_high=high, opening_low=low, target=target)
    if not Decimal(".003") <= abs(gap) <= Decimal(".01"):
        return [], dict(facts, status="outside_gap_bounds")
    up = gap > 0
    if (high > bars[0].high if up else low < bars[0].low):
        return [], dict(facts, status="extended_opening_extreme")
    if (low <= target if up else high >= target):
        return [], dict(facts, status="target_already_touched")
    close = bars[-1].close
    if (close >= opening if up else close <= opening):
        return [], dict(facts, status="no_fade_confirmation")
    return [dict(at_ns=at, spot=close, right="P" if up else "C", target=target,
                 stop=high if up else low)], dict(facts, status="price_setup")


def underlying_exit(strategy, signal, bar, previous, full_bar):
    """Completed bar decisions. A partially owned first minute contributes only its close."""
    call = signal["right"] == "C"
    high, low = (bar.high, bar.low) if full_bar else (bar.close, bar.close)
    if strategy == "S1":
        if bar.z is not None and abs(bar.z) >= signal["stop_z"]:
            return "z_stop"
        target = previous.vwap
    else:
        if (low < signal["stop"] if call else high > signal["stop"]):
            return "opening_extreme_stop"
        target = signal["target"]
    if target is not None and (high >= target if call else low <= target):
        return "vwap_touch" if strategy == "S1" else "half_gap_fill"
    return None
