import math

import pytest

from qte.american_option import american_option_gamma, american_option_price


def test_non_dividend_call_converges_to_european_price():
    # Black-Scholes at S = K = 100, 20% IV, 5% rate, and one year.
    european_call = 10.450583572185565
    inputs = (100, 100, 0.20, 1, 0.05, 0.0, "call")
    coarse = american_option_price(*inputs, steps=50)
    fine = american_option_price(*inputs, steps=400)
    assert abs(fine - european_call) < abs(coarse - european_call)
    assert fine == pytest.approx(european_call, abs=0.006)


def test_at_the_money_put_has_early_exercise_premium():
    # Spot and strike match, so the premium requires exercise inside the tree.
    european_put = 5.573526022256971
    put = american_option_price(100, 100, 0.20, 1, 0.05, 0.0, "put", steps=400)
    assert put > european_put
    assert put == pytest.approx(6.0884, abs=0.004)


@pytest.mark.parametrize(("spot", "rate", "yield_", "kind", "intrinsic", "upper_bound", "exercise"), [
    (140, 0.01, 0.15, "call", 40, 140, True),
    (80, 0.08, 0.00, "put", 20, 100, True),
    (140, 0.05, 0.00, "call", 40, 140, False),
    (80, -0.03, 0.00, "put", 20, 100 * math.exp(0.03 * 0.5), False),
])
def test_prices_respect_intrinsic_and_discounted_upper_bound(
    spot, rate, yield_, kind, intrinsic, upper_bound, exercise,
):
    price = american_option_price(spot, 100, 0.20, 0.5, rate, yield_, kind)
    assert intrinsic <= price <= upper_bound
    assert (price == intrinsic) == exercise


def test_expiry_and_zero_volatility_have_defined_values():
    assert american_option_price(110, 100, 0.20, 0, 0.05, 0, "call") == 10
    assert american_option_price(110, 100, 0.20, 0, 0.05, 0, "put") == 0
    assert american_option_price(110, 100, 0, 1, 0.05, 0, "call") == pytest.approx(
        110 - 100 * math.exp(-0.05))
    assert american_option_price(90, 100, 0, 1, 0.05, 0, "put") == 10


@pytest.mark.parametrize(("rate", "yield_", "kind", "expected"), [
    (0.2, 0, "call", 100 - 100 * math.exp(-0.2)),
    (0, 0.2, "put", 100 - 100 * math.exp(-0.2)),
])
def test_deterministic_transition_at_crr_probability_boundaries(rate, yield_, kind, expected):
    assert american_option_price(100, 100, 0.2, 1, rate, yield_, kind, steps=1) == pytest.approx(expected)


def test_incompatible_drift_and_time_step_raise_instead_of_clipping_probability():
    with pytest.raises(ValueError, match="risk-neutral probability"):
        american_option_price(100, 100, 0.02, 1, 0.2, 0, "call", steps=1)
    assert american_option_price(100, 100, 0.02, 1, 0.2, 0, "call", steps=200) > 0


@pytest.mark.parametrize("invalid", [
    {"spot": False}, {"strike": 0}, {"iv": math.inf}, {"rate": math.nan},
    {"years": -1}, {"dividend_yield": -0.01}, {"option_type": "invalid"},
    {"steps": 10.0}, {"steps": 2049}, {"spot": 10**1000},
])
def test_price_rejects_invalid_inputs(invalid):
    inputs = dict(spot=100, strike=100, iv=0.2, years=1, rate=0.05,
                  dividend_yield=0, option_type="call")
    with pytest.raises(ValueError):
        american_option_price(**(inputs | invalid))


def test_gamma_reprices_a_symmetric_spot_bump():
    # At expiry and at the strike, each option gains 2 on only one side.
    for kind in ("call", "put"):
        assert american_option_gamma(100, 100, 0.2, 0, 0.05, 0, kind, spot_bump=2) == 0.5

    gamma = american_option_gamma(100, 100, 0.2, 1, 0.05, 0, "call", spot_bump=2, steps=400)
    assert gamma == pytest.approx(0.018762017345846895, rel=0.02)


@pytest.mark.parametrize("bump", [0, 100, 1e-15, math.inf])
def test_gamma_rejects_an_unusable_bump(bump):
    with pytest.raises(ValueError, match="spot_bump"):
        american_option_gamma(100, 100, 0.2, 1, 0.05, 0, "call", spot_bump=bump)


@pytest.mark.parametrize(("rate", "yield_", "reference"), [
    (0, 0.025, 8.871505915120776),
    (0.05, 0, 12.3359989303687243),
])
def test_one_year_lattice_within_one_cent_of_quantlib_fixture(rate, yield_, reference):
    # QuantLib test-suite/americanoption.cpp, QD+ edgeTestCases. Flat continuous
    # curves, Actual365Fixed, 365 days. One cent per share is the audit threshold.
    value = american_option_price(100, 100, 0.25, 1, rate, yield_, "call", steps=1024)
    assert abs(value - reference) < 0.01
