"""Pure, causal Nasdaq gamma decision policies; they never submit or simulate orders."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from qte.regimes import DealerObservation


_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def _timestamp(value: int, name: str) -> None:
    if type(value) is not int or not _INT64_MIN <= value <= _INT64_MAX:
        raise ValueError(f"{name} must be a signed 64-bit UTC nanosecond integer")


def _integer(value: int, name: str) -> None:
    if type(value) is not int or not _INT64_MIN <= value <= _INT64_MAX:
        raise ValueError(f"{name} must be an integer")


def _number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite")


def _identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def round_half_away_from_zero(value: float) -> int:
    """Deterministic integer rounding for contract targets, never bankers rounding."""
    _number(value, "value")
    if type(value) is int:
        return value
    fraction, whole = math.modf(abs(value))
    rounded = int(whole) + (1 if fraction >= 0.5 else 0)
    return -rounded if value < 0 else rounded


@dataclass(frozen=True)
class OptionPosition:
    contract_id: str
    quantity: int
    multiplier: int

    def __post_init__(self) -> None:
        _identity(self.contract_id, "contract_id")
        _integer(self.quantity, "quantity")
        _integer(self.multiplier, "multiplier")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")


@dataclass(frozen=True)
class VisibleOptionGreek:
    contract_id: str
    underlying_contract: str
    delta: float
    gamma: float
    reference_ns: int
    available_ns: int
    source_id: str
    vintage_id: str
    methodology_id: str
    delta_unit: str = "futures_equivalent"

    def __post_init__(self) -> None:
        _identity(self.contract_id, "contract_id")
        _identity(self.underlying_contract, "underlying_contract")
        _number(self.delta, "delta"); _number(self.gamma, "gamma")
        if not -1 <= self.delta <= 1 or self.gamma < 0:
            raise ValueError("delta must be in [-1, 1] and gamma nonnegative")
        _timestamp(self.reference_ns, "reference_ns"); _timestamp(self.available_ns, "available_ns")
        if self.reference_ns > self.available_ns:
            raise ValueError("reference_ns must not follow available_ns")
        for name in ("source_id", "vintage_id", "methodology_id"):
            _identity(getattr(self, name), name)
        if self.delta_unit != "futures_equivalent":
            raise ValueError("delta_unit must be futures_equivalent")


@dataclass(frozen=True)
class HedgeInstrument:
    contract_id: str
    reference_underlying: str
    multiplier: int
    max_contracts: int

    def __post_init__(self) -> None:
        _identity(self.contract_id, "contract_id")
        _identity(self.reference_underlying, "reference_underlying")
        _integer(self.multiplier, "multiplier")
        _integer(self.max_contracts, "max_contracts")
        if self.multiplier <= 0 or self.max_contracts <= 0:
            raise ValueError("hedge multiplier and cap must be positive")


@dataclass(frozen=True)
class GammaHedgeConfig:
    max_greek_age_ns: int = 300_000_000_000
    require_positive_gamma: bool = True

    def __post_init__(self) -> None:
        _timestamp(self.max_greek_age_ns, "max_greek_age_ns")
        if self.max_greek_age_ns < 0 or type(self.require_positive_gamma) is not bool:
            raise ValueError("invalid gamma hedge configuration")


@dataclass(frozen=True)
class HedgeDecision:
    target_contracts: int | None
    desired_contracts: int | None
    reason: str
    uncapped_contracts: int | None = None


class GammaHedgePolicy:
    """Converts visible option Greeks to a capped hedge target; no positions are mutated."""

    def __init__(self, config: GammaHedgeConfig = GammaHedgeConfig()) -> None:
        if not isinstance(config, GammaHedgeConfig):
            raise ValueError("config must be GammaHedgeConfig")
        self.config = config

    def decide(self, timestamp_ns: int, positions: Iterable[OptionPosition],
               greeks: Iterable[VisibleOptionGreek], hedge: HedgeInstrument, *,
               current_contracts: int, pending_target_contracts: int | None = None) -> HedgeDecision:
        _timestamp(timestamp_ns, "timestamp_ns")
        _integer(current_contracts, "current_contracts")
        if not isinstance(hedge, HedgeInstrument):
            raise ValueError("hedge must be HedgeInstrument")
        if pending_target_contracts is not None:
            _integer(pending_target_contracts, "pending_target_contracts")
            return HedgeDecision(None, None, "pending_hedge")
        try:
            positions = tuple(positions)
            greeks = tuple(greeks)
        except TypeError as error:
            raise ValueError("positions and greeks must be iterable") from error
        if any(not isinstance(item, OptionPosition) for item in positions):
            return self._blocked("invalid_position")
        if len({item.contract_id for item in positions}) != len(positions):
            return self._blocked("ambiguous_position")
        if any(item.quantity < 0 for item in positions):
            return self._blocked("short_option_inventory")
        positions = tuple(sorted((item for item in positions if item.quantity),
                                 key=lambda item: item.contract_id))
        if not positions:
            return self._flat(current_contracts, "no_option_positions")
        if any(not isinstance(item, VisibleOptionGreek) for item in greeks):
            return self._blocked("invalid_greek")
        deltas, gammas = [], []
        for position in positions:
            # Availability filtering precedes revision/provenance selection.
            visible = tuple(item for item in greeks if item.contract_id == position.contract_id
                            and item.available_ns <= timestamp_ns)
            if not visible:
                return self._blocked("missing_greek")
            if (len({(item.reference_ns, item.available_ns) for item in visible}) != len(visible)
                    or len({(item.reference_ns, item.vintage_id) for item in visible}) != len(visible)):
                return self._blocked("ambiguous_greek")
            if len({item.source_id for item in visible}) != 1:
                return self._blocked("ambiguous_greek_source")
            greek = max(visible, key=lambda item: (item.reference_ns, item.available_ns))
            if greek.underlying_contract != hedge.reference_underlying:
                return self._blocked("underlying_mismatch")
            if timestamp_ns - greek.reference_ns > self.config.max_greek_age_ns:
                return self._blocked("stale_greek")
            deltas.append(position.quantity * position.multiplier * greek.delta)
            gammas.append(position.quantity * position.multiplier * greek.gamma)
        try:
            delta_dollars = math.fsum(deltas)
            signed_gamma = math.fsum(gammas)
        except (OverflowError, ValueError):
            return self._blocked("aggregate_overflow")
        if not math.isfinite(delta_dollars) or not math.isfinite(signed_gamma):
            return self._blocked("aggregate_overflow")
        if self.config.require_positive_gamma and signed_gamma <= 0:
            return self._blocked("non_positive_gamma")
        uncapped = round_half_away_from_zero(-delta_dollars / hedge.multiplier)
        desired = max(-hedge.max_contracts, min(hedge.max_contracts, uncapped))
        reason = "hedge_target_capped" if desired != uncapped else "hedge_target"
        return HedgeDecision(None if desired == current_contracts else desired, desired, reason, uncapped)

    @staticmethod
    def _flat(current: int, reason: str) -> HedgeDecision:
        return HedgeDecision(None if current == 0 else 0, 0, reason, 0)

    @staticmethod
    def _blocked(reason: str) -> HedgeDecision:
        return HedgeDecision(None, None, reason, None)


@dataclass(frozen=True)
class FuturesMark:
    underlying: str
    reference_ns: int
    available_ns: int
    price: float
    source_id: str
    vintage_id: str

    def __post_init__(self) -> None:
        _identity(self.underlying, "underlying"); _number(self.price, "price")
        if self.price <= 0: raise ValueError("price must be positive")
        _timestamp(self.reference_ns, "reference_ns"); _timestamp(self.available_ns, "available_ns")
        if self.reference_ns > self.available_ns: raise ValueError("reference_ns must not follow available_ns")
        _identity(self.source_id, "source_id"); _identity(self.vintage_id, "vintage_id")


@dataclass(frozen=True)
class DealerGammaConfig:
    underlying: str = "NQ"
    max_dealer_age_ns: int = 86_400_000_000_000
    max_mark_age_ns: int = 300_000_000_000
    allow_model_estimates: bool = False
    rolling_window: int = 20
    entry_z: float = 1.0
    position_contracts: int = 1
    max_contracts: int = 1

    def __post_init__(self) -> None:
        _identity(self.underlying, "underlying"); _timestamp(self.max_dealer_age_ns, "max_dealer_age_ns"); _timestamp(self.max_mark_age_ns, "max_mark_age_ns")
        _integer(self.rolling_window, "rolling_window"); _integer(self.position_contracts, "position_contracts"); _integer(self.max_contracts, "max_contracts")
        _number(self.entry_z, "entry_z")
        if (self.max_dealer_age_ns < 0 or self.max_mark_age_ns < 0 or type(self.allow_model_estimates) is not bool or
                self.rolling_window < 2 or self.entry_z <= 0 or self.position_contracts <= 0 or
                self.position_contracts > self.max_contracts):
            raise ValueError("invalid dealer gamma configuration")


@dataclass(frozen=True)
class DealerDecision:
    target_contracts: int | None
    desired_contracts: int | None
    reason: str
    z_score: float | None
    dealer_input: DealerObservation | None


class DealerGammaPolicy:
    """Futures-only signal policy, not a claim about actual dealer inventory or gamma scalping.

    Positive-gamma entries mean-revert z-score extremes and exit at their
    lagged-mean cross. Negative-gamma entries require a strict lagged high/low
    breakout and exit at a lagged-mean cross. An unqualified or zero observation
    requests flat; a pending target suppresses all duplicate intents.
    """

    def __init__(self, observations: Iterable[DealerObservation],
                 config: DealerGammaConfig = DealerGammaConfig()) -> None:
        if not isinstance(config, DealerGammaConfig):
            raise ValueError("config must be DealerGammaConfig")
        try:
            values = tuple(observations)
        except TypeError as error:
            raise ValueError("observations must be iterable") from error
        if any(not isinstance(item, DealerObservation) for item in values):
            raise ValueError("observations must be DealerObservation values")
        if any(item.underlying != config.underlying for item in values):
            raise ValueError("dealer observation underlying must match configuration")
        if len({item.source_id for item in values}) > 1: raise ValueError("one dealer source_id is required")
        if (len({(item.reference_ns, item.available_ns) for item in values}) != len(values) or
                len({(item.reference_ns, item.vintage_id) for item in values}) != len(values)):
            raise ValueError("ambiguous dealer observation revision")
        self._observations, self.config = values, config

    def decide(self, timestamp_ns: int, marks: Iterable[FuturesMark], *, current_contracts: int,
               pending_target_contracts: int | None = None,
               active_gamma_sign: int | None = None) -> DealerDecision:
        _timestamp(timestamp_ns, "timestamp_ns")
        _integer(current_contracts, "current_contracts")
        if abs(current_contracts) > self.config.max_contracts:
            raise ValueError("current_contracts exceeds cap")
        if active_gamma_sign is not None and (type(active_gamma_sign) is not int
                                             or active_gamma_sign not in (-1, 1)):
            raise ValueError("active_gamma_sign must be -1, 1, or None")
        if current_contracts and active_gamma_sign is None:
            raise ValueError("active_gamma_sign is required with a live position")
        if pending_target_contracts is not None:
            _integer(pending_target_contracts, "pending_target_contracts")
            return DealerDecision(None, None, "pending_target", None, None)
        dealer = self._as_of(timestamp_ns)
        if dealer is None:
            return self._flat(current_contracts, "dealer_unqualified", None)
        sign = 1 if dealer.signed_gamma > 0 else -1 if dealer.signed_gamma < 0 else 0
        if sign == 0:
            return self._flat(current_contracts, "zero_gamma", dealer)
        if current_contracts and active_gamma_sign != sign:
            return self._flat(current_contracts, "gamma_regime_flip", dealer)
        points = self._marks_as_of(marks, timestamp_ns)
        if points is None or len(points) < self.config.rolling_window + 1:
            return self._flat(current_contracts, "insufficient_marks", dealer)
        lagged, current = points[-self.config.rolling_window - 1:-1], points[-1]
        try:
            # Scaling before accumulation/squaring avoids unnecessary overflow.
            mean = math.fsum(point / len(lagged) for point in lagged)
            scale = max(abs(point - mean) for point in lagged)
            stddev = 0.0 if scale == 0 else scale * math.sqrt(math.fsum(
                ((point - mean) / scale) ** 2 for point in lagged) / len(lagged))
        except (OverflowError, ValueError):
            return self._flat(current_contracts, "mark_aggregate_overflow", dealer)
        if not math.isfinite(stddev):
            return self._flat(current_contracts, "mark_aggregate_overflow", dealer)
        z = (current - mean) / stddev if stddev > 0 else None
        if z is not None and not math.isfinite(z):
            if sign > 0:
                return self._flat(current_contracts, "mark_aggregate_overflow", dealer)
            z = None  # Breakout uses price comparisons, not an undefined diagnostic z.
        # Existing exposure exits at its lagged mean cross, regardless of entry threshold.
        gap = current - mean
        mean_crossed = ((current_contracts > 0 and gap >= 0) or
                        (current_contracts < 0 and gap <= 0)) if sign > 0 else (
                        (current_contracts > 0 and gap <= 0) or
                        (current_contracts < 0 and gap >= 0))
        if current_contracts and mean_crossed:
            return self._flat(current_contracts, "mean_cross_exit", dealer, z)
        if sign > 0:
            if z is None:
                return self._flat(current_contracts, "zero_variance", dealer)
            if abs(z) < self.config.entry_z:
                return DealerDecision(None, current_contracts, "inside_entry_band", z, dealer)
            direction = -1 if z > 0 else 1
        elif current > max(lagged):
            direction = 1
        elif current < min(lagged):
            direction = -1
        else:
            return DealerDecision(None, current_contracts, "inside_breakout_range", z, dealer)
        desired = direction * self.config.position_contracts
        if current_contracts and current_contracts * desired < 0:
            return self._flat(current_contracts, "opposite_signal_exit", dealer, z)
        return DealerDecision(None if desired == current_contracts else desired, desired,
                              "positive_gamma_mean_reversion" if dealer.signed_gamma > 0 else "negative_gamma_breakout",
                              z, dealer)

    def _as_of(self, timestamp_ns: int) -> DealerObservation | None:
        eligible = [item for item in self._observations if item.available_ns <= timestamp_ns]
        if not eligible: return None
        reference = max(item.reference_ns for item in eligible)
        item = max((item for item in eligible if item.reference_ns == reference), key=lambda value: value.available_ns)
        if timestamp_ns - item.reference_ns > self.config.max_dealer_age_ns: return None
        if item.classification == "proxy" or (item.classification == "model_estimate" and not self.config.allow_model_estimates): return None
        return item

    def _marks_as_of(self, marks: Iterable[FuturesMark], timestamp_ns: int) -> list[float] | None:
        try:
            values = tuple(marks)
        except TypeError as error:
            raise ValueError("marks must be iterable") from error
        if any(not isinstance(item, FuturesMark) for item in values):
            return None
        eligible = [item for item in values if item.available_ns <= timestamp_ns]
        if any(item.underlying != self.config.underlying for item in eligible):
            return None
        if len({item.source_id for item in eligible}) > 1:
            return None
        if (len({(item.reference_ns, item.available_ns) for item in eligible}) != len(eligible) or
                len({(item.reference_ns, item.vintage_id) for item in eligible}) != len(eligible)):
            return None
        if not eligible: return []
        current = max(eligible, key=lambda item: (item.reference_ns, item.available_ns))
        if timestamp_ns - current.reference_ns > self.config.max_mark_age_ns:
            return None
        # Select each lagged revision AS OF the current reference time, not now.
        latest: dict[int, FuturesMark] = {}
        for item in eligible:
            if item.reference_ns >= current.reference_ns or item.available_ns > current.reference_ns:
                continue
            if item.reference_ns not in latest or item.available_ns > latest[item.reference_ns].available_ns:
                latest[item.reference_ns] = item
        history = sorted(latest.values(), key=lambda item: item.reference_ns)
        return [item.price for item in (*history, current)]

    @staticmethod
    def _flat(current: int, reason: str, dealer: DealerObservation | None, z: float | None = None) -> DealerDecision:
        return DealerDecision(None if current == 0 else 0, 0, reason, z, dealer)
