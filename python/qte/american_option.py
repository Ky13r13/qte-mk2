"""American equity option diagnostics for synthetic research.

The Cox-Ross-Rubinstein lattice uses constant volatility, a continuously
compounded rate, and a continuous dividend yield in place of discrete dividend
payments. Exercise is available at each lattice time, including immediately.
Prices are in underlying price units per share, with no contract multiplier,
spreads, or fees. The model does not simulate assignment or actual fills.
"""
from __future__ import annotations

import math


def _finite_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def american_option_price(
    spot: float,
    strike: float,
    iv: float,
    years: float,
    rate: float,
    dividend_yield: float,
    option_type: str,
    *,
    steps: int = 200,
) -> float:
    """Price a call or put using decimal annual inputs and calendar years.

    At zero maturity the price is intrinsic value. At zero volatility the
    model checks exercise along the deterministic risk-neutral path. An
    incompatible drift and time step raises ValueError.
    """
    spot = _finite_number("spot", spot)
    strike = _finite_number("strike", strike)
    iv = _finite_number("iv", iv)
    years = _finite_number("years", years)
    rate = _finite_number("rate", rate)
    dividend_yield = _finite_number("dividend_yield", dividend_yield)
    if min(spot, strike) <= 0.0:
        raise ValueError("spot and strike must be positive")
    if min(iv, years, dividend_yield) < 0.0:
        raise ValueError("iv, years, and dividend_yield must be nonnegative")
    if type(option_type) is not str or option_type not in {"call", "put"}:
        raise ValueError("option_type must be call or put")
    if type(steps) is not int or not 1 <= steps <= 2_048:
        raise ValueError("steps must be an integer between 1 and 2048")

    direction = 1.0 if option_type == "call" else -1.0
    intrinsic = max(0.0, direction * (spot - strike))
    if years == 0.0:
        return intrinsic

    try:
        if iv == 0.0:
            price = _deterministic_price(spot, strike, years, rate, dividend_yield,
                                         steps, direction, intrinsic)
        else:
            price = _lattice_price(spot, strike, iv, years, rate, dividend_yield,
                                   steps, direction, intrinsic)
    except OverflowError as exc:
        raise ValueError("option lattice exceeds the numeric range") from exc
    if not math.isfinite(price):
        raise ValueError("option lattice exceeds the numeric range")
    return price


def _deterministic_price(
    spot: float, strike: float, years: float, rate: float, dividend_yield: float,
    steps: int, direction: float, intrinsic: float,
) -> float:
    price = intrinsic
    for index in range(1, steps + 1):
        exercise_time = years * (index / steps)
        discounted_spot = spot * math.exp(-dividend_yield * exercise_time)
        discounted_strike = strike * math.exp(-rate * exercise_time)
        price = max(price, direction * (discounted_spot - discounted_strike))
    return price


def _lattice_price(
    spot: float, strike: float, iv: float, years: float, rate: float, dividend_yield: float,
    steps: int, direction: float, intrinsic: float,
) -> float:
    dt = years / steps
    log_up = iv * (math.sqrt(years) / math.sqrt(steps))
    log_growth = rate * dt - dividend_yield * dt
    if log_up <= 0.0 or not math.isfinite(log_up):
        raise ValueError("lattice volatility is outside the numeric range")
    if not -log_up <= log_growth <= log_up:
        raise ValueError("CRR risk-neutral probability is outside [0, 1]; increase steps")

    # Divide both probabilities by the up factor and use expm1 so short
    # maturities and small per-step price changes retain precision.
    denominator = -math.expm1(-2.0 * log_up)
    discount = math.exp(-rate * dt)
    up_weight = discount * math.exp(log_growth - log_up) * (
        -math.expm1(-(log_growth + log_up)) / denominator)
    down_weight = discount * (-math.expm1(log_growth - log_up) / denominator)

    # Each net up/down move produces the same exercise value at every level.
    # Maturity uses every second entry, from all down moves to all up moves.
    log_spot = math.log(spot)
    exercise_values = [
        max(0.0, direction * (math.exp(log_spot + offset * log_up) - strike))
        for offset in range(-steps, steps + 1)
    ]
    exercise_values[steps] = intrinsic
    values = exercise_values[::2]
    for level in range(steps - 1, 0, -1):
        first_spot = steps - level
        for index in range(level + 1):
            exercise = exercise_values[first_spot + 2 * index]
            continuation = down_weight * values[index] + up_weight * values[index + 1]
            values[index] = max(continuation, exercise)
    return max(down_weight * values[0] + up_weight * values[1], intrinsic)


def american_option_gamma(
    spot: float,
    strike: float,
    iv: float,
    years: float,
    rate: float,
    dividend_yield: float,
    option_type: str,
    *,
    spot_bump: float,
    steps: int = 200,
) -> float:
    """Estimate per-share spot gamma with three prices at the same volatility.

    The bump is an absolute spot move in price units. Gamma has units of
    option-price points per spot-point squared. Its numerical estimate
    depends on the spot bump and lattice resolution, especially near expiry
    or an exercise boundary.
    """
    spot = _finite_number("spot", spot)
    spot_bump = _finite_number("spot_bump", spot_bump)
    if not 0.0 < spot_bump < spot or spot - spot_bump == spot or spot + spot_bump == spot:
        raise ValueError("spot_bump must change the price and keep the lower spot positive")
    lower = american_option_price(spot - spot_bump, strike, iv, years, rate, dividend_yield,
                                  option_type, steps=steps)
    center = american_option_price(spot, strike, iv, years, rate, dividend_yield,
                                   option_type, steps=steps)
    upper = american_option_price(spot + spot_bump, strike, iv, years, rate, dividend_yield,
                                  option_type, steps=steps)
    estimate = ((upper - center) / spot_bump - (center - lower) / spot_bump) / spot_bump
    if not math.isfinite(estimate):
        raise ValueError("gamma exceeds the numeric range")
    return estimate
