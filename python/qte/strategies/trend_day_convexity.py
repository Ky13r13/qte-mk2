"""S7 opening-range signal. Inputs contain completed cash-session candles only."""
from statistics import fmean

from qte.s7_tape import straddle_move, regime_at
from qte.volatility import MINUTE_NS

FIVE = 5 * MINUTE_NS
LOOKBACK = 20


def five_minutes(minutes, opening, closing, aggregate):
    """Key a slot by elapsed minutes at its right edge, independent of DST."""
    result = {}
    for start in range(opening, closing, FIVE):
        keys = range(start, start + FIVE, MINUTE_NS)
        if all(key in minutes for key in keys):
            result[(start + FIVE - opening) // MINUTE_NS] = aggregate([minutes[k] for k in keys])
    return result


def index_window(rows):
    return max(r[0] for r in rows), min(r[1] for r in rows), rows[-1][2]


def screen(day, index, volume, history, tape):
    row = dict(date=day.trading_date, breakouts=[], signal=None)
    if any(slot not in index for slot in range(5, 31, 5)):
        return dict(row, status="incomplete_opening_range")
    high = max(index[s][0] for s in range(5, 31, 5))
    low = min(index[s][1] for s in range(5, 31, 5))
    formed = day.open_ns + 30 * MINUTE_NS
    move = straddle_move(tape, formed, index[30][2])
    row.update(range_high=high, range_low=low, midpoint=(high+low)/2, implied_points=move)
    cutoff = min(240, (day.close_ns-day.open_ns)//MINUTE_NS-15)
    for slot in range(35, cutoff+1, 5):
        if slot not in index:
            return dict(row, status="incomplete_index_prefix")
        close = index[slot][2]
        side = "C" if close > high else "P" if close < low else None
        if side is None:
            continue
        at = day.open_ns + slot * MINUTE_NS
        baseline = None
        if len(history) == LOOKBACK and all(slot in prior for prior in history):
            baseline = fmean(prior[slot] for prior in history)
        relative = volume[slot]/baseline if slot in volume and baseline else None
        regime = regime_at(tape, day.open_ns, at)
        reasons = []
        if relative is None:
            reasons.append("missing_proxy_volume_baseline")
        elif relative <= 1.5:
            reasons.append("weak_volume")
        if move is None:
            reasons.append("missing_atm_straddle")
        elif high-low >= 0.6 * move:
            reasons.append("wide_range")
        if regime is not True:
            reasons.append("missing_regime" if regime is None else "regime_ineligible")
        candidate = dict(at_ns=at, spot=close, right=side, relative_volume=relative,
                         spy_volume=volume.get(slot), mean_prior_spy_volume=baseline,
                         regime_eligible=regime, reasons=reasons)
        row["breakouts"].append(candidate)
        if not reasons:
            return dict(row, signal=candidate, status="signal")
    return dict(row, status="filtered" if row["breakouts"] else "no_breakout")
