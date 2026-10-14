from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

import pytest

from qte.comparison_config import capture_comparison_inputs, prepare_comparison
from qte.comparison_export import run_comparison
from qte.experiments import run_experiment
from qte.research import compare_moving_average


INTERVAL = 60_000_000_000


def _bars(day: int) -> bytes:
    rows = ["symbol,timestamp,open,high,low,close,volume"]
    for index in range(12):
        value = 100 + day * 10 + index
        rows.append(f"SPY,2024-01-{day:02d}T00:{index:02d}:00Z,{value},{value + 1},{value - 1},{value + .5},100")
    return ("\n".join(rows) + "\n").encode()


def _window(name: str, role: str, regime: str, filename: str, raw: bytes) -> dict:
    # Historical is an intentional declaration fixture, not observed market
    # evidence. Public examples instead label their fabricated data synthetic.
    return {
        "name": name,
        "role": role,
        "regime": regime,
        "data": {"adapter": "csv", "path": filename, "interval_ns": INTERVAL, "action_free": True},
        "evidence": {
            "kind": "historical",
            "source": f"local fixture {name}",
            "source_sha256": sha256(raw).hexdigest(),
            "availability_policy": "bar_end_ns",
            "corporate_action_policy": "action_free_fixture",
        },
    }


def _config(workflow: str = "historical_experiment") -> tuple[dict, tuple[bytes, ...]]:
    raw = (_bars(1), _bars(2), _bars(3))
    windows = [
        _window("train", "train", "low_vol", "one.csv", raw[0]),
        _window("validation", "validation", "high_vol", "two.csv", raw[1]),
        _window("holdout", "test", "low_vol", "three.csv", raw[2]),
    ]
    config = {
        "schema_version": 1,
        "workflow": workflow,
        "windows": windows,
        "candidates": [{
            "name": "sma", "type": "moving_average_regime", "symbol": "SPY",
            "parameters": {"fast_period": 2, "slow_period": 3, "quantity": 1},
        }],
        "experiment": {},
    }
    if workflow == "moving_average_comparison":
        config["windows"] = [windows[0], windows[2]]
        config["windows"][1]["role"] = "test"
    return config, raw if workflow == "historical_experiment" else (raw[0], raw[2])


def _write_config(tmp_path, config: dict, raw: tuple[bytes, ...]):
    for window, content in zip(config["windows"], raw, strict=True):
        (tmp_path / window["data"]["path"]).write_bytes(content)
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(config))
    return path


def test_historical_export_matches_existing_api_and_captures_inputs(tmp_path):
    config, raw = _config()
    path = _write_config(tmp_path, config, raw)
    captured_config, captured = capture_comparison_inputs(path)
    expected = asdict(run_experiment(
        prepare_comparison(captured_config, captured).candidates,
        prepare_comparison(captured_config, captured).windows,
        prepare_comparison(captured_config, captured).experiment_config,
    ))

    output = run_comparison(path, tmp_path / "export")
    report = json.loads((output / "report.json").read_text())
    assert report == json.loads(json.dumps(expected))
    assert (output / "inputs/window-000.csv").read_bytes() == raw[0]
    assert json.loads((output / "configuration.json").read_text())["windows"][0]["data"]["path"] == "inputs/window-000.csv"
    assert json.loads((output / "submitted.json").read_text()) == config
    assert json.loads((output / "comparison_artifact.json").read_text())["artifact_kind"] == "comparison_export_v1"
    assert not (output / "runs").is_dir() or all(
        {item.name for item in folder.iterdir()} == {"equity.csv", "fills.csv", "orders.csv"}
        for folder in (output / "runs").iterdir()
    )


def test_legacy_export_is_exact_existing_four_scenario_report(tmp_path):
    config, raw = _config("moving_average_comparison")
    path = _write_config(tmp_path, config, raw)
    captured_config, captured = capture_comparison_inputs(path)
    prepared = prepare_comparison(captured_config, captured)
    expected = asdict(compare_moving_average(
        in_sample=prepared.windows[0].data, out_of_sample=prepared.windows[1].data,
        symbol=prepared.symbol, strategy_config=prepared.strategy_config,
        initial_cash=prepared.initial_cash, stressed_costs=prepared.stressed_costs,
    ))
    output = run_comparison(path, tmp_path / "legacy")
    written = json.loads((output / "report.json").read_text())
    assert written == json.loads(json.dumps(expected))
    assert len(written["scenarios"]) == 4
    assert not (output / "runs").exists()


