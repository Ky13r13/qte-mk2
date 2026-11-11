from dataclasses import replace
import math

import pytest

from qte.regimes import DealerObservation
from qte.strategies.nasdaq_gamma import (
    DealerGammaConfig, DealerGammaPolicy, FuturesMark, GammaHedgeConfig,
    GammaHedgePolicy, HedgeInstrument, OptionPosition, VisibleOptionGreek,
    round_half_away_from_zero,
)


def greek(contract="call", delta=.5, gamma=.01, available=10, underlying="NQZ6"):
    return VisibleOptionGreek(contract, underlying, delta, gamma, 0, available,
                              "licensed", "v1", "black76-v1")


def hedge(cap=10): return HedgeInstrument("MNQZ6", "NQZ6", 2, cap)


def position(contract="call", quantity=1, multiplier=20): return OptionPosition(contract, quantity, multiplier)


def dealer(gamma, reference=0, available=1, **changes):
    return replace(DealerObservation(gamma, reference, available, "licensed", "v1", "method-v1", "NQ", "observed"),
                   **changes)


def marks(values, *, available_offset=0):
    return tuple(FuturesMark("NQ", index, index + available_offset, value, "licensed", f"v{index}")
                 for index, value in enumerate(values))


@pytest.mark.parametrize("value,expected", [(-1.5, -2), (-.5, -1), (-.49, 0), (0, 0), (.5, 1), (1.5, 2)])
def test_rounding_is_half_away_from_zero(value, expected):
    assert round_half_away_from_zero(value) == expected


def test_nq_option_delta_hedges_with_ten_mnq_and_cap():
    policy = GammaHedgePolicy()
    decision = policy.decide(10, [position("call"), position("put")],
                             [greek("call", .5), greek("put", -.2)], hedge(), current_contracts=0)
    assert decision.target_contracts == -3 and decision.desired_contracts == -3
    capped = policy.decide(10, [position()], [greek(delta=1)], hedge(3), current_contracts=0)
    assert capped.target_contracts == -3


@pytest.mark.parametrize("change,reason", [
    ({"underlying": "NQU6"}, "underlying_mismatch"),
    ({"available": 11}, "missing_greek"),
    ({"available": 0}, "stale_greek"),
])
def test_gamma_policy_fails_closed_on_invisible_stale_or_wrong_underlying(change, reason):
    decision = GammaHedgePolicy(GammaHedgeConfig(max_greek_age_ns=5)).decide(
        10, [position()], [greek(**change)], hedge(), current_contracts=2)
    assert decision.target_contracts is None and decision.desired_contracts is None and reason in decision.reason


def test_wrong_delta_units_are_rejected_at_the_boundary():
    with pytest.raises(ValueError):
        replace(greek(), delta_unit="dollars")


def test_gamma_policy_requires_authoritative_complete_positive_gamma_and_suppresses_pending():
    policy = GammaHedgePolicy()
    assert policy.decide(10, [position()], [], hedge(), current_contracts=0).reason == "missing_greek"
    assert policy.decide(10, [position(quantity=-1)], [greek()], hedge(), current_contracts=2).target_contracts is None
    pending = policy.decide(10, [position()], [greek()], hedge(), current_contracts=0, pending_target_contracts=-4)
    assert pending.target_contracts is None and pending.reason == "pending_hedge"


@pytest.mark.parametrize("bad", [True, 1.1, math.inf, "1"])
def test_quantity_and_numeric_boundaries_are_strict(bad):
    with pytest.raises(ValueError): OptionPosition("x", bad, 20)
    with pytest.raises(ValueError): HedgeInstrument("m", "n", 2, bad)


@pytest.mark.parametrize("bad", [True, math.inf, -math.inf, math.nan, "1", None])
def test_rounding_rejects_non_numeric_or_nonfinite_values(bad):
    with pytest.raises(ValueError): round_half_away_from_zero(bad)


