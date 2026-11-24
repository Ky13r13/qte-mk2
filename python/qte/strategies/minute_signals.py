"""Completed-minute features for short-hold research hypotheses."""
from dataclasses import dataclass
import math
from statistics import fmean, median, pstdev

FAMILIES = {'reentry': (1.25, 2.0, 3.0), 'breakout': (1.0, 1.5, 2.5),
            'daily_band': (0.75, 1.5, 2.25)}
WARMUP = 5


@dataclass(frozen=True)
class Policy:
    family: str
    threshold: float
    gated: bool


@dataclass(frozen=True)
class Minute:
    index: int
    close: float
    open: float
    bb_z: float
    previous_bb_z: float
    breakout: int
    relative_volume: float
    flow_direction: int
    daily_z: float
    previous_daily_z: float
    shock_direction: int
    high_range: bool
    annual_vol_proxy: float


def features(sessions):
    """Session and rolling statistics use only current and earlier observations."""
    previous_sessions = []
    result = {}
    for day, bars in sessions.items():
        if len(previous_sessions) >= WARMUP:
            past = previous_sessions[-WARMUP:]
            anchor = past[-1][0]
            full_range = median(row[1] for row in past)
            # A range-based scale in dollars, not an implied standard deviation.
            # The model IV proxy uses the earlier open-to-close RMS separately.
            vol = max(.10, min(.80, math.sqrt(252 * fmean(row[2] ** 2 for row in past))))
            if full_range <= 0:
                raise ValueError('Prior range scale must be positive')
            out = []
            seen_high, seen_low = bars[0].high, bars[0].low
            cumulative_value = cumulative_volume = 0.0
            last_z = 0.0
            shock = 0
            high_range = False
            for i, bar in enumerate(bars):
                cumulative_value += bar.close * bar.volume
                cumulative_volume += bar.volume
                seen_high, seen_low = max(seen_high, bar.high), min(seen_low, bar.low)
                daily_z = (bar.close-anchor) / full_range
                high_range = high_range or seen_high-seen_low >= 1.5*full_range
                if not shock and abs(daily_z) >= 1.5:
                    shock = 1 if daily_z > 0 else -1
                window = bars[max(0, i-19):i+1]
                closes = [b.close for b in window]
                sd = pstdev(closes) if i >= 19 else 0.0
                z = (bar.close-fmean(closes))/sd if sd else 0.0
                if i >= 20:
                    prior = bars[i-20:i]
                    breakout = (int(bar.close > max(b.high for b in prior)) - int(bar.close < min(b.low for b in prior)))
                    prior_volume = fmean(b.volume for b in prior)
                    vwap = cumulative_value / cumulative_volume if cumulative_volume else bar.close
                    flow = int(bar.close > vwap and bar.close > bars[0].open) - int(bar.close < vwap and bar.close < bars[0].open)
                    out.append(Minute(i, bar.close, bar.open, z, last_z, breakout,
                                      bar.volume/prior_volume if prior_volume else 0.0, flow, daily_z,
                                      (bars[i-1].close-anchor)/full_range, shock, high_range, vol))
                last_z = z
            result[day] = tuple(out)
        previous_sessions.append((bars[-1].close, max(b.high for b in bars)-min(b.low for b in bars),
                                  math.log(bars[-1].close/bars[0].open)))
    return result


def direction(bar, policy):
    level = policy.threshold
    if policy.family == 'breakout':
        sign = bar.breakout if bar.relative_volume >= level else 0
        if policy.gated and (sign != bar.flow_direction or (abs(bar.daily_z) >= 2 and sign != bar.shock_direction)):
            return 0
        return sign
    if policy.family == 'reentry':
        sign = int(bar.previous_bb_z <= -level < bar.bb_z) - int(bar.previous_bb_z >= level > bar.bb_z)
    elif policy.family == 'daily_band':
        # Inside a frozen prior-session range band after a completed excursion.
        # Confirm an improving candle with the Bollinger z-score moving inward.
        sign = (int(bar.previous_daily_z <= -level < bar.daily_z < 0 and bar.bb_z > bar.previous_bb_z)
                - int(bar.previous_daily_z >= level > bar.daily_z > 0 and bar.bb_z < bar.previous_bb_z))
    else:
        raise ValueError('Unregistered minute strategy')
    if sign*(bar.close-bar.open) <= 0:
        return 0
    if policy.gated and (sign == -bar.shock_direction or (bar.high_range and sign != bar.flow_direction)):
        return 0
    return sign