def test_captured_bytes_are_not_reread_and_completion_hashes_exact_inventory(tmp_path, monkeypatch):
    config, raw = _config()
    path = _write_config(tmp_path, config, raw)
    import qte.comparison_export as exporter

    original_capture = exporter.capture_comparison_inputs

    def capture_then_change(config_path):
        result = original_capture(config_path)
        (tmp_path / "one.csv").write_bytes(b"changed after capture\n")
        return result

    monkeypatch.setattr(exporter, "capture_comparison_inputs", capture_then_change)
    output = exporter.run_comparison(path, tmp_path / "export")
    assert (output / "inputs/window-000.csv").read_bytes() == raw[0]
    complete = json.loads((output / "complete.json").read_text())
    inventory = {item.relative_to(output).as_posix(): sha256(item.read_bytes()).hexdigest()
                 for item in output.rglob("*") if item.is_file() and item.name != "complete.json"}
    assert complete["sha256"] == dict(sorted(inventory.items()))

    # A valid second capture still refuses to overwrite the completed export.
    (tmp_path / "one.csv").write_bytes(raw[0])
    with pytest.raises(FileExistsError):
        exporter.run_comparison(path, output)


def test_artifact_io_failure_never_writes_completion_marker(tmp_path, monkeypatch):
    config, raw = _config()
    path = _write_config(tmp_path, config, raw)
    import qte.comparison_export as exporter

    actual = exporter._write_json

    def fail_report(target, value):
        if target.name == "report.json":
            raise OSError("simulated full disk")
        return actual(target, value)

    monkeypatch.setattr(exporter, "_write_json", fail_report)
    output = tmp_path / "incomplete"
    with pytest.raises(OSError, match="full disk"):
        exporter.run_comparison(path, output)
    assert output.is_dir()
    assert not (output / "complete.json").exists()


@pytest.mark.parametrize("workflow", ["historical_experiment", "moving_average_comparison"])
def test_holdout_input_and_mixed_reports_are_declared_without_selection(tmp_path, workflow):
    config, raw = _config(workflow)
    for window in config["windows"]:
        window["evidence"]["kind"] = "synthetic"
    output = run_comparison(_write_config(tmp_path, config, raw), tmp_path / "export")
    manifest = json.loads((output / "manifest.json").read_text())
    assert f"inputs/window-{len(raw) - 1:03d}.csv" in manifest["holdout_files"]
    assert {"report.json", "comparison.csv", "summary.json", "configuration.json"} <= set(manifest["mixed_role_files"])
    assert all((output / name).is_file() for name in manifest["holdout_files"] + manifest["mixed_role_files"])
    if workflow == "historical_experiment":
        report = json.loads((output / "report.json").read_text())
        assert report["selected_candidate"] is None
        assert all(row["role"] != "test" for row in manifest["scenario_files"])
    else:
        assert manifest["scenario_files"] == []


def test_failed_trial_is_retained_and_success_file_mapping_is_unambiguous(tmp_path, monkeypatch):
    import qte
    original = qte.BacktestEngine.run
    calls = 0

    def fail_once(engine, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("deliberate scenario regression fixture")
        return original(engine, *args, **kwargs)

    monkeypatch.setattr(qte.BacktestEngine, "run", fail_once)
    config, raw = _config()
    output = run_comparison(_write_config(tmp_path, config, raw), tmp_path / "export")
    report = json.loads((output / "report.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(report["scenarios"]) == 4
    assert report["scenarios"][0]["status"] == "error"
    assert "deliberate scenario" in report["scenarios"][0]["error"]
    assert summary["scenario_failure_count"] == 1
    assert summary["failures"] == [report["scenarios"][0]]
    successful = [row for row in report["scenarios"] if row["status"] == "ok"]
    assert len(manifest["scenario_files"]) == len(successful) == 3
    for index, (mapping, scenario) in enumerate(zip(manifest["scenario_files"], successful, strict=True)):
        assert mapping["index"] == index
        assert {key: mapping[key] for key in ("candidate", "window", "role", "cost_scenario")} == {
            key: scenario[key] for key in ("candidate", "window", "role", "cost_scenario")}
        assert all(name.startswith(f"runs/scenario-{index:06d}/") for name in mapping["files"])
        assert all((output / name).is_file() for name in mapping["files"])


def test_repeated_exports_have_identical_files_and_source_changes_never_finalize(tmp_path, monkeypatch):
    import qte.comparison_export as exporter
    config, raw = _config()
    path = _write_config(tmp_path, config, raw)
    first = run_comparison(path, tmp_path / "first")
    second = run_comparison(path, tmp_path / "second")
    def content(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert content(first) == content(second)
    actual_identity = exporter.source_identity
    calls = 0
    def changed_identity():
        nonlocal calls
        calls += 1
        return actual_identity() if calls == 1 else "sha256:" + "0" * 64
    monkeypatch.setattr(exporter, "source_identity", changed_identity)
    incomplete = tmp_path / "changed"
    with pytest.raises(ValueError, match="source files changed"):
        run_comparison(path, incomplete)
    assert incomplete.is_dir()
    assert not (incomplete / "complete.json").exists()
