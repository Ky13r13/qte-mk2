"""Offline, captured-input exports for declarative strategy comparisons.

This module is deliberately an artifact writer, not a catalog format.  In
particular, a completed comparison is not automatically made visible to the
GUI: a reader which understands the holdout boundary is a separate feature.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from qte.comparison_config import capture_comparison_inputs, prepare_comparison
from qte.experiments import run_experiment
from qte.provenance import source_identity
from qte.research import compare_moving_average


_ARTIFACT = "comparison_artifact.json"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _input_name(configuration: dict[str, Any], index: int) -> str:
    """Return the copied, opaque name for one captured window input."""
    try:
        adapter = configuration["windows"][index]["data"]["adapter"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("comparison configuration has no normalized window adapter") from error
    if adapter == "csv":
        suffix = "csv"
    elif adapter == "alpaca":
        suffix = "json"
    else:
        # Preparation should already have rejected this.  Keeping the failure
        # explicit protects the artifact inventory if that contract changes.
        raise ValueError("comparison configuration has an unsupported window adapter")
    return f"inputs/window-{index:03d}.{suffix}"


def _copied_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(configuration)
    windows = copied.get("windows")
    if not isinstance(windows, list):
        raise ValueError("comparison configuration has no normalized windows")
    for index, window in enumerate(windows):
        if not isinstance(window, dict) or not isinstance(window.get("data"), dict):
            raise ValueError("comparison configuration has an invalid normalized window")
        window["data"]["path"] = _input_name(configuration, index)
    return copied


def _report_rows(report: object) -> tuple[tuple[str, ...], list[tuple[object, ...]]]:
    """Project either existing report type into a stable, complete CSV."""
    scenarios = getattr(report, "scenarios")
    if not isinstance(scenarios, tuple):
        scenarios = tuple(scenarios)
    if not scenarios:
        return ("status",), []
    fields = tuple(scenarios[0].__dataclass_fields__)
    return fields, [tuple(getattr(row, field) for field in fields) for row in scenarios]


def _summary(report: object, workflow: str) -> dict[str, Any]:
    scenarios = tuple(getattr(report, "scenarios"))
    failures = [asdict(row) for row in scenarios if getattr(row, "status", "ok") != "ok"]
    value: dict[str, Any] = {
        "workflow": workflow,
        "scenario_count": len(scenarios),
        "successful_scenario_count": len(scenarios) - len(failures),
        "scenario_failure_count": len(failures),
        # Duplicating failed rows is intentional: it provides a compact index
        # while report.json remains the complete, authoritative report.
        "failures": failures,
    }
    for name in ("selected_candidate", "holdout_status", "holdout_reasons", "parameter_status"):
        if hasattr(report, name):
            value[name] = getattr(report, name)
    return value


def _write_result(folder: Path, result: Any) -> None:
    """Use the ordinary CLI CSV projections for an engine result."""
    # Import lazily: the CLI imports this module only for its compare command.
    from qte.cli import write_csv

    write_csv(folder / "equity.csv", ("timestamp_ns", "equity", "gross_exposure"),
              ((point.timestamp_ns, point.equity, point.gross_exposure)
               for point in result.equity_curve))
    write_csv(folder / "fills.csv",
              ("fill_id", "order_id", "symbol", "side", "quantity", "timestamp_ns", "price", "commission"),
              ((fill.id, fill.order_id, fill.symbol, fill.side.name, fill.quantity,
                fill.effective_ns, fill.executed_price, fill.commission)
               for fill in result.fills))
    write_csv(folder / "orders.csv", ("order_id", "symbol", "status", "detail"),
              ((order.id, order.symbol, order.status.name, order.detail) for order in result.orders))


def _artifact_marker(workflow: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "comparison_export_v1",
        "workflow": workflow,
        "catalog_status": "unsupported_pending_holdout_aware_catalog_reader",
        "catalog_registration": False,
    }


def _write_json(path: Path, value: object) -> None:
    # x mode means a failed/incomplete export never replaces an existing file.
    with path.open("xb") as stream:
        stream.write(_json_bytes(value))


def run_comparison(config_path: Path, output: Path) -> Path:
    """Capture, validate, execute and export one offline comparison exactly once.

    Configuration and every source input are captured before the exclusive
    output directory is created.  Thus malformed inputs leave no output, while
    later engine or artifact failures leave an inspectable incomplete directory
    without a completion marker.
    """
    submitted, captured_inputs = capture_comparison_inputs(Path(config_path))
    prepared = prepare_comparison(submitted, captured_inputs)
    configuration = _copied_configuration(prepared.configuration)
    if source_identity() != prepared.source_identity:
        raise ValueError("source files changed during comparison preparation; rerun in a fresh process")

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # Nothing after this point deletes the directory: it is useful forensic
    # evidence if writing or source identity validation fails.
    inputs = tuple(captured_inputs)
    if len(inputs) != len(configuration["windows"]):
        raise ValueError("captured comparison window count does not match configuration")
    input_names = tuple(_input_name(prepared.configuration, index) for index in range(len(inputs)))
    (output / "inputs").mkdir()
    _write_json(output / _ARTIFACT, _artifact_marker(configuration["workflow"]))
    _write_json(output / "submitted.json", submitted)
    _write_json(output / "configuration.json", configuration)
    for name, content in zip(input_names, inputs, strict=True):
        path = output / name
        with path.open("xb") as stream:
            stream.write(content)

    workflow = configuration["workflow"]
    scenario_files: list[dict[str, Any]] = []
    if workflow == "historical_experiment":
        runs = output / "runs"
        runs.mkdir()
        scenario_index = 0

        def on_result(scenario: object, result: Any) -> None:
            nonlocal scenario_index
            folder = runs / f"scenario-{scenario_index:06d}"
            scenario_index += 1
            folder.mkdir()
            _write_result(folder, result)
            files = [(folder / filename).relative_to(output).as_posix()
                     for filename in ("equity.csv", "fills.csv", "orders.csv")]
            # This is deliberately a mapping, rather than an inferred row
            # offset: failed scenarios have no callback and no event files.
            scenario_files.append({
                "index": scenario_index - 1,
                "candidate": scenario.candidate,
                "window": scenario.window,
                "role": scenario.role,
                "regime": scenario.regime,
                "cost_scenario": scenario.cost_scenario,
                "files": files,
            })

        report = run_experiment(prepared.candidates, prepared.windows,
                                prepared.experiment_config, on_result=on_result)
    elif workflow == "moving_average_comparison":
        # This legacy API intentionally has no result callback.  Its four
        # scenarios are report-only; rerunning to synthesize event logs would
        # silently turn one comparison into another experiment.
        if len(prepared.windows) != 2:
            raise ValueError("legacy moving-average comparison needs exactly two windows")
        report = compare_moving_average(
            in_sample=prepared.windows[0].data,
            out_of_sample=prepared.windows[1].data,
            symbol=prepared.symbol,
            strategy_config=prepared.strategy_config,
            initial_cash=prepared.initial_cash,
            stressed_costs=prepared.stressed_costs,
        )
    else:
        raise ValueError("unsupported comparison workflow")

    from qte.cli import write_csv
    columns, rows = _report_rows(report)
    write_csv(output / "comparison.csv", columns, rows)
    _write_json(output / "report.json", asdict(report))
    _write_json(output / "summary.json", _summary(report, workflow))

    # A test input is holdout material even when selection does not execute a
    # test scenario.  Report-level files contain multiple roles (including
    # legacy out-of-sample data), so they are declared rather than pretending
    # they are train-only metadata.
    holdout_files = [name for index, window in enumerate(configuration["windows"])
                     if window["role"] == "test" for name in (input_names[index],)]
    holdout_files.extend(
        name for scenario in scenario_files if scenario["role"] == "test" for name in scenario["files"]
    )
    mixed_role_files = [
        _ARTIFACT, "submitted.json", "configuration.json", "manifest.json",
        "report.json", "summary.json", "comparison.csv",
    ]
    manifest = {
        "schema_version": 1,
        "artifact_kind": "comparison_export_v1",
        "workflow": workflow,
        "source_identity": prepared.source_identity,
        "canonical_configuration_sha256": _canonical_sha256(configuration),
        "input_sha256": {name: hashlib.sha256(content).hexdigest()
                         for name, content in zip(input_names, inputs, strict=True)},
        "assumptions": [
            "Checksums establish artifact integrity, not the authenticity of historical data.",
            "Window evidence and source declarations are supplied by the comparison configuration.",
            "This artifact is intentionally unsupported by the catalog until a holdout-aware reader exists.",
        ],
        "holdout_files": holdout_files,
        "holdout_files_explicit": True,
        "mixed_role_files": mixed_role_files,
        "scenario_files": scenario_files,
    }
    if hasattr(report, "protocol_id"):
        manifest["protocol_id"] = report.protocol_id
    if hasattr(report, "source_identity"):
        manifest["report_source_identity"] = report.source_identity
    _write_json(output / "manifest.json", manifest)
    # The experiment performs its own post-run check.  This second check also
    # covers legacy comparisons and all artifact I/O before finalization.
    inventory = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    if source_identity() != prepared.source_identity:
        raise ValueError("source files changed during comparison; incomplete output was retained")
    _write_json(output / "complete.json", {"schema_version": 1, "sha256": inventory})
    return output
