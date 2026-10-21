"""G5b.2a strict fixed-form compiler and pure preview tests."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import copy
import json
from pathlib import Path

import pytest

import qte
import qte.research_config as shared
import qte.strategy_lab as lab
from qte.gui.research_forms import (
    FormError,
    INLINE_CONFIG_ORIGIN,
    compile_configuration,
    config_origin,
    form_options,
    form_identity,
    prepare_form,
)
from qte.strategies import CandidateConfig
from qte.strategies.candidates import FAMILIES
from qte.synthetic import REGIMES


ROOT = Path(__file__).resolve().parents[2]
DATA = (ROOT / "examples/research-bars.csv").read_bytes()
START_NS = 1_704_204_000_000_000_000
END_NS = 1_704_229_200_000_000_000
HOUR_NS = 3_600_000_000_000


def research_wire(strategy_type: str = "moving_average_regime") -> dict:
    strategy = ({
        "type": "moving_average_regime", "symbol": "SPY", "fast_period": "2",
        "slow_period": "3", "quantity": "10",
    } if strategy_type == "moving_average_regime" else {
        "type": strategy_type, "symbol": "SPY",
    })
    return {
        "data": {"adapter": "csv", "path": "examples/research-bars.csv",
                 "interval_ns": str(HOUR_NS), "action_free": True},
        "strategy": strategy,
        "engine": {"initial_cash": 100_000, "history_capacity": "256", "random_seed": "0"},
        "execution": {"commission_bps": 1, "spread_bps": 2, "slippage_bps": 2},
        "sampling": {"interval_ns": str(HOUR_NS), "max_staleness_ns": "0",
                     "periods_per_year": 8766},
    }


def lab_wire() -> dict:
    return {"schema_version": 1, "seed": "817", "bars_per_window": "128",
            "timeframes": ["hourly"], "regimes": ["low_vol_trend", "gap_shock"]}


def test_origin_and_options_are_fixed_registry_derived_metadata():
    assert dict(INLINE_CONFIG_ORIGIN) == {
        "kind": "inline_form", "path_base": "repository_root", "form_schema_version": 1,
    }
    first, second = config_origin(), config_origin()
    first["kind"] = "changed"
    assert second == dict(INLINE_CONFIG_ORIGIN)

    options = form_options()
    from qte.gui.research_forms import _FAMILY_FIELDS
    assert tuple(_FAMILY_FIELDS) == FAMILIES
    strategies = options["research_run"]["strategies"]
    assert tuple(value["type"] for value in strategies) == ("moving_average_regime", *FAMILIES, "macro_router")
    assert strategies[-1]["fields"]["profile"]["type"] == "json_text"
    assert strategies[-1]["fields"]["profile"]["required"] is True
    defaults = asdict(CandidateConfig())
    defaults.pop("kind")
    for value, family in zip(strategies[1:-1], FAMILIES, strict=True):
        assert value["type"] == family
        assert set(value["exposed_fields"]) == set(value["fields"])
        assert set(value["fields"]) | set(value["hidden_defaults"]) == set(defaults)
        assert value["hidden_defaults"] == {
            name: item for name, item in defaults.items() if name not in value["exposed_fields"]
        }
    assert options["strategy_lab"]["families"] == list(FAMILIES)
    assert set(options["strategy_lab"]["strategy_parameter_fields"]) == set(defaults)
    assert options["strategy_lab"]["regimes"] == list(REGIMES)
    assert options["research_run"]["fields"]["engine.random_seed"]["type"] == "integer_string"
    assert options["strategy_lab"]["fields"]["engine.initial_cash"]["type"] == "number"


@pytest.mark.parametrize("kind", ["research_run", "strategy_lab"])
def test_compile_only_identity_matches_validated_preview_without_parsing_data(kind, monkeypatch):
    import qte.gui.research_forms as forms
    wire = research_wire() if kind == "research_run" else lab_wire()
    data = DATA if kind == "research_run" else None
    preview = prepare_form(kind, wire, data)
    monkeypatch.setattr(forms, "prepare_research", lambda *_: pytest.fail("identity parsed data"))
    monkeypatch.setattr(forms, "prepare_lab", lambda *_: pytest.fail("identity validated financial config"))
    identity = form_identity(kind, wire, sha256(DATA).hexdigest() if data is not None else None)
    assert identity == {key: preview[key] for key in ("canonical_config_sha256", "preview_fingerprint")}


@pytest.mark.parametrize("strategy_type", ("moving_average_regime", *FAMILIES))
def test_compile_and_prepare_every_registered_strategy(strategy_type, monkeypatch):
    class ForbiddenEngine:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("preview constructed an engine")

    monkeypatch.setattr(qte, "BacktestEngine", ForbiddenEngine)
    monkeypatch.setattr(qte, "analyze", lambda *_: pytest.fail("preview analyzed results"))
    monkeypatch.setattr(shared, "source_identity", lambda: "forms-test")
    preview = prepare_form("research_run", research_wire(strategy_type), DATA)
    effective = preview["effective_configuration"]
    assert effective["strategy"]["type"] == strategy_type
    assert type(effective["data"]["interval_ns"]) is int
    assert type(effective["engine"]["initial_cash"]) is float
    assert preview["kind"] == "research_run"
    assert preview["config_origin"] == dict(INLINE_CONFIG_ORIGIN)
    assert len(preview["canonical_config_sha256"]) == 64
    assert len(preview["preview_fingerprint"]) == 64


def test_compile_exact_integer_boundaries_and_nullable_single_run_risk():
    wire = research_wire()
    wire["engine"]["random_seed"] = str(2**64 - 1)
    wire["risk"] = {"max_order_quantity": str(2**63 - 1), "max_symbol_allocation": None,
                    "max_gross_leverage": None, "cash_floor": None, "allow_short": False}
    wire["sampling"] = {"timestamps_ns": [str(-(2**63)), str(2**63 - 1)],
                        "max_staleness_ns": str(2**63 - 1), "periods_per_year": 1}
    value = compile_configuration("research_run", wire)
    assert value["engine"]["random_seed"] == 2**64 - 1
    assert value["risk"]["max_order_quantity"] == 2**63 - 1
    assert value["sampling"]["timestamps_ns"] == [-(2**63), 2**63 - 1]
    assert value["risk"]["cash_floor"] is None

    lab_value = compile_configuration("strategy_lab", {
        **lab_wire(), "seed": str(2**62 - 1),
        "engine": {"max_order_quantity": str(2**63 - 1)},
    })
    assert lab_value["seed"] == 2**62 - 1
    assert lab_value["engine"]["max_order_quantity"] == 2**63 - 1


@pytest.mark.parametrize(("path", "bad"), [
    (("engine", "random_seed"), 7),
    (("engine", "random_seed"), True),
    (("engine", "random_seed"), "01"),
    (("engine", "random_seed"), "+1"),
    (("engine", "random_seed"), "-0"),
    (("engine", "random_seed"), str(2**64)),
    (("strategy", "quantity"), str(2**63)),
    (("data", "interval_ns"), "0"),
])
def test_noncanonical_numeric_and_overflow_values_are_rejected(path, bad):
    wire = research_wire()
    wire[path[0]][path[1]] = bad
    with pytest.raises(FormError) as caught:
        compile_configuration("research_run", wire)
    assert caught.value.code == "invalid_configuration"
    assert caught.value.status == 400
    assert caught.value.field == f"configuration.{path[0]}.{path[1]}"


@pytest.mark.parametrize("bad", [True, "1", None, float("inf"), 10**400])
def test_economic_numbers_reject_bool_strings_null_nonfinite_and_overflow(bad):
    wire = research_wire()
    wire["engine"]["initial_cash"] = bad
    with pytest.raises(FormError, match="finite JSON number"):
        compile_configuration("research_run", wire)


def test_unknown_fields_schema_and_forbidden_control_surfaces_are_rejected():
    for change in (
        {"factory": "module:callable"}, {"output": "build/result"}, {"environment": {}},
        {"candidates": []}, {"command": ["python"]},
    ):
        with pytest.raises(FormError, match="unsupported"):
            compile_configuration("research_run", {**research_wire(), **change})
    wire = research_wire()
    wire["data"]["schema"] = {"close": "close", "unknown": "payload"}
    with pytest.raises(FormError) as caught:
        compile_configuration("research_run", wire)
    assert caught.value.field == "configuration.data.schema"


def test_family_specific_fields_and_lab_fixed_shape_are_strict():
    wire = research_wire("trend")
    wire["strategy"]["entry_z"] = 2.0
    with pytest.raises(FormError) as caught:
        compile_configuration("research_run", wire)
    assert caught.value.field == "configuration.strategy"

    bad_lab = {**lab_wire(), "candidates": []}
    with pytest.raises(FormError, match="unsupported"):
        compile_configuration("strategy_lab", bad_lab)
    null_lab = {**lab_wire(), "engine": {"max_order_quantity": None}}
    with pytest.raises(FormError):
        compile_configuration("strategy_lab", null_lab)
    for bad in ({**lab_wire(), "seed": str(2**62)},
                {**lab_wire(), "schema_version": True}):
        with pytest.raises(FormError):
            compile_configuration("strategy_lab", bad)


def test_prepare_form_uses_only_captured_bytes_and_lab_is_pure(monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda *_a, **_k: pytest.fail("preview read a path"))
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("preview read a path"))
    monkeypatch.setattr(shared, "source_identity", lambda: "forms-test")
    monkeypatch.setattr(qte, "BacktestEngine", lambda *_a, **_k: pytest.fail("preview made engine"))
    monkeypatch.setattr(lab, "generate_case", lambda *_a, **_k: pytest.fail("preview generated data"))
    monkeypatch.setattr(lab, "run_experiment", lambda *_a, **_k: pytest.fail("preview ran lab"))

    research = prepare_form("research_run", research_wire(), DATA)
    synthetic = prepare_form("strategy_lab", lab_wire(), None)
    assert research["effective_configuration"]["data"]["path"] == "examples/research-bars.csv"
    assert synthetic["kind"] == "strategy_lab"
    assert "15 entries" in synthetic["assumptions"][1]
    with pytest.raises(FormError):
        prepare_form("strategy_lab", lab_wire(), b"not allowed")
    with pytest.raises(FormError):
        prepare_form("strategy_lab", {**lab_wire(), "execution": {}}, None)


def test_preview_hashes_typed_config_and_exact_captured_bytes(monkeypatch):
    monkeypatch.setattr(shared, "source_identity", lambda: "forms-test")
    wire = research_wire()
    first = prepare_form("research_run", wire, DATA)
    second = prepare_form("research_run", copy.deepcopy(wire), DATA)
    assert first == second
    wire["engine"]["initial_cash"] = 90_000
    changed_config = prepare_form("research_run", wire, DATA)
    assert changed_config["canonical_config_sha256"] != first["canonical_config_sha256"]
    assert changed_config["preview_fingerprint"] != first["preview_fingerprint"]

    changed_data = DATA.replace(b",1000\n", b",1001\n", 1)
    changed = prepare_form("research_run", research_wire(), changed_data)
    assert changed["canonical_config_sha256"] == first["canonical_config_sha256"]
    assert changed["preview_fingerprint"] != first["preview_fingerprint"]

    canonical = json.dumps(first["effective_configuration"], sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False).encode()
    assert first["canonical_config_sha256"] == sha256(canonical).hexdigest()


def test_invalid_adapter_content_is_sanitized_including_csv_field_limit(monkeypatch):
    monkeypatch.setattr(shared, "source_identity", lambda: "forms-test")
    for raw in (b"private vendor parse detail", b"x" * 140_000 + b"\n"):
        with pytest.raises(FormError) as caught:
            prepare_form("research_run", research_wire(), raw)
        assert caught.value.field == "configuration"
        assert "private" not in caught.value.message
        assert len(caught.value.message) <= 240


def test_sampling_preview_does_not_claim_result_dependent_runtime_success(monkeypatch):
    monkeypatch.setattr(shared, "source_identity", lambda: "forms-test")
    monkeypatch.setattr(qte, "analyze", lambda *_a, **_k: pytest.fail("preview ran analytics"))
    wire = research_wire()
    wire["sampling"] = {
        "timestamps_ns": [str(START_NS), str(START_NS + HOUR_NS // 2), str(END_NS)],
        "max_staleness_ns": "0", "periods_per_year": 8766,
    }
    preview = prepare_form("research_run", wire, DATA)
    sampling = next(text for text in preview["assumptions"] if text.startswith("Sampling"))
    assert "runtime checks" in sampling
    assert "periods_per_year=8766.0" in sampling
    assert "max_staleness_ns=0" in sampling


def test_config_only_sampling_failure_is_bounded_and_generic(monkeypatch):
    monkeypatch.setattr(shared, "source_identity", lambda: "forms-test")
    wire = research_wire()
    wire["sampling"]["interval_ns"] = str(HOUR_NS + 1)
    with pytest.raises(FormError) as caught:
        prepare_form("research_run", wire, DATA)
    assert caught.value.field == "configuration"
    assert caught.value.message == "Configuration or captured data is incompatible with this fixed form."


def test_form_default_risk_values_match_native_normalized_manifest():
    wire = research_wire()
    wire.pop("execution")
    config = compile_configuration("research_run", wire)
    prepared = shared.prepare_research(config, DATA, build_identity="forms-risk-defaults")
    result = qte.BacktestEngine(prepared.engine_config).run(prepared.loaded.dataset, prepared.strategy)
    normalized = result.manifest.normalized_config
    defaults = form_options()["research_run"]["defaults"]
    assert defaults["risk"] == {"max_order_quantity": 2**63 - 1, "max_symbol_allocation": 1.0,
                                "max_gross_leverage": 1.0, "allow_short": False, "cash_floor": 0.0}
    for expected in ("max_order_quantity=9223372036854775807", "max_symbol_allocation=1",
                     "max_gross_leverage=1", "allow_short=false", "cash_floor=0",
                     "history_capacity=256", "random_seed=0"):
        assert expected in normalized
