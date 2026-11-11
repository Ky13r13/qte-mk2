from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal, localcontext
import gc
import json
import math

import pytest
import qte

from qte.volatility import (
    DAY_NS, MINUTE_NS, YEAR_NS, DEFAULT_HORIZONS, MODEL_ID,
    CandleSnapshot, GammaSensitivity, MoveHorizon, ProxyAssumption,
    VolatilityFutureObservation, analyze_vix_proxy,
)


NOW = 100 * DAY_NS
CONTRACT = "VX-fixture@expiry"


def future(**changes):
    values = dict(contract_id=CONTRACT, expiry_cutoff_ns=NOW + 30 * DAY_NS,
                  value_pct=20.0, reference_ns=NOW - DAY_NS, available_ns=NOW,
                  source_id="synthetic-vx", vintage_id="v1", evidence="synthetic")
    return VolatilityFutureObservation(**(values | changes))


def candle(**changes):
    values = dict(label="1h", symbol="NQ-fixture", start_ns=NOW - 60 * MINUTE_NS,
                  end_ns=NOW, available_ns=NOW, open=20000., high=20200.,
                  low=19800., close=20000., source_id="synthetic-candle")
    return CandleSnapshot(**(values | changes))


def proxy(**changes):
    values = dict(target_symbol="NQ-fixture", volatility_scale=1.,
                  assumption_id="unfitted-scenario", reference_ns=NOW, available_ns=NOW)
    return ProxyAssumption(**(values | changes))


def gamma(**changes):
    values = dict(symbol="NQ-fixture", dollars_per_point_squared=.02,
                  reference_ns=NOW, available_ns=NOW, source_id="synthetic-gamma")
    return GammaSensitivity(**(values | changes))


def analyze(observations=None, **changes):
    kwargs = dict(timestamp_ns=NOW, contract_id=CONTRACT, anchor=candle(), proxy=proxy())
    return analyze_vix_proxy([future()] if observations is None else observations,
                             **(kwargs | changes))


def test_hand_computed_calendar_daily_sigma_and_endpoint_masses():
    result = analyze()
    assert result.model_id == MODEL_ID
    assert result.effective_volatility_pct == 20
    daily = result.moves[-1]
    sigma = 20000 * .20 / math.sqrt(365)
    assert daily.horizon == MoveHorizon("1d_calendar", DAY_NS)
    assert daily.sigma_fraction == pytest.approx(.20 / math.sqrt(365))
    assert daily.sigma_points == pytest.approx(sigma)
    assert daily.gaussian_expected_absolute_points == pytest.approx(sigma * math.sqrt(2 / math.pi))
    assert daily.local_gamma_expected_gross_change is None
    for band, mass in zip(daily.bands, (.6826894921370859, .9544997361036416, .9973002039367398)):
        assert band.lower == pytest.approx(20000 - band.sigma * sigma)
        assert band.upper == pytest.approx(20000 + band.sigma * sigma)
        assert band.gaussian_endpoint_mass == pytest.approx(mass)
        assert band.local_gamma_gross_change is None
    assert result.selected_future.source_id == "synthetic-vx"
    assert result.selected_future.vintage_id == "v1"
    assert result.anchor.source_id == "synthetic-candle"
    assert result.proxy.assumption_id == "unfitted-scenario"
    assert any("not calibrated" in text for text in result.warnings)
    assert any("synthetic" in text for text in result.warnings)
    assert any("status is unknown" in text for text in result.warnings)


def test_time_scaling_is_elapsed_calendar_time_not_trading_session_count():
    horizons = (MoveHorizon("weekend", 3 * DAY_NS), MoveHorizon("1h", 60 * MINUTE_NS),
                MoveHorizon("daily", DAY_NS), MoveHorizon("custom_session", 390 * MINUTE_NS))
    result = analyze(horizons=reversed(horizons))
    by_label = {move.horizon.label: move for move in result.moves}
    hour = by_label["1h"].sigma_points
    assert by_label["daily"].sigma_points == pytest.approx(hour * math.sqrt(24))
    assert by_label["weekend"].sigma_points == pytest.approx(hour * math.sqrt(72))
    assert by_label["custom_session"].sigma_points == pytest.approx(hour * math.sqrt(6.5))
    assert [move.horizon.duration_ns for move in result.moves] == sorted(h.duration_ns for h in horizons)
    assert [h.label for h in DEFAULT_HORIZONS] == ["1m", "5m", "15m", "30m", "1h", "4h", "1d_calendar"]
    assert analyze(proxy=proxy(volatility_scale=1.5)).moves[-1].sigma_points == pytest.approx(
        analyze().moves[-1].sigma_points * 1.5)


