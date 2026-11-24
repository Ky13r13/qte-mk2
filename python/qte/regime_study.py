"""Causal SPX regime research and hypothetical European option replay.

This research layer reuses QTE's gamma/pricing model, not the live broker or
the core execution engine. Hypothetical weekly contracts are not listing evidence.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import math
from statistics import fmean, pstdev

from .gamma_surface import _black76

REGIMES = ("unclassified", "stress_selloff", "recovery", "high_vol_down",
           "high_vol_range", "low_vol_trend", "trend_up", "trend_down", "range")
FAMILIES = ("sma20", "ema20", "flow_plus", "flow_minus", "vwma20",
            "band_reentry", "sma300_reclaim", "stress_momentum")
SCENARIOS = ("base_frozen_iv", "wide_frozen_iv", "vix_repriced")
INITIAL_CASH = 1_000_000.0


def classify(p, sma20, sma50, vix, daily_z, recent_stress):
    if (daily_z <= -2 and vix >= 25) or (vix >= 35 and p["close"] < sma20):
        return "stress_selloff", f"Prior return {daily_z:.2f} sigma; VIX {vix:.2f}; below SMA20={p['close'] < sma20}"
    if recent_stress and p["close"] > p["open"]:
        return "recovery", "Positive prior candle within five sessions of observable stress"
    if vix >= 25:
        down = p["close"] < sma50 and sma20 < sma50
        return ("high_vol_down" if down else "high_vol_range"), f"VIX {vix:.2f} >=25; bearish trend={down}"
    if p["close"] > sma50 and sma20 > sma50:
        return ("low_vol_trend" if vix < 15 else "trend_up"), f"Prior close > SMA50, SMA20 > SMA50; VIX {vix:.2f}"
    if p["close"] < sma50 and sma20 < sma50:
        return "trend_down", "Prior close < SMA50 and SMA20 < SMA50"
    return "range", "Trend conditions mixed; prior VIX below 25"


def features(daily):
    """Today's regime/levels use only earlier dates. Today's OHLC is display/outcome only."""
    rows = [daily["SPX"][day] for day in sorted(daily["SPX"])]
    closes, ranges, known, out = [], [], [], []
    ema = None
    for i, current in enumerate(rows):
        day = current["date"]
        result = dict(current, regime="unclassified", reason="Requires 300 prior SPX sessions and matching prior VIX")
        if i:
            p = rows[i - 1]
            vbar = daily["VIX"].get(p["date"])
            fresh = (date.fromisoformat(day) - date.fromisoformat(p["date"])).days <= 4
            if i >= 300 and vbar and fresh:
                vix = vbar["close"]
                prev_vix = daily["VIX"].get(rows[i-2]["date"], {}).get("close", vix)
                z = (p["close"] / rows[i-2]["close"] - 1) / (prev_vix / 100 / math.sqrt(252))
                sma20, sma50, sma300 = fmean(closes[-20:]), fmean(closes[-50:]), fmean(closes[-300:])
                std, atr = pstdev(closes[-20:]), fmean(ranges[-14:])
                regime, reason = classify(p, sma20, sma50, vix, z, "stress_selloff" in known[-5:])
                move = p["close"] * vix / 100 / math.sqrt(252)
                spy = [daily["SPY"].get(r["date"]) for r in rows[i-20:i]]
                flow = vwma = None
                if all(b is not None for b in spy) and sum(b["volume"] for b in spy) > 0:
                    total = sum(b["volume"] for b in spy)
                    # OHLC close-location volume proxy; never claimed to be trade/order-book imbalance.
                    flow = sum((2*b["close"]-b["high"]-b["low"]) / (b["high"]-b["low"])
                               * b["volume"] if b["high"]>b["low"] else 0 for b in spy) / total
                    vwma = sum(c*b["volume"] for c, b in zip(closes[-20:], spy)) / total
                rv = pstdev([math.log(closes[j]/closes[j-1]) for j in range(len(closes)-20, len(closes))]) * math.sqrt(252)
                result.update(regime=regime, reason=reason, asof=p["date"], vix=vix,
                              sma20=sma20, sma50=sma50, sma300=sma300, ema20=ema, atr14=atr,
                              bb_lower=sma20-2*std, bb_upper=sma20+2*std,
                              anchor=p["close"], expected_move=move, lower2=p["close"]-2*move,
                              upper2=p["close"]+2*move, daily_z=z, realized_vol20=rv,
                              flow=flow, vwma20=vwma,
                              adjusted_plus=None if flow is None else ema+.5*atr*flow,
                              adjusted_minus=None if flow is None else ema-.5*atr*flow,
                              prior_open=p["open"], prior_low=p["low"], prior_high=p["high"])
        known.append(result["regime"])
        out.append(result)
        ranges.append(max(current["high"]-current["low"], abs(current["high"]-closes[-1]),
                          abs(current["low"]-closes[-1])) if closes else current["high"]-current["low"])
        closes.append(current["close"])
        ema = current["close"] if ema is None else ema + 2/21*(current["close"]-ema)
    return out


def intervals(rows):
    out = []
    for row in rows:
        if not out or out[-1]["regime"] != row["regime"]:
            out.append({"start": row["date"], "end": row["date"], "regime": row["regime"],
                        "reason": row["reason"], "sessions": 1})
        else:
            out[-1]["end"] = row["date"]
            out[-1]["sessions"] += 1
    return out


def direction(row, family):
    if row["regime"] == "unclassified":
        return 0
    close = row["anchor"]
    key = {"sma20": "sma20", "ema20": "ema20", "flow_plus": "adjusted_plus",
           "flow_minus": "adjusted_minus", "vwma20": "vwma20"}.get(family)
    if key:
        return 0 if row[key] is None else (1 if close > row[key] else -1 if close < row[key] else 0)
    if family == "stress_momentum":
        return -1 if row["regime"] in ("stress_selloff", "high_vol_down") and close < row["prior_open"] else 0
    if row["regime"] in ("stress_selloff", "high_vol_down"):
        return 0
    if family == "sma300_reclaim":
        return 1 if row["prior_low"] <= row["sma300"]*1.003 and close > row["sma300"] and close > row["prior_open"] else 0
    if family == "band_reentry":
        if row["prior_low"] < row["bb_lower"] < close and close > row["prior_open"]:
            return 1
        if row["prior_high"] > row["bb_upper"] > close and close < row["prior_open"]:
            return -1
    return 0


def guard(row, sign, spot, *, lower=None, upper=None):
    """No bearish chase at support/extreme; stress allows continuation, not arbitrary size."""
    if not sign:
        return False
    stress = row["regime"] in ("stress_selloff", "high_vol_down")
    if sign > 0 and stress:
        return False
    if sign < 0 and not stress:
        if spot <= row["lower2"] or abs(spot/row["sma300"]-1) <= .005:
            return False
        if lower is not None and spot < lower:
            return False
    if sign > 0 and (spot >= row["upper2"] or (upper is not None and spot > upper)):
        return False
    return True


def _price(spot, strike, years, iv, right):
    return _black76(spot*math.exp((.04-.013)*years), strike, iv, years, .04, right)


def option_trade(spot, exit_spot, iv, exit_iv, sign, vehicle, hours, scenario):
    """One hypothetical seven-day contract/spread. Dollars rounded to cents per leg."""
    strike = 5 * math.ceil(spot/5-.5)
    width = max(5, 5 * round(.01*spot/5))
    right = "call" if sign > 0 else "put"
    if vehicle == "long":
        legs = [(1, right, strike)]
    elif vehicle == "credit":
        right = "put" if sign > 0 else "call"
        legs = [(-1, right, strike), (1, right, strike-sign*width)]
    elif vehicle == "cash_put" and sign > 0:
        legs = [(-1, "put", strike)]
    else:
        raise ValueError("Unknown or incompatible option vehicle")
    wide = scenario == "wide_frozen_iv"
    half, fee = (1.0, 2.5) if wide else (.25, 1.25)
    out_iv = exit_iv if scenario == "vix_repriced" else iv
    entry, exit_value, gamma = 0.0, 0.0, 0.0
    for qty, kind, k in legs:
        initial = _price(spot, k, 7/365, iv, kind)
        ending = _price(exit_spot, k, (7-hours/24)/365, out_iv, kind)
        # Adverse .10-point rounding; historical contract tick rules are hypothetical.
        buy = lambda x: math.ceil((x+half)*10)/10
        sell = lambda x: math.floor(max(0, x-half)*10)/10
        entry += qty*(buy(initial["price"]) if qty > 0 else sell(initial["price"])) * 100
        exit_value += qty*(sell(ending["price"]) if qty > 0 else buy(ending["price"])) * 100
        gamma += qty*initial["gamma"]*math.exp(2*(.04-.013)*7/365)*100
    fees = 2*len(legs)*fee
    # Expiry payoff cap plus modeled liquidation friction; independently rounded
    # leg quotes may cost more than the expiry-width cap when closed early.
    friction = len(legs)*(half+.1)*100
    risk = entry+fees if vehicle == "long" else width*100+entry+fees+friction if vehicle == "credit" else strike*100+entry+fees+friction
    collateral = max(entry, 0) if vehicle == "long" else width*100 if vehicle == "credit" else strike*100
    if risk <= 0 or (vehicle == "credit" and entry >= 0):
        return None
    return {"pnl": round(exit_value-entry-fees, 2), "entry_debit": round(entry, 2),
            "exit_value": round(exit_value, 2), "fees": fees, "max_loss": round(risk, 2),
            "collateral": round(collateral+fees+(friction if vehicle != "long" else 0), 2), "gamma": gamma, "strike": strike,
            "width": width if vehicle == "credit" else None}


def aggregate(trades):
    if not trades:
        return {"trades": 0, "pnl": 0, "win_rate": None, "drawdown": 0, "worst_trade": None,
                "return_on_risk": None, "profit_factor": None, "max_collateral": 0}
    pnls = [t["pnl"] for t in trades]
    gains, losses = sum(max(x, 0) for x in pnls), -sum(min(x, 0) for x in pnls)
    cash = peak = INITIAL_CASH
    dd = 0
    for pnl in pnls:
        cash += pnl
        peak = max(peak, cash)
        dd = max(dd, peak-cash)
    return {"trades": len(trades), "pnl": round(sum(pnls), 2), "win_rate": sum(x>0 for x in pnls)/len(pnls),
            "drawdown": round(dd, 2), "worst_trade": min(pnls),
            "return_on_risk": sum(pnls)/sum(t["max_loss"] for t in trades),
            "profit_factor": gains/losses if losses else None,
            "max_collateral": max(t["collateral"] for t in trades)}


def run_daily(rows, vix):
    trades, skipped = [], Counter()
    for family in FAMILIES:
        for vehicle in ("long", "credit", "cash_put"):
            for scenario in SCENARIOS:
                cash = INITIAL_CASH
                for row in rows:
                    sign = direction(row, family)
                    if not sign or (vehicle == "cash_put" and sign < 0):
                        continue
                    # Known daily signal, observed opening value; modeled fill at opening value plus costs.
                    if not guard(row, sign, row["open"], lower=row["bb_lower"], upper=row["bb_upper"]):
                        skipped["daily_guard"] += 1
                        continue
                    exit_vix = vix.get(row["date"])
                    if scenario == "vix_repriced" and exit_vix is None:
                        skipped["exit_vix_missing"] += 1
                        continue
                    trade = option_trade(row["open"], row["close"], row["vix"]/100,
                                         (exit_vix["close"] if exit_vix else row["vix"])/100,
                                         sign, vehicle, 6.5, scenario)
                    if trade is None or trade["collateral"] > cash:
                        skipped["collateral_or_invalid_credit"] += 1
                        continue
                    cash += trade["pnl"]
                    trades.append({**trade, "date": row["date"], "asof": row["asof"],
                                   "regime": row["regime"], "family": family, "vehicle": vehicle,
                                   "scenario": scenario, "direction": sign,
                                   "entry_spot": row["open"], "exit_spot": row["close"]})
    return trades, dict(skipped)


def run_intraday(rows, minutes):
    """Five-minute holds, first later complete minute entry, max five trades per day."""
    trades, charts = [], {}
    balances = defaultdict(lambda: INITIAL_CASH)
    for row in rows:
        bars = minutes["SPX"].get(row["date"], [])
        if row["regime"] == "unclassified" or not bars:
            continue
        spy = {b["time"]: b for b in minutes["SPY"].get(row["date"], [])}
        chart, closes, proxy, ema = [], [], [], None
        stress_latched = False
        session_end = int(datetime.combine(date.fromisoformat(row["date"]), time(16), ZoneInfo("America/New_York")).timestamp())
        by_time = {b["time"]: b for b in bars}
        decisions = []
        for j, bar in enumerate(bars):
            spot = bar["close"]
            closes.append(spot)
            ema = spot if ema is None else ema+2/21*(spot-ema)
            proxy.append(spy.get(bar["time"]))
            view = dict(bar, regime=row["regime"])
            if j >= 20 and all(bars[k]["time"]-bars[k-1]["time"] == 60 for k in range(j-19, j+1)):
                center, std = fmean(closes[-20:]), pstdev(closes[-20:])
                lower, upper = center-2*std, center+2*std
                priorcenter, priorstd = fmean(closes[-21:-1]), pstdev(closes[-21:-1])
                # Intraday observed downside shock overrides the premarket label immediately.
                stress_latched = stress_latched or (spot < center and (
                    spot <= row["anchor"]-2.5*row["expected_move"]
                    or (spot <= row["lower2"] and row["vix"] >= 25)))
                effective = dict(row, regime="stress_selloff" if stress_latched else row["regime"])
                view.update(bb_lower=lower, bb_upper=upper, ema20=ema, regime=effective["regime"])
                signals = {}
                if effective["regime"] not in ("stress_selloff", "high_vol_down"):
                    if closes[-2] < priorcenter-2*priorstd and spot >= lower and spot <= row["anchor"]-row["expected_move"]:
                        signals["minute_reentry"] = 1
                    elif closes[-2] > priorcenter+2*priorstd and spot <= upper and spot >= row["anchor"]+row["expected_move"]:
                        signals["minute_reentry"] = -1
                    if bars[j-1]["low"] <= row["sma300"] <= spot and closes[-2] <= row["sma300"]:
                        signals["minute_sma300"] = 1
                elif spot < lower:
                    signals["minute_stress"] = -1
                signals["minute_ema"] = 1 if spot > ema else -1
                recent = proxy[-20:]
                if all(b is not None for b in recent) and (total := sum(b["volume"] for b in recent)) > 0:
                    flow = sum((2*b["close"]-b["high"]-b["low"])/(b["high"]-b["low"])*b["volume"]
                               if b["high"]>b["low"] else 0 for b in recent)/total
                    atr = fmean(max(b["high"]-b["low"], abs(b["high"]-bars[k-1]["close"]),
                                    abs(b["low"]-bars[k-1]["close"])) for k, b in enumerate(bars[j-13:j+1], j-13))
                    adjusted = ema+.5*atr*flow
                    view["adjusted_plus"] = adjusted
                    signals["minute_ema_proxy_coverage"] = signals["minute_ema"]
                    signals["minute_flow"] = 1 if spot > adjusted else -1
                if bar["time"] + 360 <= session_end:
                    for family, sign in signals.items():
                        if guard(effective, sign, spot, lower=lower, upper=upper):
                            decisions.append((j, family, sign, effective["regime"]))
            chart.append(view)
        charts[row["date"]] = {"daily": row, "bars": chart}
        for family in ("minute_reentry", "minute_sma300", "minute_stress", "minute_ema", "minute_ema_proxy_coverage", "minute_flow"):
            for vehicle in ("long", "credit"):
                for scenario in ("base_frozen_iv", "wide_frozen_iv"):
                    occupied, count = -1, 0
                    key = (family, vehicle, scenario)
                    for j, name, sign, regime in decisions:
                        if name != family or bars[j]["time"] < occupied or count >= 5:
                            continue
                        entry = by_time.get(bars[j]["time"]+60)
                        if entry is None:
                            continue
                        ending = by_time.get(entry["time"]+300)
                        trade = option_trade(entry["close"], ending["close"] if ending else entry["close"], row["vix"]/100,
                                             row["vix"]/100, sign, vehicle, 5/60, scenario)
                        if trade is None or trade["collateral"] > balances[key]:
                            continue
                        if ending is None:
                            # Missing exit cannot retroactively cancel an entered trade.
                            trade.update(pnl=-trade["max_loss"], exit_value=None)
                        balances[key] += trade["pnl"]
                        occupied, count = entry["time"]+300, count+1
                        trades.append({**trade, "date": row["date"], "family": family, "vehicle": vehicle,
                                       "scenario": scenario, "regime": regime, "direction": sign,
                                       "signal_time": bars[j]["time"], "entry_time": entry["time"],
                                       "exit_time": ending["time"] if ending else None, "entry_spot": entry["close"],
                                       "unresolved": ending is None,
                                       "exit_spot": ending["close"] if ending else None})
    return trades, charts


def comparisons(trades):
    groups = defaultdict(list)
    for trade in trades:
        split = "development" if trade["date"] < "2016-01-01" else "validation" if trade["date"] < "2021-01-01" else "holdout"
        for regime in ("all", trade["regime"]):
            groups[(split, regime, trade["family"], trade["vehicle"], trade["scenario"])].append(trade)
    return [{"split": split, "regime": regime, "family": family, "vehicle": vehicle, "scenario": scenario,
             **aggregate(sorted(values, key=lambda t: (t["date"], t.get("entry_time", 0))))}
            for (split, regime, family, vehicle, scenario), values in sorted(groups.items())]


def walk_forward(trades):
    """Prior five years choose among capped-risk base-model variants; next year only scores."""
    eligible = [t for t in trades if t["scenario"] == "base_frozen_iv" and t["vehicle"] != "cash_put"]
    selections = []
    for year in range(2016, 2027):
        for regime in REGIMES[1:]:
            train = [t for t in eligible if year-5 <= int(t["date"][:4]) < year and t["regime"] == regime]
            groups = defaultdict(list)
            for t in train:
                groups[(t["family"], t["vehicle"])].append(t)
            candidates = [(aggregate(v)["return_on_risk"], key, v) for key, v in groups.items() if len(v) >= 30]
            # Flat is always an available candidate. Never force a historical loser into exposure.
            best = max(candidates, default=None, key=lambda x: (x[0], x[1]))
            if best is None or best[0] <= 0:
                selections.append({"year": year, "regime": regime, "selected": "cash", "training_trades": len(train),
                                   "wide_pnl": 0, "repriced_pnl": 0, **aggregate([])})
                continue
            score, (family, vehicle), training = best
            test = [t for t in eligible if int(t["date"][:4]) == year and t["regime"] == regime
                    and t["family"] == family and t["vehicle"] == vehicle]
            sensitivity = {scenario: sum(t["pnl"] for t in trades if t["scenario"] == scenario
                           and int(t["date"][:4]) == year and t["regime"] == regime
                           and t["family"] == family and t["vehicle"] == vehicle)
                           for scenario in ("wide_frozen_iv", "vix_repriced")}
            selections.append({"year": year, "regime": regime, "selected": family+" / "+vehicle,
                               "training_trades": len(training), "training_return_on_risk": score,
                               "wide_pnl": round(sensitivity["wide_frozen_iv"], 2),
                               "repriced_pnl": round(sensitivity["vix_repriced"], 2), **aggregate(test)})
    return selections
