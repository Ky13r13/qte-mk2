from dataclasses import FrozenInstanceError, replace
import math
import sys

import pytest

from qte.regimes import (
    DAY_NS, DealerObservation, FAMILIES, FEATURES, MacroObservation,
    MacroRegimeFilter, RegimeConfig,
)


def observations(*, growth=1.5, inflation=0.0, liquidity=1.5, volatility=12.0,
                 reference=0, available=1):
    return tuple(MacroObservation(feature, value, reference, available,
                                  f"synthetic:{feature}:script-v1", "v1")
                 for feature, value in zip(FEATURES, (growth, inflation, liquidity, volatility)))


def dealer(gamma=100.0, **changes):
    value = DealerObservation(gamma, 0, 1, "synthetic-dealer", "v1", "script-v1",
                              "SPX", "observed")
    return replace(value, **changes)


@pytest.mark.parametrize("score,expected", [(-0.500001, "risk_off"), (-0.5, "risk_off"),
                                            (-0.499999, "neutral"), (0.499999, "neutral"),
                                            (0.5, "risk_on"), (0.500001, "risk_on")])
def test_macro_thresholds_are_explicit_and_inclusive(score, expected):
    gate = MacroRegimeFilter(observations(growth=score * 3, liquidity=0))
    assert gate.explain(1).macro_regime == expected
    assert gate.explain(1).macro_score == pytest.approx(score)


@pytest.mark.parametrize("vol,regime", [(0, "low"), (14.999, "low"), (15, "normal"),
                                       (24.999, "normal"), (25, "high"),
                                       (39.999, "high"), (40, "extreme")])
def test_volatility_thresholds_use_annualized_percentage_points(vol, regime):
    gate = MacroRegimeFilter(observations(volatility=vol))
    assert gate.explain(1).volatility_regime == regime


@pytest.mark.parametrize("growth,volatility,families", [
    (1.5, 12, ("trend", "breakout", "contraction", "pullback", "mean_reversion")),
    (1.5, 30, ("trend", "breakout", "rebound")),
    (1.5, 40, ()),
    (-1.5, 12, ("pullback", "mean_reversion")),
    (-1.5, 30, ()),
    (-3.0, 12, ()),
])
def test_top_down_route_table(growth, volatility, families):
    gate = MacroRegimeFilter(observations(growth=growth, volatility=volatility))
    decision = gate.explain(1)
    assert decision.permitted_families == families
    assert tuple(family for family in FAMILIES if gate(1, family)) == families


def test_unavailable_data_is_not_exposed_and_exact_availability_is_visible():
    gate = MacroRegimeFilter(observations(available=100))
    before = gate.explain(99)
    assert before.macro_inputs == ()
    assert before.permitted_families == ()
    assert before.macro_score is None
    assert before.reasons == tuple(f"missing:{name}" for name in FEATURES)
    assert gate.explain(100).macro_regime == "risk_on"
    assert all(item.available_ns <= 100 for item in gate.explain(100).macro_inputs)


def test_missing_macro_is_fail_closed_not_neutral_and_never_backfilled():
    gate = MacroRegimeFilter(observations()[:-1])
    assert not gate(1, "trend")
    assert "missing:volatility_pct" in gate.explain(1).reasons
    assert gate.explain(1).macro_regime == "unknown"


def test_age_is_reference_age_not_revision_age_and_limit_is_inclusive():
    base = observations(available=10)
    revision = replace(base[0], available_ns=99, vintage_id="v2", value=2.0)
    gate = MacroRegimeFilter((*base, revision), RegimeConfig(max_macro_age_ns=100,
                                                           max_volatility_age_ns=100))
    assert gate(100, "trend")
    assert not gate(101, "trend")
    assert "stale:growth_z" in gate.explain(101).reasons


def test_macro_and_volatility_have_independent_staleness_limits():
    gate = MacroRegimeFilter(observations(), RegimeConfig(max_macro_age_ns=10,
                                                       max_volatility_age_ns=3))
    assert gate(3, "trend")
    assert not gate(4, "trend")
    assert gate.explain(4).reasons == ("stale:volatility_pct",)


