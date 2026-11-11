"""Bounded, side-effect-free Black-76 gamma calculations.

Prices are discounted European premium-paid option prices on futures. Per-option
gamma has units of option-price points per futures-point squared (1/point);
position gamma additionally applies the contract multiplier and signed quantity
and has units of currency per futures-point squared.
"""
from __future__ import annotations

import math
from typing import Any


class GammaInputError(ValueError):
    """A calculator request is outside the fixed public contract."""


_FIELDS = {
    "schema_version", "forward", "strike", "volatility_pct", "days",
    "rate_pct", "multiplier", "quantity", "option_type", "span_pct",
}
_SQRT_TWO_PI = math.sqrt(2.0 * math.pi)


def _number(payload: dict[str, Any], name: str, low: float, high: float) -> float:
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GammaInputError(f"{name} must be a number")
    # Check bounds before conversion so an arbitrarily large JSON integer cannot
    # raise OverflowError while being converted to a binary float.
    if not low <= value <= high or isinstance(value, float) and not math.isfinite(value):
        raise GammaInputError(f"{name} is outside its allowed range")
    return float(value)


def _integer(payload: dict[str, Any], name: str, low: int, high: int) -> int:
    value = payload[name]
    if isinstance(value, bool) or type(value) is not int or not low <= value <= high:
        raise GammaInputError(f"{name} must be an integer in its allowed range")
    return value


def _normal_cdf(value: float) -> float:
    return 0.5 * math.erfc(-value / math.sqrt(2.0))


def _black76(forward: float, strike: float, volatility: float, time_years: float,
             rate: float, option_type: str) -> dict[str, float]:
    root_time = math.sqrt(time_years)
    vol_time = volatility * root_time
    d1 = (math.log(forward / strike) + 0.5 * volatility * volatility * time_years) / vol_time
    d2 = d1 - vol_time
    discount = math.exp(-rate * time_years)
    density = math.exp(-0.5 * d1 * d1) / _SQRT_TWO_PI
    gamma = discount * density / (forward * vol_time)
    if option_type == "call":
        price = discount * (forward * _normal_cdf(d1) - strike * _normal_cdf(d2))
        delta = discount * _normal_cdf(d1)
    else:
        price = discount * (strike * _normal_cdf(-d2) - forward * _normal_cdf(-d1))
        delta = -discount * _normal_cdf(-d1)
    # Roundoff can only make a theoretically non-negative price slightly negative.
    return {"price": max(0.0, price), "delta": delta, "gamma": gamma,
            "d1": d1, "discount": discount}


def _fd_gamma(forward: float, strike: float, volatility: float, time_years: float,
              rate: float, option_type: str, center_price: float) -> tuple[float, float, str]:
    # A 1 bp move is normally useful, but cannot exceed a tenth of the
    # one-standard-deviation move for very short expiries.
    scale_limit = forward * volatility * math.sqrt(time_years) * 0.1
    step = max(forward * 1.0e-8, min(forward * 1.0e-4, scale_limit))
    lower = _black76(forward - step, strike, volatility, time_years, rate, option_type)["price"]
    upper = _black76(forward + step, strike, volatility, time_years, rate, option_type)["price"]
    estimate = (upper - 2.0 * center_price + lower) / (step * step)
    if estimate < 0.0 and abs(estimate) < 1.0e-12:
        estimate = 0.0
    status = "ok"
    if center_price == lower == upper or estimate == 0.0:
        status = "tail_cancellation"
    return estimate, step, status


def calculate_gamma_surface(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one fixed envelope and return a bounded Black-76 surface."""
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise GammaInputError("request fields do not match schema version 1")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise GammaInputError("unsupported schema version")
    forward = _number(payload, "forward", 100.0, 1_000_000.0)
    strike = _number(payload, "strike", 100.0, 1_000_000.0)
    volatility_pct = _number(payload, "volatility_pct", 1.0, 200.0)
    days = _number(payload, "days", 1.0 / 24.0, 365.0)
    rate_pct = _number(payload, "rate_pct", -10.0, 50.0)
    multiplier = _integer(payload, "multiplier", 1, 1000)
    quantity = _integer(payload, "quantity", -1000, 1000)
    span_pct = _number(payload, "span_pct", 1.0, 30.0)
    option_type = payload["option_type"]
    if type(option_type) is not str or option_type not in {"call", "put"}:
        raise GammaInputError("option_type must be call or put")

    volatility = volatility_pct / 100.0
    rate = rate_pct / 100.0
    time_years = days / 365.0
    point_values = _black76(forward, strike, volatility, time_years, rate, option_type)
    finite_difference, finite_step, finite_status = _fd_gamma(
        forward, strike, volatility, time_years, rate, option_type, point_values["price"])
    point_gamma = point_values["gamma"]
    finite_error = abs(finite_difference - point_gamma)
    if (finite_difference < 0.0
            or finite_error > max(1.0e-12, point_gamma * 0.01)):
        finite_status = "roundoff_limited"

    span = span_pct / 100.0
    forwards = [forward * (1.0 - span + 2.0 * span * index / 40.0) for index in range(41)]
    maximum_days = max(30.0, days)
    minimum_days = 1.0 / 24.0
    ratio = maximum_days / minimum_days
    surface_days = [minimum_days * ratio ** (index / 24.0) for index in range(25)]
    surface_days[-1] = maximum_days
    gamma_grid = [
        [_black76(item_forward, strike, volatility, item_days / 365.0, rate, option_type)["gamma"]
         for item_forward in forwards]
        for item_days in surface_days
    ]
    slice_values = []
    for item_forward in forwards:
        values = _black76(item_forward, strike, volatility, time_years, rate, option_type)
        slice_values.append({"forward": item_forward, "gamma": values["gamma"],
                             "position_gamma": values["gamma"] * multiplier * quantity,
                             "price": values["price"]})

    warnings = [
        "Model-only output from supplied inputs; no live market or broker data is used.",
        "Black-76 assumes European exercise and lognormal futures prices; per-option gamma is option-price points per futures-point squared (1/point).",
        "Calendar/365 time and a continuously compounded rate are assumed.",
    ]
    if finite_status != "ok":
        warnings.append("Finite-difference gamma is cancellation/roundoff limited and is only a numerical diagnostic, not market validation.")
    echoed = {name: payload[name] for name in (
        "schema_version", "forward", "strike", "volatility_pct", "days", "rate_pct",
        "multiplier", "quantity", "option_type", "span_pct")}
    return {
        "schema_version": 1,
        "mode": "model_only",
        "model": "black76_european",
        "inputs": echoed,
        "point": {
            **point_values,
            "position_gamma": point_gamma * multiplier * quantity,
            "time_years": time_years,
            "finite_difference_gamma": finite_difference,
            "finite_difference_step": finite_step,
            "finite_difference_absolute_error": finite_error,
            "finite_difference_status": finite_status,
        },
        "surface": {"forwards": forwards, "days": surface_days, "gamma": gamma_grid},
        "slice": slice_values,
        "warnings": warnings,
    }
