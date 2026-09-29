"""Integration checks for the mechanics-only strategy lab, not alpha evidence."""

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

import qte.strategy_lab as lab
from qte.strategies.candidates import FAMILIES


def spec(**changes):
    return {
        "schema_version": 1,
        "seed": 817,
        "bars_per_window": 128,
        "timeframes": ["hourly"],
        "regimes": ["low_vol_trend", "gap_shock"],
        **changes,
    }


def config_file(folder, value):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "configuration.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def completed_lab(tmp_path_factory):
    root = tmp_path_factory.mktemp("strategy-lab")
    config = config_file(root / "input", spec())
    output = root / "output"
    assert lab.run_lab(config, output) == output
    return root, config, output


def test_lab_preserves_full_slate_and_disallows_synthetic_promotion(completed_lab):
    _, _, output = completed_lab
    summary = read_json(output / "summary.json")
    report = read_json(output / "hourly" / "report.json")
    slate = read_json(output / "hourly" / "slate.json")
    expected = {f"{family}-{gate}" for family in FAMILIES for gate in ("macro", "ungated")}
    expected |= {"macro-router", "cash", "buy-and-hold"}
    assert len(report["candidates"]) == 15
    assert {candidate["name"] for candidate in report["candidates"]} == expected
    assert {candidate["name"] for candidate in slate["candidates"]} == expected
    assert summary["evidence"] == "synthetic_only"
    assert summary["experiments"][0]["candidate_count"] == 15
    assert summary["experiments"][0]["scenario_failures"] == 0
    assert report["selected_candidate"] is None
    assert report["holdout_status"] == "not_run_no_selection"
    assert all(not assessment["eligible"] for assessment in report["assessments"])
    assert all("synthetic_not_promotion_evidence" in assessment["exclusions"]
               for assessment in report["assessments"])
    assert {window["role"] for window in report["windows"]} == {"train", "validation", "test"}
    assert {row["role"] for row in report["scenarios"]} == {"train", "validation"}
    assert len(report["scenarios"]) == 2 * 2 * 15 * 2
    assert {row["candidate"] for row in report["scenarios"]} == expected
    assert {row["cost_scenario"] for row in report["scenarios"]} == {"base", "stress"}
    assert all(window["evidence"]["kind"] == "synthetic" for window in report["windows"])
    assert all(window["source_id"].startswith("synthetic:") for window in report["windows"])
    cash = [row for row in report["scenarios"] if row["candidate"] == "cash"]
    assert len(cash) == 8
    assert all(row["status"] == "ok" and row["total_return"] == 0 and row["fill_count"] == 0 for row in cash)
    router_record = next(candidate for candidate in report["candidates"] if candidate["name"] == "macro-router")
    assert router_record["factory_name"] == "qte.strategies.router.MacroRouterStrategy"
    assert router_record["eligible"] is True
    router_params = json.loads(router_record["parameters_json"])
    assert {cfg["kind"] for cfg in router_params["strategy_configs"]} == set(FAMILIES)
    assert router_params["macro_sha256"] is not None
    router_rows = [row for row in report["scenarios"] if row["candidate"] == "macro-router"]
    assert len(router_rows) == 8
    assert all(row["status"] == "ok" and row["normalized_config"] for row in router_rows)
    assert any(row["fill_count"] > 0 for row in router_rows)