def test_all_completed_ohlc_levels_have_signed_distance_and_complementary_endpoint_tails():
    sigma = 20000 * .20 / math.sqrt(365)
    level = candle(open=20000, high=20000 + sigma, low=20000 - sigma, close=20000)
    previous = candle(label="1d", start_ns=NOW - 2 * DAY_NS, end_ns=NOW - DAY_NS,
                      available_ns=NOW - DAY_NS)
    result = analyze(candles=[level, previous])
    daily = result.moves[-1]
    assert len(daily.candle_levels) == 8
    assert [item.level for item in daily.candle_levels] == ["open", "high", "low", "close"] * 2
    levels = {item.level: item for item in daily.candle_levels if item.candle_label == "1h"}
    assert levels["open"].signed_sigma_distance == 0
    assert levels["open"].gaussian_probability_end_above == .5
    assert levels["high"].signed_sigma_distance == pytest.approx(1.)
    assert levels["high"].gaussian_probability_end_above == pytest.approx(.15865525393145707)
    assert levels["low"].signed_sigma_distance == pytest.approx(-1.)
    assert levels["low"].gaussian_probability_end_below == pytest.approx(.15865525393145707)
    for item in daily.candle_levels:
        assert item.gaussian_probability_end_above + item.gaussian_probability_end_below == pytest.approx(1)
        assert item.candle_age_ns == NOW - item.candle_end_ns
    assert result.level_candles == (previous, level)


@pytest.mark.parametrize("sensitivity", [.02, -.02, 0.])
def test_gamma_is_signed_currency_per_point_squared_and_not_net_pnl(sensitivity):
    result = analyze(gamma=gamma(dollars_per_point_squared=sensitivity))
    daily = result.moves[-1]
    for band in daily.bands:
        expected = .5 * sensitivity * (band.sigma * daily.sigma_points) ** 2
        assert band.local_gamma_gross_change == pytest.approx(expected)
    assert daily.local_gamma_expected_gross_change == daily.bands[0].local_gamma_gross_change
    assert result.gamma.source_id == "synthetic-gamma"
    assert any("not total PnL" in text for text in result.warnings)


def test_gamma_scaled_product_avoids_intermediate_underflow_and_overflow():
    # Min-subnormal gamma must not turn into zero at the intermediate 0.5*gamma.
    large_anchor = candle(open=1e152, high=1e152, low=1e152, close=1e152)
    result = analyze(anchor=large_anchor, gamma=gamma(dollars_per_point_squared=5e-324))
    band = result.moves[-1].bands[0]
    with localcontext() as context:
        context.prec = 80
        distance = Decimal.from_float(result.moves[-1].sigma_points)
        expected = float(Decimal.from_float(5e-324) * distance * distance / 2)
    assert band.local_gamma_gross_change > 0
    assert math.isclose(band.local_gamma_gross_change, expected, rel_tol=1e-14, abs_tol=0)
    # Distance squared overflows, but multiplying by small gamma restores range.
    larger = candle(open=1e162, high=1e162, low=1e162, close=1e162)
    large_result = analyze(anchor=larger, gamma=gamma(dollars_per_point_squared=1e-300))
    assert math.isfinite(large_result.moves[-1].bands[-1].local_gamma_gross_change)


def test_future_records_and_later_revisions_cannot_change_past_results():
    baseline = analyze(candles=[candle()])
    later_revision = future(value_pct=80., available_ns=NOW + 1, vintage_id="v2")
    future_contract = future(contract_id="future-contract", available_ns=NOW + 1)
    future_level = candle(symbol="OTHER", available_ns=NOW + 1)
    assert analyze([later_revision, future(), future_contract], candles=[future_level, candle()]) == baseline
    assert analyze([future_contract, future(), later_revision], candles=[candle(), future_level]) == baseline
    revision_now = replace(later_revision, available_ns=NOW + MINUTE_NS)
    revised = analyze([future(), revision_now], timestamp_ns=NOW + MINUTE_NS)
    assert revised.selected_future == revision_now
    assert revised.effective_volatility_pct == 80


