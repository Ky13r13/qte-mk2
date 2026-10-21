"""Strict macro-router JSON textarea and preview regressions."""
from __future__ import annotations

import copy
import json

import pytest

from qte.gui.research_forms import FormError, compile_configuration, form_options, prepare_form
from qte.gui.catalog import Catalog
from qte.gui.form_inputs import capture_form_inputs
from qte.regimes import FEATURES
from test_gui_catalog import _unknown
from test_gui_form_api import ORIGIN, PREVIEW, repository
from test_gui_job_api import visit
from test_gui_research_forms import DATA, START_NS, research_wire


def router_profile(*, source: str = "synthetic") -> dict:
    return {
        "evidence": "synthetic",
        "candidates": [{
            "kind": "trend", "fast_period": "2", "slow_period": "3",
            "atr_period": "1", "max_holding_bars": "20",
        }],
        "macro_observations": [{
            "feature": feature, "value": value,
            "reference_ns": str(START_NS), "available_ns": str(START_NS),
            "source_id": f"{source}:{feature}", "vintage_id": "v1",
        } for feature, value in zip(FEATURES, (3.0, 0.0, 0.0, 12.0), strict=True)],
        "regime": {"max_macro_age_ns": "9223372036854775807",
                   "max_volatility_age_ns": "9223372036854775807"},
    }


def router_wire(profile: dict | None = None) -> dict:
    wire = research_wire()
    wire["strategy"] = {
        "type": "macro_router", "symbol": "SPY",
        "profile": json.dumps(router_profile() if profile is None else profile),
    }
    return wire


def test_options_expose_one_required_json_text_without_changing_old_families():
    strategies = form_options()["research_run"]["strategies"]
    assert tuple(item["type"] for item in strategies[:-1]) == (
        "moving_average_regime", "trend", "breakout", "contraction",
        "pullback", "mean_reversion", "rebound",
    )
    router = strategies[-1]
    assert router["type"] == "macro_router"
    assert router["exposed_fields"] == ["profile"]
    descriptor = router["fields"]["profile"]
    assert descriptor["type"] == "json_text" and descriptor["required"] is True


def test_router_json_compiles_exact_large_integers_and_prepares_profile():
    value = compile_configuration("research_run", router_wire())
    profile = value["strategy"]["profile"]
    assert profile["macro_observations"][0]["reference_ns"] == START_NS
    assert type(profile["macro_observations"][0]["reference_ns"]) is int
    assert profile["regime"]["max_macro_age_ns"] == 2**63 - 1
    assert profile["candidates"][0]["fast_period"] == 2
    preview = prepare_form("research_run", router_wire(), DATA)
    assert preview["effective_configuration"]["strategy"]["type"] == "macro_router"


@pytest.mark.parametrize("bad", [1, True, 1.0])
def test_router_integer_positions_require_canonical_decimal_strings(bad):
    value = router_profile()
    value["macro_observations"][0]["reference_ns"] = bad
    with pytest.raises(FormError) as caught:
        compile_configuration("research_run", router_wire(value))
    assert caught.value.field.startswith("configuration.strategy.profile")


@pytest.mark.parametrize("raw", [
    '{"evidence":"synthetic","evidence":"user_declared","candidates":[],"macro_observations":[]}',
    '{"evidence":"synthetic","candidates":[],"macro_observations":[],"x":NaN}',
])
def test_router_text_uses_strict_json(raw):
    wire = router_wire()
    wire["strategy"]["profile"] = raw
    with pytest.raises(FormError):
        compile_configuration("research_run", wire)


def test_router_parser_enforces_provenance_vintage_and_keeps_proxy_semantics():
    value = router_profile(source="declared")
    value["macro_observations"].append({
        **value["macro_observations"][0], "available_ns": str(START_NS + 1),
    })
    with pytest.raises(FormError):
        compile_configuration("research_run", router_wire(value))

    value = router_profile()
    value["regime"]["dealer_mode"] = "required"
    value["dealer_observations"] = [{
        "signed_gamma": 1.0, "reference_ns": str(START_NS),
        "available_ns": str(START_NS), "source_id": "declared-dealer",
        "vintage_id": "v1", "methodology_id": "declared-method",
        "underlying": "SPX", "classification": "proxy",
    }]
    compiled = compile_configuration("research_run", router_wire(value))
    assert compiled["strategy"]["profile"]["dealer_observations"][0]["classification"] == "proxy"


def test_profile_edit_changes_both_preview_hashes_without_numeric_precision_loss():
    first = prepare_form("research_run", router_wire(), DATA)
    changed = router_profile()
    changed["macro_observations"][0]["value"] = 4.0
    second = prepare_form("research_run", router_wire(changed), DATA)
    assert second["canonical_config_sha256"] != first["canonical_config_sha256"]
    assert second["preview_fingerprint"] != first["preview_fingerprint"]
    assert first["effective_configuration"]["strategy"]["profile"]["macro_observations"][0]["reference_ns"] == START_NS


def test_router_profile_rejects_nonfinite_and_bool_economic_values():
    for bad in (True, float("inf")):
        value = copy.deepcopy(router_profile())
        value["macro_observations"][0]["value"] = bad
        wire = router_wire(value)
        if bad == float("inf"):
            # Produce the same non-standard token a permissive JSON encoder emits.
            assert "Infinity" in wire["strategy"]["profile"]
        with pytest.raises(FormError):
            compile_configuration("research_run", wire)


def test_exact_captured_router_config_is_authorized_before_profile_diagnostics(repository):
    configuration = router_wire()
    configuration["data"]["path"] = "data/bars.csv"
    configuration["strategy"]["profile"] = "PRIVATE_INVALID_ROUTER_PROFILE"
    captured = capture_form_inputs(repository, "research_run", ORIGIN, configuration)
    catalog = Catalog(repository)
    protected = catalog.register(_unknown(repository, "protected-router-config", captured.configuration_bytes))
    payload = {"schema_version": 1, "kind": "research_run",
               "config_origin": dict(ORIGIN), "configuration": configuration}

    async def check(client, headers):
        response = await client.post(PREVIEW, headers=headers, json=payload)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "holdout_protected"
        assert "PRIVATE_" not in response.text and "invalid_configuration" not in response.text
        catalog.reveal(protected["id"], "reveal outside protocol")
        response = await client.post(PREVIEW, headers=headers, json=payload)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_configuration"
        assert "PRIVATE_" not in response.text

    visit(repository, check)
