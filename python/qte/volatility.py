"""Point-in-time VX proxy scenarios, not option prices or calibrated forecasts.

One explicitly selected VX contract supplies an annualized volatility *scenario*.
The constant-volatility, zero-drift arithmetic-normal approximation uses a
365-calendar-day year. It does not recover NQ implied volatility from VX futures.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from qte import BarObservation

MINUTE_NS = 60_000_000_000
DAY_NS = 86_400_000_000_000
YEAR_NS = 365 * DAY_NS
MODEL_ID = "vx_selected_contract_normal_proxy_calendar365_v1"


def _time(value: int, name: str) -> None:
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError(f"{name} must be signed int64 UTC nanoseconds")


def _number(value: float, name: str, *, positive: bool = False) -> None:
    if type(value) not in (float, int):
        raise ValueError(f"{name} must be finite numeric")
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid or (positive and value <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))


def _identity(value: str, name: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 1024:
        raise ValueError(f"{name} must be a nonblank string of at most 1024 characters")


def _availability(reference: int, available: int) -> None:
    _time(reference, "reference_ns")
    _time(available, "available_ns")
    if reference > available:
        raise ValueError("reference_ns must not follow available_ns")


def _gamma_change(gamma: float, distance: float) -> float:
    # Combine exponents separately: neither 0.5*gamma nor distance**2 need
    # be representable on their own when the final convexity value is finite.
    if gamma == 0:
        return 0.0
    gm, ge = math.frexp(gamma)
    dm, de = math.frexp(distance)
    try:
        result = math.ldexp(0.5 * gm * dm * dm, ge + 2 * de)
    except OverflowError:
        raise ValueError("local gamma gross change exceeds numeric range") from None
    if result == 0:
        raise ValueError("local gamma gross change underflows numeric range")
    _number(result, "local gamma gross change")
    return result


@dataclass(frozen=True)
class VolatilityFutureObservation:
    contract_id: str
    expiry_cutoff_ns: int
    value_pct: float
    reference_ns: int
    available_ns: int
    source_id: str
    vintage_id: str
    evidence: str = "historical_declared"
    discretionary: bool | None = None

    def __post_init__(self) -> None:
        for name in ("contract_id", "source_id", "vintage_id"):
            _identity(getattr(self, name), name)
        _time(self.expiry_cutoff_ns, "expiry_cutoff_ns")
        _availability(self.reference_ns, self.available_ns)
        _number(self.value_pct, "value_pct", positive=True)
        if self.evidence not in ("synthetic", "historical_declared"):
            raise ValueError("evidence must be synthetic or historical_declared")
        if self.discretionary is not None and type(self.discretionary) is not bool:
            raise ValueError("discretionary must be bool or None (unknown)")


@dataclass(frozen=True)
class ProxyAssumption:
    """Explicit VX-to-target multiplier; no automatic estimation/calibration."""

    target_symbol: str
    volatility_scale: float
    assumption_id: str
    reference_ns: int
    available_ns: int

    def __post_init__(self) -> None:
        _identity(self.target_symbol, "target_symbol")
        _identity(self.assumption_id, "assumption_id")
        _number(self.volatility_scale, "volatility_scale", positive=True)
        _availability(self.reference_ns, self.available_ns)


@dataclass(frozen=True)
class CandleSnapshot:
    """Owned OHLC facts from an already completed canonical candle."""

    label: str
    symbol: str
    start_ns: int
    end_ns: int
    available_ns: int
    open: float
    high: float
    low: float
    close: float
    source_id: str

    def __post_init__(self) -> None:
        for name in ("label", "symbol", "source_id"):
            _identity(getattr(self, name), name)
        _time(self.start_ns, "start_ns")
        _availability(self.end_ns, self.available_ns)
        if self.start_ns >= self.end_ns:
            raise ValueError("candle interval must be positive")
        for name in ("open", "high", "low", "close"):
            _number(getattr(self, name), name, positive=True)
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("inconsistent OHLC")

    @classmethod
    def from_observation(cls, label: str, observation: BarObservation, *, source_id: str):
        if not isinstance(observation, BarObservation):
            raise ValueError("observation must be a canonical BarObservation")
        bar = observation.bar
        return cls(label, bar.symbol, bar.start_ns, bar.end_ns, observation.available_at_ns,
                   bar.open, bar.high, bar.low, bar.close, source_id)


@dataclass(frozen=True)
class MoveHorizon:
    label: str
    duration_ns: int

    def __post_init__(self) -> None:
        _identity(self.label, "label")
        _time(self.duration_ns, "duration_ns")
        if self.duration_ns <= 0:
            raise ValueError("duration_ns must be positive elapsed calendar time")


DEFAULT_HORIZONS = tuple(MoveHorizon(label, minutes * MINUTE_NS) for label, minutes in (
    ("1m", 1), ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60), ("4h", 240),
    ("1d_calendar", 1440),
))


@dataclass(frozen=True)
class GammaSensitivity:
    """Net signed currency/point² supplied from a separately validated option book."""

    symbol: str
    dollars_per_point_squared: float
    reference_ns: int
    available_ns: int
    source_id: str

    def __post_init__(self) -> None:
        _identity(self.symbol, "symbol")
        _identity(self.source_id, "source_id")
        _availability(self.reference_ns, self.available_ns)
        _number(self.dollars_per_point_squared, "dollars_per_point_squared")


@dataclass(frozen=True)
class SigmaBand:
    sigma: int
    lower: float
    upper: float
    gaussian_endpoint_mass: float
    local_gamma_gross_change: float | None


@dataclass(frozen=True)
class LevelDistance:
    candle_label: str
    candle_end_ns: int
    candle_age_ns: int
    level: str
    price: float
    signed_sigma_distance: float
    gaussian_probability_end_above: float
    gaussian_probability_end_below: float


@dataclass(frozen=True)
class ExpectedMove:
    horizon: MoveHorizon
    sigma_fraction: float
    sigma_points: float
    gaussian_expected_absolute_points: float
    local_gamma_expected_gross_change: float | None
    bands: tuple[SigmaBand, ...]
    candle_levels: tuple[LevelDistance, ...]


@dataclass(frozen=True)
class VolatilityAnalysis:
    model_id: str
    timestamp_ns: int
    selected_future: VolatilityFutureObservation
    visible_curve: tuple[VolatilityFutureObservation, ...]
    front_spread_points: float | None
    curve_shape: str
    proxy: ProxyAssumption
    anchor: CandleSnapshot
    level_candles: tuple[CandleSnapshot, ...]
    gamma: GammaSensitivity | None
    effective_volatility_pct: float
    moves: tuple[ExpectedMove, ...]
    warnings: tuple[str, ...]


def _curve_as_of(observations: Iterable[VolatilityFutureObservation], now: int,
                 max_age: int) -> tuple[VolatilityFutureObservation, ...]:
    values = tuple(observations)
    if any(type(item) is not VolatilityFutureObservation for item in values):
        raise ValueError("observations must be VolatilityFutureObservation values")
    grouped: dict[str, list[VolatilityFutureObservation]] = {}
    for item in values:
        if item.available_ns <= now:
            grouped.setdefault(item.contract_id, []).append(item)
    selected = []
    for contract, visible in sorted(grouped.items()):
        keys = {(item.reference_ns, item.available_ns) for item in visible}
        vintages = {(item.reference_ns, item.vintage_id) for item in visible}
        if len(keys) != len(visible) or len(vintages) != len(visible):
            raise ValueError(f"ambiguous volatility revision: {contract}")
        if len({item.expiry_cutoff_ns for item in visible}) != 1:
            raise ValueError(f"inconsistent contract expiry: {contract}")
        item = max(visible, key=lambda value: (value.reference_ns, value.available_ns))
        if now < item.expiry_cutoff_ns and now - item.reference_ns <= max_age:
            selected.append(item)
    return tuple(sorted(selected, key=lambda item: (item.expiry_cutoff_ns, item.contract_id)))


def analyze_vix_proxy(
    observations: Iterable[VolatilityFutureObservation], *, timestamp_ns: int,
    contract_id: str, anchor: CandleSnapshot, proxy: ProxyAssumption,
    horizons: Iterable[MoveHorizon] = DEFAULT_HORIZONS,
    candles: Iterable[CandleSnapshot] = (), gamma: GammaSensitivity | None = None,
    max_vol_age_ns: int = 3 * DAY_NS, max_anchor_age_ns: int = 5 * MINUTE_NS,
    max_gamma_age_ns: int = 5 * MINUTE_NS,
) -> VolatilityAnalysis:
    """Project sigma bands and endpoint distances to available OHLC levels.

    No auto-roll or curve interpolation. No probability of touching a level.
    No session calendar synthesis: one day is exactly 24 elapsed hours.
    """
    _time(timestamp_ns, "timestamp_ns")
    _identity(contract_id, "contract_id")
    for name, value in (("max_vol_age_ns", max_vol_age_ns),
                        ("max_anchor_age_ns", max_anchor_age_ns),
                        ("max_gamma_age_ns", max_gamma_age_ns)):
        _time(value, name)
        if value < 0:
            raise ValueError(f"{name} must be nonnegative")
    if type(anchor) is not CandleSnapshot or type(proxy) is not ProxyAssumption:
        raise ValueError("anchor/proxy must be canonical analysis values")
    if anchor.symbol != proxy.target_symbol:
        raise ValueError("anchor symbol does not match explicit proxy target")
    if anchor.available_ns > timestamp_ns or timestamp_ns - anchor.end_ns > max_anchor_age_ns:
        raise ValueError("anchor is unavailable or stale")
    if proxy.available_ns > timestamp_ns:
        raise ValueError("proxy assumption is not yet available")
    curve = _curve_as_of(observations, timestamp_ns, max_vol_age_ns)
    selected = next((item for item in curve if item.contract_id == contract_id), None)
    if selected is None:
        raise ValueError("selected VX contract is missing, unavailable, stale or expired; no auto-roll")
    if gamma is not None:
        if type(gamma) is not GammaSensitivity or gamma.symbol != anchor.symbol:
            raise ValueError("gamma must reference the same target symbol")
        if gamma.available_ns > timestamp_ns or timestamp_ns - gamma.reference_ns > max_gamma_age_ns:
            raise ValueError("gamma sensitivity is unavailable or stale")
    horizons = tuple(horizons)
    if (not horizons or any(type(item) is not MoveHorizon for item in horizons)
            or len({item.label for item in horizons}) != len(horizons)):
        raise ValueError("horizons must be nonempty with unique labels")
    levels: dict[str, CandleSnapshot] = {}
    for candle in candles:
        if type(candle) is not CandleSnapshot:
            raise ValueError("candles must be CandleSnapshot values")
        if candle.available_ns > timestamp_ns:
            continue
        if candle.symbol != anchor.symbol:
            raise ValueError("candle symbol does not match target")
        if candle.label in levels:
            raise ValueError("supply one available completed candle per label")
        levels[candle.label] = candle
    level_candles = tuple(levels[key] for key in sorted(levels))
    effective_vol = selected.value_pct * proxy.volatility_scale
    _number(effective_vol, "effective volatility", positive=True)
    moves = []
    for horizon in sorted(horizons, key=lambda item: (item.duration_ns, item.label)):
        if timestamp_ns + horizon.duration_ns >= 2**63:
            raise ValueError("horizon endpoint exceeds int64 timestamp range")
        fraction = (effective_vol / 100) * math.sqrt(horizon.duration_ns / YEAR_NS)
        sigma_points = anchor.close * fraction
        _number(sigma_points, "sigma_points", positive=True)
        # Do not publish negative-index-price bands or clip them to invent realism.
        if 3 * fraction >= 1:
            raise ValueError("three-sigma scenario exceeds positive-price approximation; use another model")
        bands = []
        for multiple in (1, 2, 3):
            distance = multiple * sigma_points
            gross = None if gamma is None else _gamma_change(
                gamma.dollars_per_point_squared, distance)
            lower, upper = anchor.close - distance, anchor.close + distance
            _number(lower, "lower band", positive=True)
            _number(upper, "upper band", positive=True)
            bands.append(SigmaBand(multiple, lower, upper,
                                   math.erf(multiple / math.sqrt(2)), gross))
        distances = []
        for candle in level_candles:
            for name in ("open", "high", "low", "close"):
                price = getattr(candle, name)
                z = (price - anchor.close) / sigma_points
                _number(z, "level sigma distance")
                distances.append(LevelDistance(
                    candle.label, candle.end_ns, timestamp_ns - candle.end_ns, name, price, z,
                    0.5 * math.erfc(z / math.sqrt(2)), 0.5 * math.erfc(-z / math.sqrt(2))))
        moves.append(ExpectedMove(horizon, fraction, sigma_points,
                                  sigma_points * math.sqrt(2 / math.pi),
                                  bands[0].local_gamma_gross_change,
                                  tuple(bands), tuple(distances)))
    spread = None
    shape = "insufficient_distinct_expiries"
    if len(curve) >= 2 and curve[0].expiry_cutoff_ns != curve[1].expiry_cutoff_ns:
        spread = curve[1].value_pct - curve[0].value_pct
        shape = "contango" if spread > 0 else "backwardation" if spread < 0 else "flat"
    warnings = [
        "VX is future SPX 30-day volatility, not current Nasdaq/NQ option implied volatility.",
        "Explicit cross-market scale is a scenario assumption, not verified calibration.",
        "Calendar365 constant-volatility normal endpoint probabilities are not calibrated forecasts or touch probabilities.",
        "Sigma bands are not predicted candle high/low ranges; daily means 24 elapsed hours.",
        "Gamma convexity excludes theta, option repricing, fees, spread, basis and hedge-path effects; not total PnL.",
    ]
    if selected.evidence == "synthetic":
        warnings.append("Selected VX input is synthetic, not market evidence.")
    if spread is not None and curve[0].reference_ns != curve[1].reference_ns:
        warnings.append("Front spread uses asynchronous reference times; it is an as-of diagnostic, not a simultaneous tradable spread.")
    if selected.discretionary:
        warnings.append("Selected settlement carries a discretionary-price flag.")
    elif selected.discretionary is None:
        warnings.append("Selected settlement discretionary-price status is unknown; absence of a marker is not confirmation.")
    return VolatilityAnalysis(MODEL_ID, timestamp_ns, selected, curve, spread, shape, proxy,
                              anchor, level_candles, gamma, effective_vol, tuple(moves), tuple(warnings))