def test_latest_reference_wins_over_late_release_of_older_reference():
    old_revised = future(reference_ns=NOW - 2 * DAY_NS, available_ns=NOW, vintage_id="old-v2", value_pct=30.)
    newer = future(available_ns=NOW - MINUTE_NS)
    assert analyze([old_revised, newer]).selected_future == newer


def test_expiry_cutoff_is_exclusive_and_never_auto_rolls():
    expiring = future(expiry_cutoff_ns=NOW)
    other = future(contract_id="later", expiry_cutoff_ns=NOW + DAY_NS)
    with pytest.raises(ValueError, match="expired; no auto-roll"):
        analyze([expiring, other])
    assert analyze([replace(expiring, expiry_cutoff_ns=NOW + 1), other]).selected_future.contract_id == CONTRACT
    assert analyze([expiring, other], contract_id="later").visible_curve == (other,)


@pytest.mark.parametrize("last_value,shape", [(25., "contango"), (20., "flat"), (18., "backwardation")])
def test_curve_is_diagnostic_with_no_implicit_interpolation(last_value, shape):
    back = future(contract_id="back", expiry_cutoff_ns=NOW + 60 * DAY_NS, value_pct=last_value)
    result = analyze([back, future()])
    assert result.front_spread_points == last_value - 20
    assert result.curve_shape == shape
    assert result.effective_volatility_pct == 20
    asynchronous = replace(back, reference_ns=NOW - 2 * DAY_NS)
    assert any("asynchronous" in text for text in analyze([future(), asynchronous]).warnings)
    same_expiry = replace(back, expiry_cutoff_ns=future().expiry_cutoff_ns)
    same = analyze([future(), same_expiry])
    assert same.front_spread_points is None
    assert same.curve_shape == "insufficient_distinct_expiries"


@pytest.mark.parametrize("other", [
    future(value_pct=21.),
    future(available_ns=NOW - 1),  # Same reference + vintage is ambiguous.
    future(expiry_cutoff_ns=NOW + 31 * DAY_NS, vintage_id="v2", available_ns=NOW - 1),
])
def test_ambiguous_revisions_and_conflicting_expiries_fail_closed(other):
    with pytest.raises(ValueError, match="ambiguous|inconsistent"):
        analyze([future(), other])


def test_staleness_and_availability_are_inclusive_at_boundary():
    assert analyze(max_vol_age_ns=DAY_NS).selected_future == future()
    with pytest.raises(ValueError, match="stale"):
        analyze(max_vol_age_ns=DAY_NS - 1)
    old_anchor = candle(start_ns=NOW - DAY_NS, end_ns=NOW - 5 * MINUTE_NS)
    assert analyze(anchor=old_anchor).anchor == old_anchor
    with pytest.raises(ValueError, match="anchor.*stale"):
        analyze(anchor=old_anchor, max_anchor_age_ns=5 * MINUTE_NS - 1)
    old_gamma = gamma(reference_ns=NOW - 5 * MINUTE_NS)
    assert analyze(gamma=old_gamma).gamma == old_gamma
    with pytest.raises(ValueError, match="gamma.*stale"):
        analyze(gamma=old_gamma, max_gamma_age_ns=5 * MINUTE_NS - 1)


@pytest.mark.parametrize("changes", [
    {"anchor": candle(available_ns=NOW + 1)},
    {"proxy": proxy(available_ns=NOW + 1)},
    {"gamma": gamma(available_ns=NOW + 1)},
    {"gamma": gamma(symbol="OTHER")},
    {"proxy": proxy(target_symbol="OTHER")},
    {"candles": [candle(symbol="OTHER")]},
    {"candles": [candle(), candle()]},
    {"contract_id": "nonexistent"},
    {"horizons": []},
    {"horizons": [MoveHorizon("one", 1), MoveHorizon("one", 2)]},
    {"horizons": [None]}, {"candles": [None]},
    {"anchor": None}, {"proxy": None}, {"gamma": 1},
    {"timestamp_ns": True}, {"timestamp_ns": 2**63},
    {"max_vol_age_ns": -1}, {"max_anchor_age_ns": -1}, {"max_gamma_age_ns": -1},
])
def test_invalid_analysis_inputs_fail_closed(changes):
    with pytest.raises(ValueError):
        analyze(**changes)