def test_revisions_apply_only_after_release_and_newest_reference_remains_primary():
    base = observations(growth=0, liquidity=0)
    revision = replace(base[0], value=3, available_ns=10, vintage_id="v2")
    next_period = replace(base[0], value=-3, reference_ns=5, available_ns=11, vintage_id="v3")
    late_old_revision = replace(base[0], value=30, available_ns=12, vintage_id="v4")
    gate = MacroRegimeFilter((*base, revision, next_period, late_old_revision))
    assert gate.explain(9).macro_score == 0
    assert gate.explain(10).macro_score == 1
    assert gate.explain(11).macro_score == -1
    assert gate.explain(12).macro_score == -1
    assert gate.explain(12).macro_inputs[0] == next_period


def test_input_order_cannot_change_decision_and_inputs_are_owned():
    data = list(observations())
    gate = MacroRegimeFilter(data)
    other = MacroRegimeFilter(reversed(data))
    data.clear()
    assert gate.explain(1) == other.explain(1)
    with pytest.raises(FrozenInstanceError):
        gate.explain(1).macro_inputs[0].value = 999


@pytest.mark.parametrize("kind", ["identical", "same_time", "reused_vintage", "second_source"])
def test_ambiguous_duplicate_revision_or_source_is_rejected(kind):
    base = observations()
    extra = {"identical": base[0],
             "same_time": replace(base[0], vintage_id="v2", value=2),
             "reused_vintage": replace(base[0], available_ns=2, value=2),
             "second_source": replace(base[0], available_ns=2, vintage_id="v2", source_id="other")}[kind]
    with pytest.raises(ValueError):
        MacroRegimeFilter((*base, extra))


def test_dealer_is_ignored_by_default_even_when_provided():
    gate = MacroRegimeFilter(observations(), dealer_observations=[dealer(-10)])
    assert gate(1, "mean_reversion")
    assert gate.explain(1).dealer_regime == "ignored"
    assert gate.explain(1).dealer_input is None


def test_missing_dealer_optional_is_not_fabricated_and_required_blocks():
    optional = MacroRegimeFilter(observations(), RegimeConfig(dealer_mode="optional"))
    required = MacroRegimeFilter(observations(), RegimeConfig(dealer_mode="required"))
    assert optional(1, "trend")
    assert optional.explain(1).dealer_regime == "unknown"
    assert optional.explain(1).dealer_input is None
    assert "dealer:missing" in optional.explain(1).reasons
    assert not required(1, "trend")
    assert "dealer_required:blocked" in required.explain(1).reasons


@pytest.mark.parametrize("gamma,removed,retained,regime", [
    (-100, "mean_reversion", "breakout", "negative_gamma"),
    (100, "breakout", "mean_reversion", "positive_gamma"),
])
def test_qualified_dealer_overlay_can_only_remove_permissions(gamma, removed, retained, regime):
    config = RegimeConfig(dealer_mode="required")
    gate = MacroRegimeFilter(observations(), config, dealer_observations=[dealer(gamma)])
    assert not gate(1, removed)
    assert gate(1, retained)
    assert gate.explain(1).dealer_regime == regime
    extreme = MacroRegimeFilter(observations(volatility=40), config, dealer_observations=[dealer(gamma)])
    assert extreme.explain(1).permitted_families == ()
    risk_off = MacroRegimeFilter(observations(growth=-10), config, dealer_observations=[dealer(gamma)])
    assert risk_off.explain(1).permitted_families == ()


def test_zero_gamma_is_qualified_but_does_not_change_macro_routing():
    baseline = MacroRegimeFilter(observations()).explain(1)
    gate = MacroRegimeFilter(observations(), RegimeConfig(dealer_mode="required"),
                            dealer_observations=[dealer(0)])
    assert gate.explain(1).permitted_families == baseline.permitted_families
    assert gate.explain(1).dealer_regime == "zero_gamma"


