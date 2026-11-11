import math

import pytest

from qte.gamma_surface import GammaInputError, calculate_gamma_surface


def _request(**changes):
    value = {"schema_version": 1, "forward": 5000.0, "strike": 5000.0,
             "volatility_pct": 20.0, "days": 30.0, "rate_pct": 5.0,
             "multiplier": 50, "quantity": 3, "option_type": "call", "span_pct": 10.0}
    value.update(changes)
    return value


def test_atm_gamma_matches_finite_difference_and_grid_is_bounded():
    result = calculate_gamma_surface(_request())
    point = result["point"]
    assert point["gamma"] > 0.0
    assert point["finite_difference_gamma"] == pytest.approx(point["gamma"], rel=2e-6)
    assert point["position_gamma"] == pytest.approx(point["gamma"] * 150)
    assert len(result["surface"]["forwards"]) == 41
    assert len(result["surface"]["days"]) == 25
    assert len(result["surface"]["gamma"]) == 25
    assert all(len(row) == 41 for row in result["surface"]["gamma"])
    assert result["surface"]["forwards"][0] == pytest.approx(4500.0)
    assert result["surface"]["forwards"][20] == pytest.approx(5000.0)
    assert result["surface"]["forwards"][-1] == pytest.approx(5500.0)
    assert result["surface"]["days"][0] == pytest.approx(1 / 24)
    assert result["surface"]["days"][-1] == pytest.approx(30.0)
    assert len(result["slice"]) == 41
    assert all(math.isfinite(value) for row in result["surface"]["gamma"] for value in row)


def test_call_put_parity_and_same_gamma():
    call = calculate_gamma_surface(_request(option_type="call"))
    put = calculate_gamma_surface(_request(option_type="put"))
    assert call["point"]["gamma"] == put["point"]["gamma"]
    discount = call["point"]["discount"]
    assert call["point"]["price"] - put["point"]["price"] == pytest.approx(
        discount * (_request()["forward"] - _request()["strike"]))
    assert call["point"]["delta"] - put["point"]["delta"] == pytest.approx(discount)


def test_position_gamma_is_signed_and_tail_outputs_remain_finite():
    long = calculate_gamma_surface(_request(quantity=2))
    short = calculate_gamma_surface(_request(quantity=-2))
    assert short["point"]["position_gamma"] == -long["point"]["position_gamma"]
    tail = calculate_gamma_surface(_request(
        forward=100.0, strike=1_000_000.0, volatility_pct=1.0, days=1 / 24,
        rate_pct=50.0, multiplier=1000, quantity=-1000, span_pct=30.0))
    values = tail["point"]
    assert all(math.isfinite(value) for value in values.values() if isinstance(value, (int, float)))
    assert values["gamma"] == 0.0
    assert values["finite_difference_status"] == "tail_cancellation"


@pytest.mark.parametrize("changes", [
    {"forward": True}, {"forward": float("nan")}, {"forward": 99.0},
    {"strike": 1_000_001.0}, {"volatility_pct": 0.99}, {"days": 0.04},
    {"rate_pct": 51.0}, {"multiplier": 1.5}, {"quantity": True},
    {"quantity": 1001}, {"option_type": "CALL"}, {"span_pct": 31.0},
    {"schema_version": True}, {"extra": 1},
])
def test_rejects_nonfinite_wrong_type_out_of_bounds_and_unknown_fields(changes):
    with pytest.raises(GammaInputError):
        calculate_gamma_surface(_request(**changes))


def test_rejects_huge_integer_without_leaking_conversion_overflow():
    with pytest.raises(GammaInputError):
        calculate_gamma_surface(_request(forward=10 ** 1000))
