"""Causal, explicit research policy; not a macro data or dealer-flow estimator."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


FEATURES = ("growth_z", "inflation_z", "liquidity_z", "volatility_pct")
FAMILIES = ("trend", "breakout", "contraction", "pullback", "mean_reversion", "rebound")
DAY_NS = 86_400_000_000_000


def _timestamp(value: int, name: str) -> None:
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError(f"{name} must be a signed 64-bit UTC nanosecond integer")


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


def _observation_metadata(reference_ns: int, available_ns: int,
                          source_id: str, vintage_id: str) -> None:
    _timestamp(reference_ns, "reference_ns")
    _timestamp(available_ns, "available_ns")
    if reference_ns > available_ns:
        raise ValueError("reference_ns must not follow available_ns")
    _identity(source_id, "source_id")
    _identity(vintage_id, "vintage_id")


@dataclass(frozen=True)
class MacroObservation:
    """A point-in-time transformed value, with release and transformation provenance."""

    feature: str
    value: float
    reference_ns: int
    available_ns: int
    source_id: str
    vintage_id: str

    def __post_init__(self) -> None:
        if self.feature not in FEATURES:
            raise ValueError(f"feature must be one of {FEATURES}")
        _number(self.value, "value")
        if self.feature == "volatility_pct" and self.value < 0:
            raise ValueError("volatility_pct must be nonnegative percentage points")
        _observation_metadata(self.reference_ns, self.available_ns,
                              self.source_id, self.vintage_id)


@dataclass(frozen=True)
class DealerObservation:
    """Externally supplied signed gamma, never inferred from price/volume bars."""

    signed_gamma: float
    reference_ns: int
    available_ns: int
    source_id: str
    vintage_id: str
    methodology_id: str
    underlying: str
    classification: str
    unit: str = "usd_per_1pct_move"

    def __post_init__(self) -> None:
        _number(self.signed_gamma, "signed_gamma")
        _observation_metadata(self.reference_ns, self.available_ns,
                              self.source_id, self.vintage_id)
        _identity(self.methodology_id, "methodology_id")
        _identity(self.underlying, "underlying")
        if self.classification not in ("observed", "model_estimate", "proxy"):
            raise ValueError("classification must be observed, model_estimate, or proxy")
        if self.unit != "usd_per_1pct_move":
            raise ValueError("dealer gamma unit must be usd_per_1pct_move")


@dataclass(frozen=True)
class RegimeConfig:
    max_macro_age_ns: int = 45 * DAY_NS
    max_volatility_age_ns: int = 4 * DAY_NS
    max_dealer_age_ns: int = DAY_NS
    volatility_low: float = 15.0
    volatility_high: float = 25.0
    volatility_extreme: float = 40.0
    risk_on_threshold: float = 0.5
    risk_off_threshold: float = -0.5
    dealer_mode: str = "ignore"
    allow_model_estimates: bool = False
    dealer_underlying: str = "SPX"

    def __post_init__(self) -> None:
        for name in ("max_macro_age_ns", "max_volatility_age_ns", "max_dealer_age_ns"):
            value = getattr(self, name)
            _timestamp(value, name)
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
        for name in ("volatility_low", "volatility_high", "volatility_extreme",
                     "risk_on_threshold", "risk_off_threshold"):
            _number(getattr(self, name), name)
        if not 0 <= self.volatility_low < self.volatility_high < self.volatility_extreme:
            raise ValueError("volatility thresholds must be nonnegative and strictly increasing")
        if self.risk_off_threshold >= self.risk_on_threshold:
            raise ValueError("risk_off_threshold must be below risk_on_threshold")
        if self.dealer_mode not in ("ignore", "optional", "required"):
            raise ValueError("dealer_mode must be ignore, optional, or required")
        if type(self.allow_model_estimates) is not bool:
            raise ValueError("allow_model_estimates must be bool")
        _identity(self.dealer_underlying, "dealer_underlying")


@dataclass(frozen=True)
class RegimeDecision:
    timestamp_ns: int
    macro_regime: str
    volatility_regime: str
    dealer_regime: str
    macro_score: float | None
    permitted_families: tuple[str, ...]
    reasons: tuple[str, ...]
    macro_inputs: tuple[MacroObservation, ...]
    dealer_input: DealerObservation | None


def _validated_series(observations: Iterable, expected_type: type) -> tuple:
    observations = tuple(observations)
    seen_keys: set = set()
    seen_vintages: set = set()
    sources: set = set()
    for item in observations:
        if not isinstance(item, expected_type):
            raise ValueError(f"observations must be {expected_type.__name__} values")
        key = (item.reference_ns, item.available_ns)
        vintage = (item.reference_ns, item.vintage_id)
        if key in seen_keys or vintage in seen_vintages:
            raise ValueError("duplicate or ambiguous observation/revision")
        seen_keys.add(key)
        seen_vintages.add(vintage)
        sources.add(item.source_id)
    if len(sources) > 1:
        raise ValueError("one source_id per feature/underlying is required")
    return tuple(sorted(observations, key=lambda item: (item.reference_ns, item.available_ns)))


def _as_of(observations: tuple, timestamp_ns: int):
    # Reference period is primary: revising an older period does not erase a newer one.
    return next((item for item in reversed(observations)
                 if item.available_ns <= timestamp_ns), None)


class MacroRegimeFilter:
    """Read-only as-of entry-permission policy for long-only US equity research.

    Z-scores must already be constructed using only then-available data and a
    registered transformation. Source identity must identify that transformation.
    No provider payloads, portfolio mutation, data fetching, or clock reads occur.
    """

    def __init__(self, macro_observations: Iterable[MacroObservation],
                 config: RegimeConfig = RegimeConfig(), *,
                 dealer_observations: Iterable[DealerObservation] = ()) -> None:
        if not isinstance(config, RegimeConfig):
            raise ValueError("config must be RegimeConfig")
        self.config = config
        macro = tuple(macro_observations)
        if any(not isinstance(item, MacroObservation) for item in macro):
            raise ValueError("macro_observations must be MacroObservation values")
        self._macro = tuple((feature, _validated_series(
            (item for item in macro if item.feature == feature), MacroObservation
        )) for feature in FEATURES)
        dealers = tuple(dealer_observations)
        if any(not isinstance(item, DealerObservation) for item in dealers):
            raise ValueError("dealer_observations must be DealerObservation values")
        if any(item.underlying != config.dealer_underlying for item in dealers):
            raise ValueError("dealer observation underlying must match dealer_underlying")
        self._dealers = _validated_series(dealers, DealerObservation)

    def __call__(self, timestamp_ns: int, family: str) -> bool:
        if family not in FAMILIES:
            raise ValueError(f"unknown strategy family: {family}")
        return family in self.explain(timestamp_ns).permitted_families

    def explain(self, timestamp_ns: int) -> RegimeDecision:
        _timestamp(timestamp_ns, "timestamp_ns")
        reasons: list[str] = []
        inputs: list[MacroObservation] = []
        values: dict[str, float] = {}
        for feature, series in self._macro:
            item = _as_of(series, timestamp_ns)
            if item is None:
                reasons.append(f"missing:{feature}")
                continue
            inputs.append(item)
            age_limit = (self.config.max_volatility_age_ns if feature == "volatility_pct"
                         else self.config.max_macro_age_ns)
            # Measure age from the economic reference, not the newest revision's release.
            if timestamp_ns - item.reference_ns > age_limit:
                reasons.append(f"stale:{feature}")
                continue
            values[feature] = item.value

        dealer = None
        dealer_regime = "ignored" if self.config.dealer_mode == "ignore" else "unknown"
        qualified = False
        if self.config.dealer_mode != "ignore":
            dealer = _as_of(self._dealers, timestamp_ns)
            if dealer is None:
                reasons.append("dealer:missing")
            elif timestamp_ns - dealer.reference_ns > self.config.max_dealer_age_ns:
                reasons.append("dealer:stale")
            elif dealer.classification == "proxy":
                reasons.append("dealer:proxy_not_positioning")
            elif dealer.classification == "model_estimate" and not self.config.allow_model_estimates:
                reasons.append("dealer:model_estimate_not_enabled")
            else:
                qualified = True
                dealer_regime = ("negative_gamma" if dealer.signed_gamma < 0 else
                                 "positive_gamma" if dealer.signed_gamma > 0 else "zero_gamma")
                reasons.append(f"dealer:{dealer.classification}:{dealer_regime}")

        score = None
        macro_regime = "unknown"
        volatility_regime = "unknown"
        permitted: set[str] = set()
        if len(values) == len(FEATURES):
            # Scale first: three rounded MAX_FLOAT / 3 terms can still overflow.
            terms = (values["growth_z"], values["liquidity_z"], -values["inflation_z"])
            scale = max(abs(value) for value in terms)
            score = 0.0 if scale == 0 else scale * (math.fsum(value / scale for value in terms) / 3)
            macro_regime = ("risk_on" if score >= self.config.risk_on_threshold else
                            "risk_off" if score <= self.config.risk_off_threshold else "neutral")
            vol = values["volatility_pct"]
            volatility_regime = ("extreme" if vol >= self.config.volatility_extreme else
                                 "high" if vol >= self.config.volatility_high else
                                 "low" if vol < self.config.volatility_low else "normal")
            reasons.extend((f"macro:{macro_regime}", f"volatility:{volatility_regime}"))
            if macro_regime == "risk_on" and volatility_regime != "extreme":
                permitted = {"trend", "breakout"}
                if volatility_regime in ("low", "normal"):
                    permitted.update(("contraction", "pullback", "mean_reversion"))
                if volatility_regime == "high":
                    permitted.add("rebound")
            elif macro_regime == "neutral" and volatility_regime in ("low", "normal"):
                permitted = {"mean_reversion", "pullback"}
            # Risk-off/extreme always wins; gamma never overrides the macro gate.
            if qualified and dealer_regime == "negative_gamma":
                permitted.difference_update(("mean_reversion", "pullback", "rebound"))
                reasons.append("dealer_overlay:remove_countertrend")
            elif qualified and dealer_regime == "positive_gamma":
                permitted.difference_update(("breakout", "contraction"))
                reasons.append("dealer_overlay:remove_breakout")
        if self.config.dealer_mode == "required" and not qualified:
            permitted.clear()
            reasons.append("dealer_required:blocked")
        return RegimeDecision(timestamp_ns, macro_regime, volatility_regime, dealer_regime,
                              score, tuple(family for family in FAMILIES if family in permitted),
                              tuple(reasons), tuple(inputs), dealer)