def test_dealer_positive_mean_reversion_negative_breakout_and_exit_rules():
    values = [100, 101, 99, 100, 110]
    positive = DealerGammaPolicy([dealer(1)], DealerGammaConfig(rolling_window=4, entry_z=1))
    negative = DealerGammaPolicy([dealer(-1)], DealerGammaConfig(rolling_window=4, entry_z=1))
    assert positive.decide(10, marks(values), current_contracts=0).target_contracts == -1
    assert negative.decide(10, marks(values), current_contracts=0).target_contracts == 1
    assert positive.decide(10, marks([100, 101, 99, 100, 100]), current_contracts=-1, active_gamma_sign=1).target_contracts == 0
    assert negative.decide(10, marks([100, 101, 99, 100, 100]), current_contracts=1, active_gamma_sign=-1).target_contracts == 0


@pytest.mark.parametrize("gamma,classification,allow,max_age,reason", [
    (0, "observed", False, 10, "zero_gamma"), (1, "proxy", True, 10, "dealer_unqualified"),
    (1, "model_estimate", False, 10, "dealer_unqualified"), (1, "observed", False, 1, "dealer_unqualified"),
])
def test_dealer_unknown_zero_proxy_estimate_and_stale_flatten(gamma, classification, allow, max_age, reason):
    config = DealerGammaConfig(rolling_window=2, allow_model_estimates=allow, max_dealer_age_ns=max_age)
    obs = dealer(gamma, reference=0, available=0, classification=classification)
    decision = DealerGammaPolicy([obs], config).decide(2, marks([1, 2, 3]), current_contracts=1, active_gamma_sign=1)
    assert decision.target_contracts == 0 and decision.reason == reason


def test_dealer_uses_latest_visible_reference_then_revision_and_never_falls_back_bad_provenance():
    old, new = dealer(1, reference=1, available=1), dealer(-1, reference=2, available=2, classification="proxy")
    policy = DealerGammaPolicy([old, new], DealerGammaConfig(rolling_window=2))
    assert policy.decide(1, marks([1, 2, 3]), current_contracts=1, active_gamma_sign=1).reason != "dealer_unqualified"
    assert policy.decide(2, marks([1, 2, 3]), current_contracts=1, active_gamma_sign=1).reason == "dealer_unqualified"
    with pytest.raises(ValueError): DealerGammaPolicy([old, replace(old, vintage_id="v2")])


def test_dealer_lagged_history_and_no_same_event_reversal_or_regime_flip():
    policy = DealerGammaPolicy([dealer(-1)], DealerGammaConfig(rolling_window=2, entry_z=.5))
    reversal = policy.decide(10, marks([100, 101, 120]), current_contracts=-1, active_gamma_sign=-1)
    assert reversal.target_contracts == 0
    assert reversal.reason == "mean_cross_exit"
    assert policy.decide(10, marks([100, 101, 120]), current_contracts=1, active_gamma_sign=1).reason == "gamma_regime_flip"
    # A revision arriving after the current mark cannot enter its lagged baseline.
    late = marks([100, 101, 120])[:-1] + (FuturesMark("NQ", 2, 20, 120, "licensed", "v2"),)
    assert policy.decide(10, late, current_contracts=1, active_gamma_sign=-1).reason == "insufficient_marks"


def test_pending_and_inside_band_do_not_create_duplicate_intents():
    policy = DealerGammaPolicy([dealer(1)], DealerGammaConfig(rolling_window=2, entry_z=2))
    assert policy.decide(10, marks([100, 101, 100.5]), current_contracts=0).target_contracts is None
    pending = policy.decide(10, marks([100, 101, 120]), current_contracts=0, pending_target_contracts=1)
    assert pending.target_contracts is None and pending.reason == "pending_target"


@pytest.mark.parametrize("value,expected", [
    (1.1, 1), (math.nextafter(.5, 0), 0), (-math.nextafter(.5, 0), 0),
    (math.nextafter(.5, 1), 1), (2**63 - 1, 2**63 - 1),
])
def test_rounding_fraction_neighbors_and_large_integer_precision(value, expected):
    assert round_half_away_from_zero(value) == expected


