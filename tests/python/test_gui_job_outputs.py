"""Strict completion checks for newly produced fixed synthetic-lab jobs."""
from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

import qte
from qte.cli import run_research
import qte.experiments as experiments
from qte.experiments import ScenarioResult
from qte.gui.artifacts import inspect_artifact
from qte.gui.job_outputs import verify_fixed_lab_output
import qte.strategy_lab as lab


ROOT = Path(__file__).resolve().parents[2]


def _spec() -> dict:
    return {
        "schema_version": 1,
        "seed": 817,
        "bars_per_window": 128,
        "timeframes": ["hourly"],
        "regimes": ["low_vol_trend"],
    }


def _write_config(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _canonical_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _canonical_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _accepted(output: Path) -> tuple[str, str]:
    config = _json(output / "configuration.json")
    report = _json(output / "hourly/report.json")
    return report["source_identity"], _canonical_hash(config)


@pytest.fixture(scope="module")
def completed_lab(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixed-job-output")
    output = root / "artifacts"
    lab.run_lab(_write_config(root / "input/config.json", _spec()), output)
    return output


def _copy(completed_lab: Path, tmp_path: Path) -> Path:
    output = tmp_path / "artifacts"
    shutil.copytree(completed_lab, output)
    return output


def _rewrite_json(output: Path, relative: str, mutate) -> None:
    path = output / relative
    value = _json(path)
    mutate(value)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    completion = _json(output / "complete.json")
    completion["sha256"][relative] = sha256(path.read_bytes()).hexdigest()
    (output / "complete.json").write_text(
        json.dumps(completion, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
    )


def _rewrite_comparison(output: Path, mutate) -> None:
    path = output / "hourly/comparison.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    mutate(rows)
    with path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    completion = _json(output / "complete.json")
    completion["sha256"]["hourly/comparison.csv"] = sha256(path.read_bytes()).hexdigest()
    (output / "complete.json").write_text(
        json.dumps(completion, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
    )


def test_verified_fixed_lab_is_accepted_without_running_research(completed_lab, monkeypatch):
    source_identity, config_hash = _accepted(completed_lab)
    monkeypatch.setattr(qte, "BacktestEngine", lambda *_a, **_k: pytest.fail("verifier ran an engine"))
    monkeypatch.setattr(lab, "generate_case", lambda *_a, **_k: pytest.fail("verifier generated data"))
    monkeypatch.setattr(lab, "run_experiment", lambda *_a, **_k: pytest.fail("verifier ran experiment"))
    assert verify_fixed_lab_output(completed_lab, source_identity, config_hash) is True
    assert verify_fixed_lab_output(completed_lab, "sha256:wrong-source", config_hash) is False
    assert verify_fixed_lab_output(completed_lab, source_identity, "0" * 64) is False


def test_retained_failure_rows_are_complete_without_success_logs(tmp_path, monkeypatch):
    real_run = experiments._run

    def fail_one(candidate, window, config, label, costs, identity, seen, on_result):
        if candidate.name == "trend-ungated":
            return ScenarioResult(candidate=candidate.name, window=window.name, role=window.role,
                                  regime=window.regime, cost_scenario=label, status="error",
                                  error="ValueError: injected retained failure")
        return real_run(candidate, window, config, label, costs, identity, seen, on_result)

    monkeypatch.setattr(experiments, "_run", fail_one)
    output = tmp_path / "artifacts"
    lab.run_lab(_write_config(tmp_path / "input/config.json", _spec()), output)
    source_identity, config_hash = _accepted(output)
    report = _json(output / "hourly/report.json")
    failures = [row for row in report["scenarios"] if row["status"] == "error"]
    assert len(failures) == 4
    assert _json(output / "summary.json")["experiments"][0]["scenario_failures"] == 4
    assert not any((output / "hourly/runs" / (
        f"{row['window']}--{row['candidate']}--{row['cost_scenario']}"
    )).exists() for row in failures)
    assert verify_fixed_lab_output(output, source_identity, config_hash) is True


def test_rehashed_missing_candidate_is_rejected(completed_lab, tmp_path):
    output = _copy(completed_lab, tmp_path)
    source_identity, config_hash = _accepted(output)
    _rewrite_json(output, "hourly/report.json", lambda report: report["candidates"].pop())
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False


def test_rehashed_missing_attempt_is_rejected_even_with_matching_public_counts(completed_lab, tmp_path):
    output = _copy(completed_lab, tmp_path)
    source_identity, config_hash = _accepted(output)
    _rewrite_json(output, "hourly/report.json", lambda report: report["scenarios"].pop())
    _rewrite_json(output, "summary.json", lambda summary: summary["experiments"][0].update(scenario_count=59))
    _rewrite_comparison(output, lambda rows: rows.pop())
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False


def test_duplicate_attempt_cannot_replace_an_expected_combination(completed_lab, tmp_path):
    output = _copy(completed_lab, tmp_path)
    source_identity, config_hash = _accepted(output)

    def duplicate(report):
        report["scenarios"][-1] = dict(report["scenarios"][-2])

    _rewrite_json(output, "hourly/report.json", duplicate)

    def duplicate_csv(rows):
        rows[-1] = list(rows[-2])

    _rewrite_comparison(output, duplicate_csv)
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False


def test_rehashed_candidate_parameters_cannot_redefine_the_fixed_slate(completed_lab, tmp_path):
    output = _copy(completed_lab, tmp_path)
    source_identity, config_hash = _accepted(output)
    changed_parameters = {}
    changed_protocol = []

    def alter_report(report):
        record = report["candidates"][0]
        parameters = json.loads(record["parameters_json"])
        parameters["strategy"]["fast_period"] += 1
        record["parameters_json"] = _canonical_text(parameters)
        changed_parameters.update(parameters)
        protocol = {
            "source_identity": report["source_identity"],
            "config": json.loads(report["config_json"]),
            "candidates": report["candidates"],
            "windows": report["windows"],
        }
        report["protocol_id"] = "sha256:" + _canonical_hash(protocol)
        changed_protocol.append(report["protocol_id"])

    _rewrite_json(output, "hourly/report.json", alter_report)
    _rewrite_json(output, "hourly/slate.json",
                  lambda slate: slate["candidates"][0].update(parameters=changed_parameters))
    _rewrite_json(output, "summary.json",
                  lambda summary: summary["experiments"][0].update(protocol_id=changed_protocol[0]))
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False


def test_rehashed_generator_identity_and_comparison_values_are_rejected(completed_lab, tmp_path):
    output = _copy(completed_lab, tmp_path)
    source_identity, config_hash = _accepted(output)
    _rewrite_json(output, "hourly/generator.json",
                  lambda generator: generator["source_sha256"].__setitem__(0, "0" * 64))
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False

    output = _copy(completed_lab, tmp_path / "comparison")
    source_identity, config_hash = _accepted(output)

    def contradict_metric(rows):
        column = rows[0].index("total_return")
        rows[1][column] = "0.123456"

    _rewrite_comparison(output, contradict_metric)
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False


@pytest.mark.parametrize("mutation", ("selection", "holdout", "source", "configuration"))
def test_rehashed_false_selection_holdout_source_and_config_are_rejected(
        completed_lab, tmp_path, mutation):
    output = _copy(completed_lab, tmp_path)
    source_identity, config_hash = _accepted(output)
    if mutation == "selection":
        _rewrite_json(output, "hourly/report.json",
                      lambda report: report.update(selected_candidate="trend-ungated"))
        _rewrite_json(output, "summary.json",
                      lambda summary: summary["experiments"][0].update(selected_candidate="trend-ungated"))
    elif mutation == "holdout":
        _rewrite_json(output, "hourly/report.json",
                      lambda report: report.update(holdout_status="passed_research_screen_only"))
        _rewrite_json(output, "summary.json",
                      lambda summary: summary["experiments"][0].update(
                          holdout_status="passed_research_screen_only"))
    elif mutation == "source":
        _rewrite_json(output, "hourly/report.json",
                      lambda report: report.update(source_identity="sha256:forged"))
    else:
        _rewrite_json(output, "configuration.json", lambda config: config.update(seed=818))
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False


def test_rehashed_extra_test_role_run_is_rejected(completed_lab, tmp_path):
    output = _copy(completed_lab, tmp_path)
    source_identity, config_hash = _accepted(output)
    source = next((output / "hourly/runs").glob("train-*--cash--base/equity.csv"))
    extra = output / "hourly/runs/test-low_vol_trend--cash--base/equity.csv"
    extra.parent.mkdir()
    shutil.copyfile(source, extra)
    completion = _json(output / "complete.json")
    relative = extra.relative_to(output).as_posix()
    completion["sha256"][relative] = sha256(extra.read_bytes()).hexdigest()
    (output / "complete.json").write_text(
        json.dumps(completion, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
    )
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, source_identity, config_hash) is False


def test_gap_windows_and_ordered_multiple_timeframes_are_verified(tmp_path, monkeypatch):
    def retained_failure(candidate, window, config, label, costs, identity, seen, on_result):
        return ScenarioResult(candidate=candidate.name, window=window.name, role=window.role,
                              regime=window.regime, cost_scenario=label, status="error",
                              error="ValueError: intentionally retained")

    monkeypatch.setattr(experiments, "_run", retained_failure)
    spec = _spec()
    spec.update(timeframes=["daily_24h", "hourly"],
                regimes=["gap_shock", "low_vol_trend"])
    output = tmp_path / "artifacts"
    lab.run_lab(_write_config(tmp_path / "input/config.json", spec), output)
    config = _json(output / "configuration.json")
    reports = [_json(output / f"{timeframe}/report.json") for timeframe in spec["timeframes"]]
    assert [report["source_identity"] for report in reports] == [reports[0]["source_identity"]] * 2
    assert verify_fixed_lab_output(
        output, reports[0]["source_identity"], _canonical_hash(config),
    ) is True


def test_research_export_remains_generic_and_is_not_a_fixed_lab(tmp_path):
    output = tmp_path / "research"
    run_research(ROOT / "examples/research-run.json", output, export_version=2)
    artifact = inspect_artifact(output)
    assert artifact.kind == "research_export_v2"
    assert artifact.metadata["integrity"] == "verified"
    assert verify_fixed_lab_output(output, "any-source", "0" * 64) is False
    assert inspect_artifact(output).metadata["integrity"] == "verified"
