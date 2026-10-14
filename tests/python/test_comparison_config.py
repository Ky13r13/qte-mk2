"""G7a shared declarative comparison preparation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

import qte
from qte.comparison_config import (
    capture_comparison_inputs,
    compile_comparison_configuration,
    prepare_comparison,
)
from qte.experiments import ExperimentConfig, run_experiment
from qte.strategies import MovingAverageConfig, MovingAverageRegimeStrategy
from qte.strategies.candidates import CandidateStrategy, FAMILIES


HOUR = 3_600_000_000_000


def csv_window(
    day: int, *, symbol: str | tuple[str, ...] = "SPY", currency: str = "USD",
) -> tuple[bytes, dict]:
    start = datetime(2024, 1, day, tzinfo=timezone.utc)
    lines = ["symbol,timestamp,open,high,low,close,volume"]
    for index, price in enumerate((100, 101, 102, 103)):
        timestamp = start + timedelta(hours=index)
        for item in (symbol,) if isinstance(symbol, str) else symbol:
            lines.append(
                f"{item},{timestamp:%Y-%m-%dT%H:%M:%SZ},"
                f"{price},{price + 1},{price - 1},{price},1000"
            )
    raw = ("\n".join(lines) + "\n").encode()
    return raw, {
        "adapter": "csv", "path": f"window-{day}.csv", "interval_ns": HOUR,
        "currency": currency, "action_free": True,
    }


def evidence(raw: bytes, kind: str = "historical") -> dict:
    return {
        "kind": kind,
        "source": "caller-declared-fixture",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "availability_policy": "bar_end_ns",
        "corporate_action_policy": "raw_action_free",
    }


def comparison(workflow: str = "historical_experiment") -> tuple[dict, tuple[bytes, ...]]:
    roles = ("train", "validation", "test") if workflow == "historical_experiment" else ("train", "test")
    raws_and_data = [csv_window(day) for day in range(1, len(roles) + 1)]
    windows = [{
        "name": f"window-{role}", "role": role, "regime": f"regime-{index}",
        "data": data, "evidence": evidence(raw),
    } for index, (role, (raw, data)) in enumerate(zip(roles, raws_and_data, strict=True))]
    config = {
        "schema_version": 1,
        "workflow": workflow,
        "windows": windows,
        "candidates": [{
            "name": "sma-one", "type": "moving_average_regime", "symbol": "SPY",
            "parameters": {"fast_period": 2, "slow_period": 3, "quantity": 10},
        }],
        "experiment": {},
    }
    return config, tuple(raw for raw, _ in raws_and_data)


def test_compile_expands_defaults_and_is_json_friendly_without_io_or_engine(monkeypatch):
    config, _ = comparison()

    class ForbiddenEngine:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("configuration compilation must not construct an engine")

    monkeypatch.setattr(qte, "BacktestEngine", ForbiddenEngine)
    normalized = compile_comparison_configuration(config)
    json.dumps(normalized, allow_nan=False)
    assert normalized["candidates"][0]["eligible"] is True
    assert normalized["windows"][0]["data"]["tick_size"] == 0.01
    assert normalized["windows"][0]["data"]["schema"]["timestamp"] == "timestamp"
    assert normalized["experiment"]["initial_cash"] == 100_000
    assert normalized["experiment"]["base_costs"] == {
        "commission_bps": 1, "spread_bps": 2, "slippage_bps": 1,
    }
    assert config["candidates"][0].get("eligible") is None


@pytest.mark.parametrize("family", FAMILIES)
def test_all_allowlisted_candidate_families_make_fresh_factories(family):
    config, raws = comparison()
    config["candidates"] = [{
        "name": f"candidate-{family}", "type": family, "symbol": "SPY", "parameters": {},
    }]
    prepared = prepare_comparison(config, raws)
    candidate = prepared.candidates[0]
    first, second = candidate.factory(), candidate.factory()
    assert isinstance(first, CandidateStrategy)
    assert first is not second
    assert candidate.history_capacity == max(256, first.config.history_required)
    assert candidate.parameters["type"] == family
    assert candidate.parameters["symbol"] == "SPY"


def test_prepare_historical_types_hash_binding_and_determinism(monkeypatch):
    config, raws = comparison()
    config["windows"][1]["evidence"]["kind"] = "synthetic"
    prepared = prepare_comparison(config, raws)
    repeated = prepare_comparison(config, raws)
    assert isinstance(prepared.experiment_config, ExperimentConfig)
    assert tuple(window.evidence.kind for window in prepared.windows) == (
        "historical", "synthetic", "historical",
    )
    assert prepared.configuration == repeated.configuration
    assert prepared.source_identity == repeated.source_identity
    assert prepared.candidates[0].parameters_json == repeated.candidates[0].parameters_json
    assert isinstance(prepared.candidates[0].factory(), MovingAverageRegimeStrategy)

    changed = list(raws)
    changed[1] += b"\n"
    with pytest.raises(ValueError, match="source_sha256"):
        prepare_comparison(config, tuple(changed))


def test_legacy_properties_and_fixed_contract():
    config, raws = comparison("moving_average_comparison")
    prepared = prepare_comparison(config, raws)
    assert prepared.experiment_config is None
    assert prepared.symbol == "SPY"
    assert prepared.strategy_config == MovingAverageConfig(2, 3, 10)
    assert prepared.initial_cash == 100_000
    assert prepared.stressed_costs.commission_bps == 5
    config["candidates"][0]["eligible"] = False
    with pytest.raises(ValueError, match="must be eligible"):
        compile_comparison_configuration(config)


def test_candidate_identity_records_family_and_symbol():
    config, raws = comparison()
    first = prepare_comparison(config, raws).candidates[0]
    config["candidates"][0].update(type="trend", parameters={})
    second = prepare_comparison(config, raws).candidates[0]
    assert first.parameters_json != second.parameters_json
    config["candidates"][0]["symbol"] = "QQQ"
    with pytest.raises(ValueError, match="must exist"):
        prepare_comparison(config, raws)


def test_family_and_symbol_changes_change_experiment_protocol_identity():
    config, _ = comparison()
    pairs = [csv_window(day, symbol=("QQQ", "SPY")) for day in range(1, 4)]
    raws = tuple(raw for raw, _data in pairs)
    for window, (raw, data) in zip(config["windows"], pairs, strict=True):
        window["data"] = data
        window["evidence"] = evidence(raw)
    sma = prepare_comparison(config, raws)
    sma_id = run_experiment(sma.candidates, sma.windows, sma.experiment_config).protocol_id

    config["candidates"][0].update(type="trend", parameters={})
    trend = prepare_comparison(config, raws)
    trend_id = run_experiment(trend.candidates, trend.windows, trend.experiment_config).protocol_id
    config["candidates"][0]["symbol"] = "QQQ"
    qqq = prepare_comparison(config, raws)
    qqq_id = run_experiment(qqq.candidates, qqq.windows, qqq.experiment_config).protocol_id
    assert len({sma_id, trend_id, qqq_id}) == 3


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda c: c.update(extra=True), "unknown"),
        (lambda c: c.update(schema_version=True), "schema_version"),
        (lambda c: c["windows"][0].update(extra=True), "unknown"),
        (lambda c: c["windows"][0].update(name="unsafe name"), "safe ASCII"),
        (lambda c: c["windows"][0]["data"].update(interval_ns=True), "int64"),
        (lambda c: c["windows"][0]["data"].update(tick_size=float("nan")), "nonfinite"),
        (lambda c: c["candidates"][0]["parameters"].update(fast_period=True), "integers"),
        (lambda c: c["candidates"][0]["parameters"].update(quantity=2**63), "int64"),
        (lambda c: c["candidates"][0].update(factory="module:object"), "unknown"),
        (lambda c: c["candidates"][0].update(name="x\ud800"), "invalid Unicode"),
    ],
)
def test_strict_fields_types_and_scalar_values(mutation, message):
    config, _ = comparison()
    mutation(config)
    with pytest.raises(ValueError, match=message):
        compile_comparison_configuration(config)


def test_bounds_names_roles_and_lookbacks():
    config, _ = comparison()
    config["candidates"] *= 33
    with pytest.raises(ValueError, match="1-32"):
        compile_comparison_configuration(config)
    config, _ = comparison()
    config["candidates"].append({**config["candidates"][0]})
    with pytest.raises(ValueError, match="candidate names"):
        compile_comparison_configuration(config)
    config, _ = comparison()
    config["candidates"][0]["parameters"]["slow_period"] = 100_001
    with pytest.raises(ValueError, match="lookback"):
        compile_comparison_configuration(config)
    config, _ = comparison()
    config["windows"][-1]["role"] = "validation"
    with pytest.raises(ValueError, match="one final test"):
        compile_comparison_configuration(config)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c["windows"][1]["data"].update(currency="EUR"), "currency"),
        (lambda c: c["windows"][1]["data"].update(interval_ns=2 * HOUR), "interval"),
    ],
)
def test_window_compatibility(change, message):
    config, raws = comparison()
    change(config)
    with pytest.raises(ValueError, match=message):
        prepare_comparison(config, raws)


def test_window_chronology_and_universe_are_enforced():
    config, raws = comparison()
    config["windows"][0]["evidence"] = evidence(raws[1])
    config["windows"][1]["evidence"] = evidence(raws[0])
    with pytest.raises(ValueError, match="chronological"):
        prepare_comparison(config, (raws[1], raws[0], raws[2]))
    config, raws = comparison()
    other, _ = csv_window(2, symbol="QQQ")
    config["windows"][1]["evidence"] = evidence(other)
    with pytest.raises(ValueError, match="symbol|universe"):
        prepare_comparison(config, (raws[0], other, raws[2]))


def test_capture_reads_strict_json_and_each_distinct_file_once(tmp_path, monkeypatch):
    config, raws = comparison()
    for window, raw in zip(config["windows"], raws, strict=True):
        (tmp_path / window["data"]["path"]).write_bytes(raw)
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(config))
    captured_config, captured = capture_comparison_inputs(path)
    assert captured_config == config
    assert captured == raws

    duplicate = json.dumps(config).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    path.write_text(duplicate)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        capture_comparison_inputs(path)


def test_capture_rejects_symlink_fifo_oversize_and_repeated_source(tmp_path):
    config, raws = comparison("moving_average_comparison")
    first = tmp_path / config["windows"][0]["data"]["path"]
    first.write_bytes(raws[0])
    second = tmp_path / config["windows"][1]["data"]["path"]
    second.symlink_to(first)
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="regular file|changed"):
        capture_comparison_inputs(path)

    second.unlink()
    os.mkfifo(second)
    with pytest.raises(ValueError, match="regular file"):
        capture_comparison_inputs(path)


def test_expected_sha_is_adapter_validation_not_historical_authentication():
    config, raws = comparison()
    config["windows"][0]["data"]["expected_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="expected_sha256"):
        prepare_comparison(config, raws)
    config["windows"][0]["data"]["expected_sha256"] = hashlib.sha256(raws[0]).hexdigest()
    config["windows"][0]["evidence"]["kind"] = "synthetic"
    assert prepare_comparison(config, raws).windows[0].evidence.kind == "synthetic"