@pytest.mark.parametrize("value", [True, 1.1, 0, -1, 2**63, math.nan, math.inf, "20"])
def test_multiplier_and_cap_domains_match_core_contracts(value):
    with pytest.raises(ValueError): position(multiplier=value)
    with pytest.raises(ValueError): HedgeInstrument("m", "n", value, 1)
    with pytest.raises(ValueError): hedge(value)


@pytest.mark.parametrize("changes", [
    {"delta": math.nan}, {"delta": math.inf}, {"delta": 100}, {"delta": -2},
    {"delta": "0.5"}, {"delta": True}, {"gamma": -1}, {"gamma": math.nan},
    {"available_ns": True}, {"reference_ns": 11}, {"methodology_id": " "},
])
def test_greek_units_times_provenance_and_domains(changes):
    with pytest.raises(ValueError): replace(greek(), **changes)


def test_greek_revisions_are_causal_latest_reference_primary_and_order_independent():
    policy = GammaHedgePolicy()
    first = greek(delta=.4)
    later = replace(first, delta=.7, available_ns=20, vintage_id="v2")
    future = replace(first, delta=1, reference_ns=100, available_ns=100,
                     source_id="future-feed", vintage_id="v3")
    at_ten = policy.decide(10, [position()], [first], hedge(), current_contracts=0)
    assert policy.decide(10, [position()], [future, later, first], hedge(),
                         current_contracts=0) == at_ten
    assert policy.decide(20, [position()], [first, later], hedge(), current_contracts=0).target_contracts == -7
    newer_ref = replace(first, delta=.2, reference_ns=15, available_ns=15, vintage_id="v4")
    assert policy.decide(20, [position()], [first, later, newer_ref], hedge(), current_contracts=0).target_contracts == -2
    conflict = replace(first, delta=.3, vintage_id="conflict")
    assert policy.decide(10, [position()], [first, conflict], hedge(), current_contracts=2).reason == "ambiguous_greek"


def test_greek_age_uses_reference_and_missing_greek_does_not_remove_live_hedge():
    policy = GammaHedgePolicy(GammaHedgeConfig(max_greek_age_ns=5))
    for observations in ([], [greek(available=10)]):
        result = policy.decide(10, [position()], observations, hedge(), current_contracts=-5)
        assert result.target_contracts is None and result.desired_contracts is None
    assert policy.decide(10, [position(quantity=0)], [], hedge(), current_contracts=-5).target_contracts == 0
    assert policy.decide(10, [], [], hedge(), current_contracts=0).desired_contracts == 0


def test_gamma_math_is_order_invariant_and_capping_is_explicit():
    policy = GammaHedgePolicy()
    positions = [position("a", quantity=2**53), position("b"), position("c", quantity=2**53)]
    observations = [greek("a", delta=1), greek("b", delta=.5), greek("c", delta=-1)]
    expected = policy.decide(10, positions, observations, hedge(), current_contracts=0)
    assert expected.target_contracts == -5
    assert policy.decide(10, reversed(positions), reversed(observations), hedge(), current_contracts=0) == expected
    cap = policy.decide(10, [position()], [greek(delta=1)], hedge(3), current_contracts=0)
    assert (cap.uncapped_contracts, cap.desired_contracts, cap.reason) == (-10, -3, "hedge_target_capped")
    overflow = policy.decide(10, [position(quantity=2)], [greek(gamma=1e308)], hedge(), current_contracts=0)
    assert overflow.reason == "aggregate_overflow" and overflow.target_contracts is None


def test_lagged_price_revision_is_selected_at_reference_cutoff_not_decision_time():
    policy = DealerGammaPolicy([dealer(-1)], DealerGammaConfig(rolling_window=2))
    original = marks([100, 101, 120])
    revision = replace(original[0], available_ns=5, price=1000, vintage_id="later")
    expected = policy.decide(10, original, current_contracts=0)
    assert expected.target_contracts == 1
    assert policy.decide(10, (*original, revision), current_contracts=0) == expected
    future = replace(original[1], available_ns=20, source_id="other", vintage_id="future")
    assert policy.decide(10, (*original, future), current_contracts=0) == expected