@pytest.mark.parametrize("classification,allow,qualified", [
    ("observed", False, True), ("observed", True, True),
    ("model_estimate", False, False), ("model_estimate", True, True),
    ("proxy", False, False), ("proxy", True, False),
])
def test_estimates_need_opt_in_and_proxy_never_becomes_positioning(classification, allow, qualified):
    config = RegimeConfig(dealer_mode="required", allow_model_estimates=allow)
    gate = MacroRegimeFilter(observations(), config,
                            dealer_observations=[dealer(classification=classification)])
    assert gate(1, "trend") is qualified
    assert (gate.explain(1).dealer_regime != "unknown") is qualified


def test_dealer_visibility_and_staleness_are_causal():
    gate = MacroRegimeFilter(observations(), RegimeConfig(dealer_mode="required", max_dealer_age_ns=10),
                            dealer_observations=[dealer(available_ns=5)])
    assert not gate(4, "trend")
    assert gate.explain(4).dealer_input is None
    assert gate(5, "trend")
    assert gate(10, "trend")
    assert not gate(11, "trend")
    assert "dealer:stale" in gate.explain(11).reasons


def test_dealer_underlying_and_duplicate_sources_cannot_be_silently_mixed():
    with pytest.raises(ValueError, match="underlying"):
        MacroRegimeFilter(observations(), dealer_observations=[dealer(underlying="QQQ")])
    with pytest.raises(ValueError, match="duplicate"):
        MacroRegimeFilter(observations(), dealer_observations=[dealer(), dealer()])


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "1", None, 10**400])
def test_all_numeric_data_and_thresholds_reject_nonfinite_or_wrong_types(value):
    with pytest.raises(ValueError):
        replace(observations()[0], value=value)
    with pytest.raises(ValueError):
        dealer(value)
    for name in ("volatility_low", "volatility_high", "volatility_extreme",
                 "risk_on_threshold", "risk_off_threshold"):
        with pytest.raises(ValueError):
            RegimeConfig(**{name: value})


@pytest.mark.parametrize("value", [True, 1.0, "1", 2**63, -(2**63)-1])
def test_timestamps_require_int64(value):
    with pytest.raises(ValueError):
        replace(observations()[0], available_ns=value)
    with pytest.raises(ValueError):
        dealer(reference_ns=value)
    with pytest.raises(ValueError):
        MacroRegimeFilter(observations()).explain(value)


def test_validation_includes_provenance_and_configuration_boundaries():
    for field in ("source_id", "vintage_id"):
        with pytest.raises(ValueError):
            replace(observations()[0], **{field: " "})
    with pytest.raises(ValueError, match="reference_ns"):
        replace(observations()[0], reference_ns=2)
    with pytest.raises(ValueError):
        replace(observations()[-1], value=-1)
    with pytest.raises(ValueError):
        replace(observations()[0], feature="latest_revised_GDP")
    for kwargs in ({"volatility_low": 25}, {"volatility_high": 40},
                   {"risk_off_threshold": .5}, {"max_macro_age_ns": -1},
                   {"max_volatility_age_ns": True}, {"dealer_mode": "guess"},
                   {"allow_model_estimates": 1}):
        with pytest.raises(ValueError):
            RegimeConfig(**kwargs)
    for kwargs in ({"classification": "known"}, {"unit": "shares"},
                   {"methodology_id": ""}, {"underlying": ""}):
        with pytest.raises(ValueError):
            dealer(**kwargs)
    with pytest.raises(ValueError):
        MacroRegimeFilter([object()])
    with pytest.raises(ValueError):
        MacroRegimeFilter(observations(), dealer_observations=[object()])
    with pytest.raises(ValueError):
        MacroRegimeFilter(observations())(1, "unregistered-family")


@pytest.mark.parametrize("value", [1e308, sys.float_info.max])
def test_large_finite_macro_values_do_not_overflow_the_equal_weight_average(value):
    gate = MacroRegimeFilter(observations(growth=value, liquidity=value, inflation=-value))
    assert math.isfinite(gate.explain(1).macro_score)
    assert gate.explain(1).macro_regime == "risk_on"
