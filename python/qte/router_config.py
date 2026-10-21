"""Strict declarative construction for the macro strategy router."""
from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from typing import Any

from qte.regimes import DealerObservation, MacroObservation, MacroRegimeFilter, RegimeConfig
from qte.strategies import CandidateConfig, MacroRouterStrategy, RouterConfig


_EVIDENCE = ("synthetic", "user_declared")
_IDENTITY_FIELDS = ("source_id", "vintage_id", "methodology_id", "underlying", "dealer_underlying")


def _section(value: object, allowed: set[str], required: set[str] = set()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("router profile sections must be objects")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise ValueError(f"unknown router profile keys: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing router profile keys: {sorted(missing)}")
    return value


def _identity(value: object, name: str) -> None:
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(not character.isprintable() for character in value)):
        raise ValueError(f"{name} must be a nonempty control-free string of at most 256 characters")


@dataclass(frozen=True)
class RouterProfile:
    """Owned, validated inputs for one deterministic macro router."""

    evidence: str
    candidates: tuple[CandidateConfig, ...]
    macro_observations: tuple[MacroObservation, ...]
    dealer_observations: tuple[DealerObservation, ...]
    regime: RegimeConfig
    priorities: RouterConfig

    def make_filter(self) -> MacroRegimeFilter:
        return MacroRegimeFilter(
            self.macro_observations, self.regime,
            dealer_observations=self.dealer_observations,
        )


def _construct(spec: object, cls: type, *, required: set[str] = set()):
    definitions = fields(cls)
    names = {field.name for field in definitions}
    constructor_required = {
        field.name for field in definitions
        if field.default is MISSING and field.default_factory is MISSING
    }
    value = _section(spec, names, required | constructor_required)
    for name in _IDENTITY_FIELDS:
        if name in value:
            _identity(value[name], name)
    return cls(**value)


def prepare_router_profile(profile: object) -> RouterProfile:
    """Validate a JSON-shaped profile and take an immutable snapshot of it."""
    spec = _section(
        profile,
        {"evidence", "candidates", "macro_observations", "dealer_observations", "regime", "priorities"},
        {"evidence", "candidates", "macro_observations"},
    )
    evidence = spec["evidence"]
    if evidence not in _EVIDENCE:
        raise ValueError(f"evidence must be one of {_EVIDENCE}")

    candidate_specs = spec["candidates"]
    macro_specs = spec["macro_observations"]
    dealer_specs = spec.get("dealer_observations", [])
    if not isinstance(candidate_specs, list) or not 1 <= len(candidate_specs) <= 6:
        raise ValueError("candidates must contain between 1 and 6 objects")
    if not isinstance(macro_specs, list) or not isinstance(dealer_specs, list):
        raise ValueError("observations must be lists")
    if len(macro_specs) + len(dealer_specs) > 128:
        raise ValueError("combined observations cannot exceed 128")

    candidates = tuple(_construct(item, CandidateConfig, required={"kind"}) for item in candidate_specs)
    if len({item.kind for item in candidates}) != len(candidates):
        raise ValueError("router candidates must have unique kinds")
    macro = tuple(_construct(item, MacroObservation) for item in macro_specs)
    dealers = tuple(_construct(item, DealerObservation) for item in dealer_specs)
    regime = _construct(spec.get("regime", {}), RegimeConfig)
    priorities = _construct(spec.get("priorities", {}), RouterConfig)

    # Constructing the filter centrally applies source, vintage, underlying, and
    # revision ambiguity checks without changing proxy qualification semantics.
    MacroRegimeFilter(macro, regime, dealer_observations=dealers)
    return RouterProfile(evidence, candidates, macro, dealers, regime, priorities)


def build_macro_router(symbol: str, profile: RouterProfile) -> MacroRouterStrategy:
    """Build a fresh router from a prepared profile."""
    if not isinstance(profile, RouterProfile):
        raise TypeError("profile must be RouterProfile")
    _identity(symbol, "symbol")
    return MacroRouterStrategy(symbol, profile.candidates, profile.make_filter(), profile.priorities)