def test_mark_freshness_and_ambiguity_fail_closed_with_explicit_exit_intent():
    policy = DealerGammaPolicy([dealer(-1)], DealerGammaConfig(rolling_window=2, max_mark_age_ns=5))
    original = marks([100, 101, 120])
    stale = policy.decide(10, original, current_contracts=1, active_gamma_sign=-1)
    assert stale.target_contracts == 0 and stale.reason == "insufficient_marks"
    conflict = replace(original[0], price=200, vintage_id="ambiguous")
    assert policy.decide(3, (*original, conflict), current_contracts=0).target_contracts is None
    other_source = replace(original[0], source_id="other")
    assert policy.decide(3, (other_source, *original[1:]), current_contracts=0).desired_contracts == 0


@pytest.mark.parametrize("sign", [True, False, 1.0, -1.0, 0, "1"])
def test_entry_regime_state_is_a_strict_sign_integer(sign):
    policy = DealerGammaPolicy([dealer(1)], DealerGammaConfig(rolling_window=2))
    with pytest.raises(ValueError):
        policy.decide(10, marks([1, 2, 3]), current_contracts=1, active_gamma_sign=sign)
    with pytest.raises(ValueError):
        policy.decide(10, marks([1, 2, 3]), current_contracts=1)


def test_zero_variance_blocks_mean_reversion_but_not_a_true_breakout():
    config = DealerGammaConfig(rolling_window=2)
    positive = DealerGammaPolicy([dealer(1)], config)
    negative = DealerGammaPolicy([dealer(-1)], config)
    assert positive.decide(10, marks([100, 100, 110]), current_contracts=0).reason == "zero_variance"
    decision = negative.decide(10, marks([100, 100, 110]), current_contracts=0)
    assert decision.target_contracts == 1 and decision.z_score is None
    assert negative.decide(10, marks([100, 100, 100]), current_contracts=0).target_contracts is None
    assert negative.decide(10, marks([100, 100, 90]), current_contracts=0).target_contracts == -1


def test_mean_reversion_holds_inside_entry_band_until_mean_cross():
    policy = DealerGammaPolicy([dealer(1)], DealerGammaConfig(rolling_window=2, entry_z=2))
    held = policy.decide(10, marks([99, 101, 100.5]), current_contracts=-1, active_gamma_sign=1)
    assert held.target_contracts is None and held.desired_contracts == -1
    closed = policy.decide(10, marks([99, 101, 100]), current_contracts=-1, active_gamma_sign=1)
    assert closed.target_contracts == 0


def test_extreme_prices_never_emit_nonfinite_diagnostics():
    config = DealerGammaConfig(rolling_window=2)
    policy = DealerGammaPolicy([dealer(1)], config)
    large = policy.decide(10, marks([1e308, 1.2e308, 1.7e308]), current_contracts=0)
    assert math.isfinite(large.z_score)
    tiny = policy.decide(10, marks([1e-310, 2e-310, 1e308]), current_contracts=0)
    assert tiny.reason == "mark_aggregate_overflow" and tiny.z_score is None


def test_model_estimates_require_opt_in_and_pending_suppresses_retry_until_feedback():
    estimate = dealer(-1, classification="model_estimate")
    config = DealerGammaConfig(rolling_window=2, allow_model_estimates=True)
    policy = DealerGammaPolicy([estimate], config)
    signal = marks([100, 101, 120])
    assert policy.decide(10, signal, current_contracts=0).target_contracts == 1
    assert policy.decide(10, signal, current_contracts=0, pending_target_contracts=1).target_contracts is None
    # Rejected/canceled order: authoritative caller clears pending, not position.
    assert policy.decide(11, signal, current_contracts=0).target_contracts == 1
