"""Option diagnostics over normalized, captured SPXW books."""
import math
import re

from qte import _core, calm_condor
from qte.adapters.schwab_session import MINUTE

YEAR = 525_600 * MINUTE
SIGN = "Calls positive and puts negative, assuming dealer long calls / short puts"


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def quote(row, at):
    bid, ask, stamp = (row.get(k) for k in ("bid", "ask", "quoteTimeInLong"))
    if (not positive(bid) or not positive(ask) or ask < bid or
            type(stamp) is not int or not 0 <= at-stamp*10**6 <= MINUTE):
        return None
    return dict(bid=bid, ask=ask, mid=(bid+ask)/2, quote_ns=stamp*10**6)


def dealer_gamma(rows, spot, at):
    valid = [b for b in rows if b["expiry_ns"] > at and positive(b.get("volatility"))
             and positive(b.get("openInterest"))]

    def value(px):
        return math.fsum(_core.european_option(px, b["strike"], b["volatility"]/100,
                                               (b["expiry_ns"]-at)/YEAR, b["right"] == "C")[1] *
                         b["openInterest"]*(1 if b["right"] == "C" else -1) for b in valid) * px*px
    if not valid:
        return dict(net=None, flip_levels=[], contracts=0, convention=SIGN)
    xs = [spot*(.95 + .001*i) for i in range(101)]
    ys = [value(x) for x in xs]
    nonzero = [(x, y) for x, y in zip(xs, ys) if y != 0]
    flips = []
    for (left, low), (right, high) in zip(nonzero, nonzero[1:]):
        if low*high >= 0:
            continue
        for _ in range(20):
            mid = (left+right)/2
            if value(mid)*low > 0:
                left = mid
            else:
                right = mid
        flips.append(round((left+right)/2, 2))
    return dict(net=value(spot), flip_levels=flips, contracts=len(valid), convention=SIGN,
                units="USD delta per 1% SPX move, multiplier 100")


def gate(rows, spot, at, close, prior_vix):
    minutes = (close-at)/MINUTE
    if minutes <= 0:
        return dict(status="after_cash_close", score=None)
    indexed = {(r["right"], r["strike"]): r for r in rows}
    result = {}

    def detail(row):
        if row is None:
            return None
        iv = row.get("volatility")
        market = quote(row, at)
        return dict(strike=row["strike"], key=row["symbol"], market=market,
                    model_multiple=iv/prior_vix*calm_condor.CALENDAR_TO_TRADING
                    if positive(iv) and market else None)
    for right in ("P", "C"):
        eligible = [r for r in rows if r["right"] == right and quote(r, at) and positive(r.get("volatility"))]
        result["atm_"+right] = detail(min(eligible, key=lambda r: (abs(spot -
                                      r["strike"]), r["strike"]), default=None))
    legs = calm_condor.structure_legs(spot, minutes, prior_vix/100, calm_condor.PRIMARY)
    for quantity, kind, strike in legs:
        right = kind[0].upper()
        result[("short_" if quantity < 0 else "wing_")+right] = detail(indexed.get((right, strike)))
    multiples = [(result[key] or {}).get("model_multiple") for key in ("short_P", "short_C")]
    score = min(multiples) if all(v is not None for v in multiples) else None
    return dict(status="observed" if score is not None else "missing_short_quote", score=score, legs=result)


def position_expiry(text, session):
    match = re.match(r"[+-]?\d+x SPXW (\d{4}-\d{2}-\d{2}) ", text)
    return match[1] if match else session


def position(text, rows, spot, at, session):
    match = re.fullmatch(r"([+-]?[1-9][0-9]*)x SPXW (?:(\d{4}-\d{2}-\d{2}) )?"
                         r"([0-9]+(?:\.[0-9]+)?)([PC]) @([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        raise ValueError("Position syntax: 2x SPXW 7170P @7.725")
    quantity, expiry, strike, right, entry = match.groups()
    quantity, strike, entry = int(quantity), float(strike), float(entry)
    row = next((b for b in rows if b["expiry"] == (expiry or session) and (
        b["strike"], b["right"]) == (strike, right)), None)
    if row is None or row["expiry_ns"] <= at or not positive(row.get("volatility")):
        raise ValueError("Position contract or calendar IV unavailable in the selected chain")
    market = quote(row, at)
    if market is None:
        raise ValueError("Position contract has no fresh two-sided quote")
    years = (row["expiry_ns"]-at)/YEAR
    iv = row["volatility"]/100
    grid = []
    for shock in (-.02, -.01, 0, .01, .02):
        px = spot*(1+shock)
        points = [_core.european_option(px, strike, iv, years*f, right == "C")[0] for f in (1, .5, 0)]
        grid.append(dict(spot=px, pnl_usd=[round(quantity*(p-entry)*100, 2) for p in points]))
    return dict(contract=row["symbol"], quantity=quantity, entry=entry, mark=market["mid"], asof_ns=at,
                pnl_usd=round(quantity*(market["mid"]-entry)*100, 2),
                expiry_breakeven=strike+(entry if right == "C" else -entry),
                calendar_iv_pct=row["volatility"], grid_time_labels=["asof", "halfway", "expiry"], grid=grid)