def test_lab_completion_hashes_cover_every_artifact_and_event_log(completed_lab):
    _, _, output = completed_lab
    completion = read_json(output / "complete.json")
    assert completion["schema_version"] == 1
    artifact_paths = {path.relative_to(output).as_posix() for path in output.rglob("*")
                      if path.is_file() and path.name != "complete.json"}
    assert set(completion["sha256"]) == artifact_paths
    for relative, digest in completion["sha256"].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == digest
    assert "hourly/report.json" in artifact_paths
    assert "hourly/macro.json" in artifact_paths
    assert "hourly/slate.json" in artifact_paths
    assert "hourly/generator.json" in artifact_paths
    assert len(list((output / "hourly" / "data").glob("*.csv"))) == 6
    report = read_json(output / "hourly" / "report.json")
    for scenario in report["scenarios"]:
        folder = output / "hourly" / "runs" / (
            f"{scenario['window']}--{scenario['candidate']}--{scenario['cost_scenario']}")
        assert {path.name for path in folder.iterdir()} == {"equity.csv", "fills.csv", "orders.csv"}
        with (folder / "fills.csv").open(newline="") as stream:
            assert len(list(csv.DictReader(stream))) == scenario["fill_count"]
        with (folder / "orders.csv").open(newline="") as stream:
            assert len(list(csv.DictReader(stream))) == scenario["order_count"]
        with (folder / "equity.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
            assert rows and float(rows[-1]["equity"]) == scenario["final_equity"]


def test_repeating_fixed_seed_in_new_directory_is_byte_reproducible(completed_lab):
    root, config, output = completed_lab
    repeated = root / "repeated"
    lab.run_lab(config, repeated)
    assert read_json(output / "hourly" / "report.json") == read_json(repeated / "hourly" / "report.json")
    assert read_json(output / "complete.json") == read_json(repeated / "complete.json")


def test_scripted_macro_remains_labeled_delayed_and_without_dealer_evidence(completed_lab):
    _, _, output = completed_lab
    macro = read_json(output / "hourly" / "macro.json")
    assert macro["evidence"] == "fabricated_not_observed_macro_or_dealer_data"
    assert macro["dealer_observations"] == []
    assert macro["config"]["dealer_mode"] == "ignore"
    assert all(row["available_ns"] == row["reference_ns"] + lab.HOUR_NS
               for row in macro["observations"])
    with (output / "hourly" / "regime-decisions.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["macro_regime"] == "unknown"
    assert rows[0]["permitted_families"] == ""
    assert rows[1]["macro_regime"] == "risk_on"


def test_existing_output_is_never_overwritten(completed_lab):
    _, config, output = completed_lab
    before = (output / "complete.json").read_bytes()
    with pytest.raises(FileExistsError):
        lab.run_lab(config, output)
    assert (output / "complete.json").read_bytes() == before


@pytest.mark.parametrize("bad_spec", [
    spec(unknown=True), spec(strategy_parameters={"surprise": 1}),
    spec(engine={"unknown": 1}), spec(selection={"best_backtest_wins": True}),
    spec(execution={"commission_percent": 1}), spec(schema_version=True),
    spec(timeframes=["daily_24h", "daily_24h"]), spec(timeframes=["exchange_daily"]),
    spec(regimes=["actual_SPY"]), spec(bars_per_window=127),
])
def test_invalid_config_rejected_before_creating_output(tmp_path, bad_spec):
    path = config_file(tmp_path / "input", bad_spec)
    output = tmp_path / "output"
    with pytest.raises(ValueError):
        lab.run_lab(path, output)
    assert not output.exists()


def test_strategy_failures_are_recorded_without_discarding_other_candidates(tmp_path, monkeypatch):
    original = lab.CandidateStrategy

    def fail_trend(symbol, config, regime_filter=None):
        if config.kind == "trend":
            raise ValueError("injected candidate failure")
        return original(symbol, config, regime_filter)

    monkeypatch.setattr(lab, "CandidateStrategy", fail_trend)
    path = config_file(tmp_path / "input", spec(regimes=["low_vol_trend"]))
    output = lab.run_lab(path, tmp_path / "output")
    report = read_json(output / "hourly" / "report.json")
    assert len(report["candidates"]) == 15
    assert len(report["scenarios"]) == 60
    failures = [row for row in report["scenarios"] if row["status"] == "error"]
    assert len(failures) == 8
    assert {row["candidate"] for row in failures} == {"trend-macro", "trend-ungated"}
    assert all("injected candidate failure" in row["error"] for row in failures)
    assert all(row["total_return"] is None for row in failures)
    assert any(row["status"] == "ok" and row["candidate"] == "cash" for row in report["scenarios"])
    assert read_json(output / "summary.json")["experiments"][0]["scenario_failures"] == 8
    assert report["selected_candidate"] is None
    assert (output / "complete.json").is_file()


def test_artifact_io_failure_aborts_without_false_completion_marker(tmp_path, monkeypatch):
    import qte.cli
    original = qte.cli.write_csv

    def fail_event_log(path, columns, rows):
        if path.name == "equity.csv":
            raise OSError("injected artifact write failure")
        return original(path, columns, rows)

    monkeypatch.setattr(qte.cli, "write_csv", fail_event_log)
    path = config_file(tmp_path / "input", spec(regimes=["low_vol_trend"]))
    output = tmp_path / "output"
    with pytest.raises(OSError, match="artifact write failure"):
        lab.run_lab(path, output)
    assert output.exists()
    assert not (output / "complete.json").exists()
    assert not (output / "summary.json").exists()


def test_cli_lab_runs_in_venv_without_network_or_real_data_claims(tmp_path):
    path = config_file(tmp_path / "input", spec(regimes=["low_vol_trend"]))
    output = tmp_path / "output"
    result = subprocess.run(
        [sys.executable, "-m", "qte", "lab", "--config", str(path), "--output", str(output)],
        check=False, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert str(output) in result.stdout
    summary = read_json(output / "summary.json")
    assert summary["evidence"] == "synthetic_only"
    assert "No live or real historical market data was used." in summary["limitations"]
    assert summary["experiments"][0]["selected_candidate"] is None