@pytest.mark.parametrize("observations", [[], [None], [future(available_ns=NOW + 1)]])
def test_unusable_volatility_input_is_not_inferred(observations):
    with pytest.raises(ValueError):
        analyze(observations)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), -float("inf"), True, "20", 10**1000])
def test_positive_inputs_are_finite_numeric_not_bool_or_coerced_strings(bad):
    with pytest.raises(ValueError):
        future(value_pct=bad)
    with pytest.raises(ValueError):
        proxy(volatility_scale=bad)
    for field in ("open", "high", "low", "close"):
        with pytest.raises(ValueError):
            candle(**{field: bad})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "1", 10**1000])
def test_gamma_rejects_nonfinite_and_coerced_values(bad):
    with pytest.raises(ValueError):
        gamma(dollars_per_point_squared=bad)


@pytest.mark.parametrize("make,changes", [
    (future, {"contract_id": ""}), (future, {"source_id": " "}), (future, {"vintage_id": "x" * 1025}),
    (future, {"evidence": "real"}), (future, {"discretionary": 0}),
    (future, {"expiry_cutoff_ns": 2**63}), (future, {"reference_ns": NOW + 1}),
    (proxy, {"reference_ns": NOW + 1}), (proxy, {"assumption_id": ""}),
    (gamma, {"reference_ns": NOW + 1}), (gamma, {"source_id": ""}),
    (candle, {"start_ns": NOW}), (candle, {"end_ns": NOW + 1}),
    (candle, {"high": 19999}), (candle, {"low": 20001}),
    (candle, {"open": 20201}), (candle, {"close": 19799}),
])
def test_domain_validation(make, changes):
    with pytest.raises(ValueError):
        make(**changes)


@pytest.mark.parametrize("duration", [0, -1, True, 1.5, 2**63])
def test_horizon_is_strict_positive_int64(duration):
    with pytest.raises(ValueError):
        MoveHorizon("h", duration)


def test_extreme_scenarios_fail_instead_of_inf_nan_negative_prices_or_underflow():
    with pytest.raises(ValueError, match="effective volatility"):
        analyze([future(value_pct=1e308)], proxy=proxy(volatility_scale=1e308))
    with pytest.raises(ValueError, match="sigma_points"):
        analyze([future(value_pct=5e-324)])
    with pytest.raises(ValueError, match="positive-price approximation"):
        analyze([future(value_pct=1000)])
    with pytest.raises(ValueError, match="local gamma gross change"):
        analyze(gamma=gamma(dollars_per_point_squared=1e308))
    with pytest.raises(ValueError, match="underflows"):
        analyze([future(value_pct=1e-150)], gamma=gamma(dollars_per_point_squared=5e-324))
    near_limit = candle(open=1.79e308, high=1.79e308, low=1.79e308, close=1.79e308)
    with pytest.raises(ValueError, match="upper band"):
        analyze(anchor=near_limit)
    with pytest.raises(ValueError, match="horizon endpoint"):
        analyze(horizons=[MoveHorizon("too_far", 2**63 - 1)])


def test_canonical_bar_snapshot_is_owned_and_validated_without_changing_bar_contract():
    bar = qte.Bar("NQ-fixture", NOW - MINUTE_NS, NOW, 20000, 20100, 19900, 20010, 0)
    observation = qte.BarObservation(bar, NOW + 1)
    snapshot = CandleSnapshot.from_observation("1m", observation, source_id="synthetic-canonical")
    del observation, bar
    gc.collect()
    assert snapshot.close == 20010
    assert snapshot.available_ns == NOW + 1
    assert snapshot.end_ns == NOW
    with pytest.raises(ValueError, match="canonical"):
        CandleSnapshot.from_observation("bad", None, source_id="x")


def test_reproducible_json_and_discretionary_status_is_tristate():
    result = analyze(candles=[candle()], gamma=gamma())
    assert result == analyze(candles=[candle()], gamma=gamma())
    encoded = json.dumps(asdict(result), sort_keys=True, allow_nan=False)
    assert json.loads(encoded)["selected_future"]["discretionary"] is None
    flagged = analyze([future(discretionary=True)])
    assert any("carries a discretionary" in text for text in flagged.warnings)
    unflagged = analyze([future(discretionary=False)])
    assert not any("discretionary" in text for text in unflagged.warnings)
