"""Price and stress a signed, single-underlying USD option book as of a receipt time.

Marks use the close-out side and displayed size. Model P&L is relative to the
unshocked theoretical book. Spot changes are percentages, IV shifts are
percentage points, and time rolls in calendar minutes. Inputs cover standard
100-multiplier SPXW and unadjusted, American-style ETF contracts.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time

from .american_option import american_option_price
from .gamma_surface import _black76
from .json_input import strict_json

_YEAR_NS = 365 * 86_400 * 1_000_000_000
_MAX_NS = 2**63 - 1
_MULTIPLIER = 100
_STEPS = 200
_KEY = re.compile(r"([A-Z][A-Z.]{0,5}) *([0-9]{6})([CP])([0-9]{8})")


def _fields(obj, expected, name):
    if type(obj) is not dict or obj.keys() != set(expected.split()):
        raise ValueError(f"{name} requires exactly {expected}")


def _number(value, low, high, label):
    if type(value) not in (float, int) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number in [{low}, {high}]")
    return float(value)


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in [{low}, {high}]")
    return value


def _observation(value, fields, asof, max_age):
    if value is None:
        return "missing"
    _fields(value, f"{fields} source_ns received_ns", "observation")
    source = _integer(value["source_ns"], 1, _MAX_NS, "source_ns")
    receipt = _integer(value["received_ns"], 1, _MAX_NS, "received_ns")
    if source > receipt or receipt > asof:
        raise ValueError("observations require source_ns <= received_ns <= asof_ns")
    return "stale" if asof - source > max_age else "fresh"


def _mark(quote, quantity, asof, max_age):
    status = _observation(quote, "bid ask bid_size ask_size", asof, max_age)
    side = "bid" if quantity > 0 else "ask"
    result = dict(close_side=side, mark_status=status, close_side_price=None,
                  displayed_size=None, supported_quantity=0, liquidation_usd=None)
    if quote is None:
        return result
    bid = _number(quote["bid"], 0, 1e8, "bid")
    ask = _number(quote["ask"], 0, 1e8, "ask")
    _integer(quote["bid_size"], 0, 2**31-1, "bid_size")
    _integer(quote["ask_size"], 0, 2**31-1, "ask_size")
    if status != "fresh":
        return result
    if not 0 < bid <= ask:
        return dict(result, mark_status="invalid_market")
    size = quote[side + "_size"]
    result.update(close_side_price=quote[side], displayed_size=size,
                  supported_quantity=min(abs(quantity), size))
    if size < abs(quantity):
        return dict(result, mark_status="insufficient_size")
    return dict(result, mark_status="displayed", liquidation_usd=quantity * _MULTIPLIER * quote[side])


def _leg(spec, underlying, asof):
    _fields(spec, "contract product quantity expiry_ns iv quote", "position")
    contract, product = spec["contract"], spec["product"]
    key = _KEY.fullmatch(contract) if type(contract) is str and len(contract) == 21 else None
    if key is None or int(key[4]) == 0:
        raise ValueError("standard 21-character contract key with a positive strike required")
    if product == "SPXW":
        if underlying != "SPX" or key[1] != "SPXW":
            raise ValueError("SPXW positions require SPX underlying and SPXW contract root")
    elif product != "ETF" or key[1] != underlying or underlying in {"SPX", "SPXW", "XSP", "NDX", "VIX", "RUT"}:
        raise ValueError("declare SPXW or an American ETF with matching standard contract root")
    expiry = _integer(spec["expiry_ns"], asof + 1, _MAX_NS, "expiry_ns")
    if datetime.fromtimestamp(expiry // 1_000_000_000, timezone.utc).strftime("%y%m%d") != key[2]:
        raise ValueError("expiry_ns must lie on the contract expiration date")
    q = _integer(spec["quantity"], -1_000_000, 1_000_000, "quantity")
    if not q:
        raise ValueError("position quantity must be nonzero")
    iv = _number(spec["iv"], .000001, 5, "iv")
    return dict(contract=contract, product=product, quantity=q, iv=iv, expiry_ns=expiry,
                strike=int(key[4]) / 1000, right="call" if key[3] == "C" else "put")


def _model(leg, spot, at, iv_shift, rate, dividend):
    years = (leg["expiry_ns"] - at) / _YEAR_NS
    strike, right = leg["strike"], leg["right"]
    iv = leg["iv"] + iv_shift
    if iv <= 0:
        raise ValueError("shocked IV must stay positive for every live contract")
    if leg["product"] == "SPXW":
        carry = math.exp((rate - dividend) * years)
        point = _black76(spot * carry, strike, iv, years, rate, right)
        premium, delta, gamma = point["price"], point["delta"] * carry, point["gamma"] * carry**2
    else:
        # Bump by one CRR up-move. Tiny bumps can give a spurious zero gamma
        # between lattice nodes, particularly on expiration day.
        bump = min(spot * .25, spot * math.expm1(iv * math.sqrt(years / _STEPS)))
        def price(s):
            return american_option_price(s, strike, iv, years, rate, dividend, right, steps=_STEPS)
        lower, premium, upper = price(spot - bump), price(spot), price(spot + bump)
        delta = (upper - lower) / (2 * bump)
        gamma = (upper - 2 * premium + lower) / bump**2
    units = _MULTIPLIER * leg["quantity"]
    return dict(model_value_usd=premium * units, delta_usd_per_point=delta * units,
                gamma_usd_per_point2=gamma * units)


_MODEL_KEYS = ("model_value_usd", "delta_usd_per_point", "gamma_usd_per_point2")


def _sum(rows, key):
    values = [row[key] for row in rows]
    return math.fsum(values) if all(v is not None for v in values) else None


def position_risk(payload):
    """Evaluate a point-in-time book. Any incomplete leg suppresses its book total."""
    _fields(payload, "asof_ns max_age_ms underlying spot rate dividend_yield positions scenarios", "risk")
    asof = _integer(payload["asof_ns"], 1, _MAX_NS, "asof_ns")
    age = _integer(payload["max_age_ms"], 1, 86_400_000, "max_age_ms") * 1_000_000
    underlying = payload["underlying"]
    if type(underlying) is not str or not re.fullmatch(r"[A-Z][A-Z.]{0,5}", underlying):
        raise ValueError("one uppercase underlying symbol required")
    rate = _number(payload["rate"], -1, 1, "rate")
    dividend = _number(payload["dividend_yield"], 0, 1, "dividend_yield")
    spot_status = _observation(payload["spot"], "price", asof, age)
    spot = None
    if payload["spot"] is not None:
        spot = _number(payload["spot"]["price"], .000001, 1e8, "spot price")
    specs, shocks = payload["positions"], payload["scenarios"]
    if type(specs) is not list or not 1 <= len(specs) <= 32:
        raise ValueError("supply 1–32 net positions")
    if type(shocks) is not list or not 1 <= len(shocks) <= 64:
        raise ValueError("supply 1–64 explicit scenarios")
    rows = []
    for spec in specs:
        leg = _leg(spec, underlying, asof)
        if any(row["contract"] == leg["contract"] for row in rows):
            raise ValueError("combine quantities for each contract into one net position")
        point = (_model(leg, spot, asof, 0, rate, dividend) if spot_status == "fresh"
                 else dict.fromkeys(_MODEL_KEYS))
        rows.append({**leg, **point, **_mark(spec["quote"], leg["quantity"], asof, age),
                     "quote_observation": dict(spec["quote"]) if spec["quote"] is not None else None})
    portfolio = {k: _sum(rows, k) for k in (*_MODEL_KEYS, "liquidation_usd")}
    scenarios = []
    for scenario in shocks:
        _fields(scenario, "spot_change_pct iv_change_points elapsed_minutes", "scenario")
        change = _number(scenario["spot_change_pct"], -99, 100, "spot_change_pct") / 100
        iv_shift = _number(scenario["iv_change_points"], -500, 500, "iv_change_points") / 100
        minutes = _integer(scenario["elapsed_minutes"], 0, 525600, "elapsed_minutes")
        at = asof + minutes * 60_000_000_000
        shocked = dict(scenario, status="priced", model_pnl_usd=None, **dict.fromkeys(_MODEL_KEYS))
        if spot_status != "fresh":
            shocked["status"] = f"{spot_status}_spot"
        elif any(at >= row["expiry_ns"] for row in rows):
            shocked["status"] = "settlement_required"
        else:
            points = [_model(row, spot * (1 + change), at, iv_shift, rate, dividend) for row in rows]
            shocked.update({k: _sum(points, k) for k in _MODEL_KEYS})
            shocked["model_pnl_usd"] = shocked["model_value_usd"] - portfolio["model_value_usd"]
        scenarios.append(shocked)
    return dict(schema="qte-option-position-risk-v1", asof_ns=asof, underlying=underlying,
                spot=spot if spot_status == "fresh" else None, spot_status=spot_status,
                spot_observation=dict(payload["spot"]) if payload["spot"] is not None else None,
                rate=rate, dividend_yield=dividend, max_age_ms=payload["max_age_ms"],
                multiplier=_MULTIPLIER, positions=rows, portfolio=portfolio, scenarios=scenarios,
                assumptions=[
                    "Liquidation is gross USD at displayed close sides, conditional on available size. Fees and fills are excluded.",
                    "Per-leg supplied IV and parallel IV shocks use continuous rates and dividends with Actual/365 Fixed.",
                    "European SPXW uses Black-Scholes-Merton spot Greeks. American ETF uses a 200-step CRR tree and bumped spot Greeks.",
                    "Scenario P&L measures the change from the base model value. Reaching expiry requires a separate settlement observation.",
                ])


def main(argv=None):
    from .live_outputs import publish
    from .live_risk_quotes import apply_recorded_market, live_settings, read_events
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="risk assumptions and positions JSON")
    parser.add_argument("--quotes", type=Path, help="replace all supplied market marks with recorder quotes.ndjson")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--output", type=Path, help="write report to a new JSON file")
    target.add_argument("--desk-snapshot", action="store_true", help="publish a static risk report to the local desk")
    target.add_argument("--desk-live", action="store_true", help="publish SPXW holdings and model assumptions for live repricing")
    args = parser.parse_args(argv)
    try:
        payload = strict_json(args.input.read_bytes())
        desk = Path("build/live-desk")
        if args.desk_live or args.desk_snapshot:
            if not Path("AGENTS.md").is_file() or not Path("python/qte").is_dir():
                raise ValueError("run desk publishing commands from the QTE repository root")
        if args.desk_live:
            if args.quotes:
                raise ValueError("--desk-live reads market observations from the active recorder")
            publish(desk / "risk-settings.json", live_settings(payload, time.time_ns()))
            return 0
        if args.quotes:
            with args.quotes.open("rb") as stream:
                payload = apply_recorded_market(payload, read_events(stream))
        report = position_risk(payload)
        report["market_input"] = "normalized_recorder" if args.quotes else "supplied_snapshot"
        if args.desk_snapshot:
            publish(desk / "risk.json", report)
            return 0
        data = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf8") as stream:
                stream.write(data)
        else:
            print(data, end="")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"option risk: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
