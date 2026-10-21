"""Declarative macro-router configuration regressions."""
from dataclasses import asdict

import pytest

import qte
from qte.regimes import FEATURES
from qte.router_config import RouterProfile, build_macro_router, prepare_router_profile
from qte.strategies import MacroRouterStrategy


def profile():
    return {
        "evidence": "synthetic",
        "candidates": [{
            "kind": "trend", "fast_period": 2, "slow_period": 3, "atr_period": 1,
            "breakout_period": 2, "exit_period": 2, "contraction_window": 2,
            "contraction_memory": 1, "max_holding_bars": 100,
            "stop_atr_multiple": 100, "entry_z": 1,
            "min_atr_fraction": .01, "max_atr_fraction": .10,
        }],
        "macro_observations": [
            {"feature": feature, "value": value, "reference_ns": 0, "available_ns": 0,
             "source_id": f"synthetic:{feature}", "vintage_id": "v0"}
            for feature, value in zip(FEATURES, (3, 0, 0, 12))
        ],
    }


def test_prepare_is_typed_owned_and_asdict_serializable():
    source = profile()
    prepared = prepare_router_profile(source)
    source["candidates"][0]["kind"] = "breakout"
    source["macro_observations"].clear()
    assert isinstance(prepared, RouterProfile)
    assert prepared.candidates[0].kind == "trend" and len(prepared.macro_observations) == 4
    assert asdict(prepared)["dealer_observations"] == ()
    assert isinstance(build_macro_router("SPY", prepared), MacroRouterStrategy)


@pytest.mark.parametrize("change", [
    {"unknown": 1},
    {"evidence": "inferred"},
    {"candidates": []},
    {"candidates": [{"kind": "trend"}, {"kind": "trend"}]},
])
def test_profile_strict_fields_evidence_and_candidate_bounds(change):
    value = profile()
    value.update(change)
    with pytest.raises(ValueError):
        prepare_router_profile(value)


def test_observation_limits_identities_and_constructor_validation():
    value = profile()
    value["macro_observations"][0]["available_ns"] = True
    with pytest.raises(ValueError, match="signed 64-bit"):
        prepare_router_profile(value)
    value = profile()
    value["macro_observations"][0]["source_id"] = "bad\nidentity"
    with pytest.raises(ValueError, match="control-free"):
        prepare_router_profile(value)
    value = profile()
    value["macro_observations"] *= 33
    with pytest.raises(ValueError, match="128"):
        prepare_router_profile(value)


def test_filter_checks_ambiguous_vintages_and_required_proxy_is_unqualified():
    value = profile()
    duplicate = dict(value["macro_observations"][0], available_ns=1)
    value["macro_observations"].append(duplicate)
    with pytest.raises(ValueError, match="ambiguous"):
        prepare_router_profile(value)

    value = profile()
    value["regime"] = {"dealer_mode": "required"}
    value["dealer_observations"] = [{
        "signed_gamma": 1, "reference_ns": 0, "available_ns": 0,
        "source_id": "dealer", "vintage_id": "v0", "methodology_id": "method",
        "underlying": "SPX", "classification": "proxy",
    }]
    prepared = prepare_router_profile(value)
    decision = prepared.make_filter().explain(0)
    assert decision.permitted_families == ()
    assert "dealer:proxy_not_positioning" in decision.reasons


def _run(router):
    second = 1_000_000_000
    prices = (100, 101, 102, 103, 100, 97, 99, 101, 102, 101)
    data = qte.Dataset.from_bars([
        qte.Bar("SPY", index * second, (index + 1) * second,
                price, price + .1, price - .1, price, 1000)
        for index, price in enumerate(prices)
    ], interval_ns=second, source_id="router-config-test")
    return qte.BacktestEngine(qte.BacktestConfig(
        100_000, history_capacity=router.history_required,
    )).run(data, router)


def test_built_router_matches_direct_router_exactly_and_exits_before_switch():
    value = profile()
    value["candidates"].append({
        **value["candidates"][0], "kind": "mean_reversion", "entry_z": 1,
    })
    value["macro_observations"] += [
        {**item, "value": next(v for f, v in zip(FEATURES, (0, 0, 0, 12)) if f == item["feature"]),
         "reference_ns": 4_000_000_000, "available_ns": 4_000_000_000, "vintage_id": "v4"}
        for item in value["macro_observations"]
    ]
    prepared = prepare_router_profile(value)
    built = build_macro_router("SPY", prepared)
    direct = MacroRouterStrategy(
        "SPY", prepared.candidates, prepared.make_filter(), prepared.priorities,
    )
    first, second = _run(built), _run(direct)
    fills = lambda result: [(fill.side, fill.quantity, fill.effective_ns, fill.executed_price)
                            for fill in result.fills]
    assert fills(first) == fills(second) == [
        (qte.OrderSide.BUY, 98, 3_000_000_000, 103),
        (qte.OrderSide.SELL, 98, 4_000_000_000, 100),
        (qte.OrderSide.BUY, 99, 5_000_000_000, 97),
        (qte.OrderSide.SELL, 99, 8_000_000_000, 102),
    ]
    assert [point.equity for point in first.equity_curve] == [point.equity for point in second.equity_curve]
