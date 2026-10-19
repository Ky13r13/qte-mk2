"""Strict, role-aware verification for completed comparison exports.

This reader validates stored evidence.  It deliberately does not run a strategy
and does not compare historical source identities with the currently installed
QTE source tree.
"""
from __future__ import annotations

import csv
from hashlib import sha256
import io
import json
import math
import re
from typing import Any

from .artifact_io import FILE_LIMIT, INVENTORY_LIMIT, METADATA_LIMIT, TOTAL_ARTIFACT_BYTES


_ROOT_FILES = {
    "comparison_artifact.json", "submitted.json", "configuration.json",
    "manifest.json", "report.json", "summary.json", "comparison.csv",
}
_ASSUMPTIONS = [
    "Checksums establish artifact integrity, not the authenticity of historical data.",
    "Window evidence and source declarations are supplied by the comparison configuration.",
    "This artifact is intentionally unsupported by the catalog until a holdout-aware reader exists.",
]
_IDENTITY = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

_EXPERIMENT_SCENARIO_FIELDS = (
    "candidate", "window", "role", "regime", "cost_scenario", "status", "error",
    "total_return", "maximum_drawdown", "average_gross_exposure", "turnover",
    "trade_count", "open_trade_count", "order_count", "fill_count",
    "rejected_order_count", "canceled_order_count", "final_equity", "normalized_config",
)
_LEGACY_SCENARIO_FIELDS = (
    "sample", "cost_scenario", "total_return", "maximum_drawdown", "trade_count",
    "order_count", "fill_count", "dataset_hash", "normalized_config", "strategy_config",
    "source_identity", "source_id", "start_ns", "end_ns",
)
_WINDOW_FIELDS = (
    "name", "role", "regime", "start_ns", "end_ns", "interval_ns", "symbols",
    "currency", "dataset_hash", "source_id", "bar_count", "gap_count", "evidence",
)
_CANDIDATE_FIELDS = (
    "name", "parameters_json", "history_capacity", "eligible", "factory_name",
    "factory_source_identity",
)
_ASSESSMENT_FIELDS = (
    "candidate", "eligible", "exclusions", "validation_score",
    "validation_mean_stressed_return",
)


def _fail(message: str) -> None:
    raise ValueError(message)


def _object(value: object, fields: tuple[str, ...] | set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        _fail(f"{label} has an invalid shape")
    return value


def _array(value: object, label: str) -> list[Any]:
    if type(value) is not list:
        _fail(f"{label} must be an array")
    return value


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(f"{label} must be an integer >= {minimum}")
    return value


def _finite(value: object, label: str, *, optional: bool = False) -> int | float | None:
    if optional and value is None:
        return None
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        _fail(f"{label} must be a finite number")
    return value


def _text(value: object, label: str, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value) or "\x00" in value or len(value) > 100_000:
        _fail(f"{label} must be bounded text")
    return value


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("artifact contains a non-canonical JSON value") from error


def _same(left: object, right: object, label: str) -> None:
    # Canonical JSON comparison preserves distinctions hidden by Python equality,
    # notably True versus 1 and 1 versus 1.0.
    if _canonical(left) != _canonical(right):
        _fail(f"{label} is inconsistent")


def _json(raw: bytes, name: str) -> Any:
    # Lazy import avoids a cycle when gui.artifacts dispatches to this reader.
    from .artifacts import strict_json
    try:
        return strict_json(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} is invalid JSON") from error


def _input_name(configuration: dict[str, Any], index: int) -> str:
    adapter = configuration["windows"][index]["data"]["adapter"]
    suffix = "csv" if adapter == "csv" else "json" if adapter == "alpaca" else None
    if suffix is None:
        _fail("configuration contains an unsupported adapter")
    return f"inputs/window-{index:03d}.{suffix}"


def _verify_completion(files: dict[str, bytes]) -> dict[str, str]:
    if type(files) is not dict or not files or len(files) > INVENTORY_LIMIT:
        _fail("comparison artifact file inventory is invalid")
    total = 0
    for name, content in files.items():
        if (type(name) is not str or type(content) is not bytes or not name or name.startswith("/")
                or "\\" in name or any(part in ("", ".", "..") for part in name.split("/"))):
            _fail("comparison artifact contains an invalid file entry")
        # Reports grow with the supported slate; they are stored artifacts, not
        # HTTP metadata bodies. Only configuration/marker inputs use 1 MiB.
        limit = METADATA_LIMIT if name in {
            "submitted.json", "configuration.json", "comparison_artifact.json",
        } else FILE_LIMIT
        total += len(content)
        if len(content) > limit or total > TOTAL_ARTIFACT_BYTES:
            _fail("comparison artifact exceeds its byte limits")
    if "complete.json" not in files:
        _fail("comparison artifact is incomplete")
    complete = _object(_json(files["complete.json"], "complete.json"),
                       ("schema_version", "sha256"), "complete.json")
    if type(complete["schema_version"]) is not int or complete["schema_version"] != 1:
        _fail("complete.json has an unsupported version")
    checksums = complete["sha256"]
    expected = set(files) - {"complete.json"}
    if type(checksums) is not dict or set(checksums) != expected:
        _fail("complete.json does not describe the exact file inventory")
    for name in sorted(expected):
        digest = checksums[name]
        if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
            _fail("complete.json contains an invalid checksum")
        if sha256(files[name]).hexdigest() != digest:
            _fail(f"checksum mismatch for {name}")
    return checksums


def _verify_configuration(files: dict[str, bytes]) -> tuple[dict[str, Any], list[Any], list[str]]:
    from qte.comparison_config import compile_comparison_configuration

    submitted = _json(files["submitted.json"], "submitted.json")
    effective = _json(files["configuration.json"], "configuration.json")
    if type(submitted) is not dict or type(effective) is not dict:
        _fail("comparison configuration files must contain objects")
    try:
        submitted_normalized = compile_comparison_configuration(submitted)
        effective_normalized = compile_comparison_configuration(effective)
    except (ValueError, TypeError) as error:
        raise ValueError("comparison configuration is invalid") from error
    _same(effective, effective_normalized, "effective configuration normalization")
    names = [_input_name(effective, index) for index in range(len(effective["windows"]))]
    from qte.comparison_config import _TOTAL_DATA_LIMIT
    if sum(len(files.get(name, b"")) for name in names) > _TOTAL_DATA_LIMIT:
        _fail("comparison inputs exceed the aggregate byte limit")
    copied = json.loads(_canonical(submitted_normalized))
    for index, name in enumerate(names):
        copied["windows"][index]["data"]["path"] = name
    _same(effective, copied, "submitted and effective configurations")
    loaded = []
    from qte.research_config import _load_data
    for index, (window, name) in enumerate(zip(effective["windows"], names, strict=True)):
        if name not in files:
            _fail(f"captured input {name} is missing")
        if sha256(files[name]).hexdigest() != window["evidence"]["source_sha256"]:
            _fail(f"window {index} evidence checksum is inconsistent")
        try:
            loaded.append(_load_data(window["data"], files[name]))
        except (ValueError, TypeError, RuntimeError) as error:
            raise ValueError(f"captured input {name} is invalid") from error
    from qte.experiments import Evidence, ResearchWindow, _validate_windows
    windows = tuple(ResearchWindow(spec["name"], adapter.dataset, spec["role"], spec["regime"],
                                   Evidence(**spec["evidence"]))
                    for spec, adapter in zip(effective["windows"], loaded, strict=True))
    for candidate in effective["candidates"]:
        if any(candidate["symbol"] not in window.data.symbols for window in windows):
            _fail("candidate symbol must exist in every window")
    if effective["workflow"] == "historical_experiment":
        _validate_windows(windows)
    else:
        train, test = (window.data for window in windows)
        if train.end_ns > test.start_ns:
            _fail("train/test windows must be chronological and non-overlapping")
        if train.interval_ns != test.interval_ns or train.currency != test.currency:
            _fail("train/test windows must have matching interval and currency")
    return effective, loaded, names


def _verify_marker(marker: object, workflow: str) -> None:
    expected = {
        "schema_version": 1,
        "artifact_kind": "comparison_export_v1",
        "workflow": workflow,
        # Historical advisory retained verbatim; the reader does not rewrite it.
        "catalog_status": "unsupported_pending_holdout_aware_catalog_reader",
        "catalog_registration": False,
    }
    _same(marker, expected, "comparison artifact marker")


def _validate_experiment_report(report: object, configuration: dict[str, Any], loaded: list[Any]) -> tuple[list[dict[str, Any]], str | None]:
    from qte.comparison_config import _build_candidate
    fields = {
        "protocol_id", "source_identity", "config_json", "candidates", "windows",
        "scenarios", "assessments", "selected_candidate", "holdout_status",
        "holdout_reasons", "parameter_status", "limitations",
    }
    report = _object(report, fields, "historical experiment report")
    if type(report["source_identity"]) is not str or _IDENTITY.fullmatch(report["source_identity"]) is None:
        _fail("report source identity is invalid")
    if type(report["protocol_id"]) is not str or _IDENTITY.fullmatch(report["protocol_id"]) is None:
        _fail("report protocol identity is invalid")
    config_json = _text(report["config_json"], "report config_json")
    try:
        parsed_config = _json(config_json.encode(), "report config_json")
    except UnicodeEncodeError as error:
        raise ValueError("report config_json is invalid") from error
    if config_json != _canonical(parsed_config):
        _fail("report config_json is not canonical")
    _same(parsed_config, configuration["experiment"], "report experiment configuration")

    candidates = _array(report["candidates"], "report candidates")
    if len(candidates) != len(configuration["candidates"]):
        _fail("report candidate count is inconsistent")
    for index, (record, configured) in enumerate(zip(candidates, configuration["candidates"], strict=True)):
        record = _object(record, _CANDIDATE_FIELDS, f"candidate record {index}")
        if record["name"] != configured["name"] or type(record["eligible"]) is not bool or record["eligible"] != configured["eligible"]:
            _fail("report candidate identity is inconsistent")
        expected_parameters = {"type": configured["type"], "symbol": configured["symbol"], **configured["parameters"]}
        if record["parameters_json"] != _canonical(expected_parameters):
            _fail("report candidate parameters are inconsistent")
        _integer(record["history_capacity"], "candidate history capacity", 1)
        if record["history_capacity"] != _build_candidate(configured)[0].history_capacity:
            _fail("candidate history capacity is inconsistent")
        _text(record["factory_name"], "candidate factory name")
        identity = record["factory_source_identity"]
        if identity != "unavailable" and (type(identity) is not str or _IDENTITY.fullmatch(identity) is None):
            _fail("candidate factory source identity is invalid")

    windows = _array(report["windows"], "report windows")
    if len(windows) != len(configuration["windows"]):
        _fail("report window count is inconsistent")
    for index, (record, configured, adapter) in enumerate(zip(windows, configuration["windows"], loaded, strict=True)):
        record = _object(record, _WINDOW_FIELDS, f"window record {index}")
        data = adapter.dataset
        expected = {
            "name": configured["name"], "role": configured["role"], "regime": configured["regime"],
            "start_ns": data.start_ns, "end_ns": data.end_ns, "interval_ns": data.interval_ns,
            "symbols": list(data.symbols), "currency": data.currency, "dataset_hash": data.hash,
            "source_id": data.source_id, "bar_count": data.bar_count, "gap_count": data.gap_count,
            "evidence": configured["evidence"],
        }
        _same(record, expected, f"report window {index}")

    scenarios = _array(report["scenarios"], "report scenarios")
    candidates_by_name = {item["name"]: item for item in configuration["candidates"]}
    windows_by_name = {item["name"]: item for item in configuration["windows"]}
    selected = report["selected_candidate"]
    if selected is not None and (type(selected) is not str or selected not in candidates_by_name):
        _fail("selected candidate is invalid")
    expected_attempts = [
        (candidate["name"], window["name"], window["role"], window["regime"], cost)
        for window in configuration["windows"] if window["role"] != "test"
        for candidate in configuration["candidates"] for cost in ("base", "stress")
    ]
    if selected is not None:
        expected_attempts.extend(
            (selected, window["name"], "test", window["regime"], cost)
            for window in configuration["windows"] if window["role"] == "test"
            for cost in ("base", "stress")
        )
    if len(scenarios) != len(expected_attempts):
        _fail("report scenario slate is incomplete")
    for index, (scenario, expected) in enumerate(zip(scenarios, expected_attempts, strict=True)):
        scenario = _object(scenario, _EXPERIMENT_SCENARIO_FIELDS, f"scenario {index}")
        actual = tuple(scenario[name] for name in ("candidate", "window", "role", "regime", "cost_scenario"))
        if actual != expected:
            _fail("report scenario ordering or identity is inconsistent")
        if scenario["status"] not in ("ok", "error"):
            _fail("report scenario status is invalid")
        for count in ("trade_count", "open_trade_count", "order_count", "fill_count",
                      "rejected_order_count", "canceled_order_count"):
            _integer(scenario[count], f"scenario {count}")
        if scenario["status"] == "ok":
            if scenario["error"] is not None:
                _fail("successful scenario contains an error")
            for metric in ("total_return", "maximum_drawdown", "average_gross_exposure",
                           "turnover", "final_equity"):
                _finite(scenario[metric], f"scenario {metric}")
            candidate = candidates_by_name[scenario["candidate"]]
            _validate_normalized_config(scenario["normalized_config"], configuration,
                                        candidate, scenario["cost_scenario"], report["source_identity"])
        else:
            _text(scenario["error"], "scenario error")
            if any(scenario[name] is not None for name in ("total_return", "maximum_drawdown",
                    "average_gross_exposure", "turnover", "final_equity", "normalized_config")):
                _fail("failed scenario contains result values")

    assessments = _array(report["assessments"], "report assessments")
    if len(assessments) != len(configuration["candidates"]):
        _fail("report assessment count is inconsistent")
    eligible: list[dict[str, Any]] = []
    for index, (assessment, candidate) in enumerate(zip(assessments, configuration["candidates"], strict=True)):
        assessment = _object(assessment, _ASSESSMENT_FIELDS, f"assessment {index}")
        if assessment["candidate"] != candidate["name"] or type(assessment["eligible"]) is not bool:
            _fail("report assessment identity is inconsistent")
        exclusions = _array(assessment["exclusions"], "assessment exclusions")
        if any(type(item) is not str or not item for item in exclusions) or len(set(exclusions)) != len(exclusions):
            _fail("report assessment exclusions are invalid")
        score = assessment["validation_score"]
        if score is not None:
            if type(score) is not list or len(score) != 2:
                _fail("report validation score is invalid")
            _finite(score[0], "validation score"); _finite(score[1], "validation score")
        _finite(assessment["validation_mean_stressed_return"], "validation mean return", optional=True)
        if assessment["eligible"] != (not exclusions):
            _fail("assessment eligibility is inconsistent with exclusions")
        if assessment["eligible"]:
            if score is None or not candidate["eligible"]:
                _fail("eligible assessment has no validation score")
            if (candidates[index]["factory_source_identity"] == "unavailable" or
                    any(row["candidate"] == candidate["name"] and row["role"] != "test" and
                        row["status"] == "error" for row in scenarios)):
                _fail("failed or unverified candidate cannot be eligible")
            eligible.append(assessment)
    expected_selected = None
    if eligible:
        eligible.sort(key=lambda row: (-row["validation_score"][0], -row["validation_score"][1], row["candidate"]))
        expected_selected = eligible[0]["candidate"]
    if selected != expected_selected:
        _fail("selected candidate is inconsistent with assessments")
    if selected is not None and any(window["evidence"]["kind"] != "historical" for window in configuration["windows"]):
        _fail("synthetic evidence cannot promote a candidate")
    if selected is not None and any("synthetic" in adapter.dataset.source_id.lower() for adapter in loaded):
        _fail("synthetic source cannot promote a candidate")
    reasons = _array(report["holdout_reasons"], "holdout reasons")
    if any(type(item) is not str or not item for item in reasons):
        _fail("holdout reasons are invalid")
    if selected is None:
        if report["holdout_status"] != "not_run_no_selection" or reasons:
            _fail("holdout status is inconsistent with no selection")
    elif report["holdout_status"] not in ("failed", "passed_research_screen_only"):
        _fail("holdout status is inconsistent with selection")
    elif (report["holdout_status"] == "failed") != bool(reasons):
        _fail("holdout status is inconsistent with reasons")
    _text(report["parameter_status"], "parameter status")
    limitations = _array(report["limitations"], "report limitations")
    if not limitations or any(type(item) is not str or not item for item in limitations):
        _fail("report limitations are invalid")

    protocol = {
        "source_identity": report["source_identity"], "config": parsed_config,
        "candidates": candidates, "windows": windows,
    }
    protocol_id = "sha256:" + sha256(_canonical(protocol).encode()).hexdigest()
    if report["protocol_id"] != protocol_id:
        _fail("report protocol identity is inconsistent")
    return scenarios, selected


def _validate_legacy_report(report: object, configuration: dict[str, Any], loaded: list[Any]) -> list[dict[str, Any]]:
    report = _object(report, ("hypothesis", "parameter_status", "scenarios"), "legacy report")
    _text(report["hypothesis"], "legacy hypothesis")
    _text(report["parameter_status"], "legacy parameter status")
    scenarios = _array(report["scenarios"], "legacy scenarios")
    expected = [("in_sample", "baseline_zero_cost", 0), ("in_sample", "stressed_costs", 0),
                ("out_of_sample", "baseline_zero_cost", 1), ("out_of_sample", "stressed_costs", 1)]
    if len(scenarios) != 4:
        _fail("legacy report must contain exactly four scenarios")
    source_identity = None
    candidate = configuration["candidates"][0]
    expected_strategy = json.dumps({"symbol": candidate["symbol"], **candidate["parameters"]}, sort_keys=True)
    for index, (scenario, (sample, cost, window_index)) in enumerate(zip(scenarios, expected, strict=True)):
        scenario = _object(scenario, _LEGACY_SCENARIO_FIELDS, f"legacy scenario {index}")
        data = loaded[window_index].dataset
        expected_identity = {
            "sample": sample, "cost_scenario": cost, "dataset_hash": data.hash,
            "source_id": data.source_id, "start_ns": data.start_ns, "end_ns": data.end_ns,
        }
        if any(scenario[key] != value for key, value in expected_identity.items()):
            _fail("legacy scenario window identity is inconsistent")
        for metric in ("total_return", "maximum_drawdown"):
            _finite(scenario[metric], f"legacy scenario {metric}")
        for count in ("trade_count", "order_count", "fill_count"):
            _integer(scenario[count], f"legacy scenario {count}")
        _validate_normalized_config(scenario["normalized_config"], configuration,
                                    candidate, cost, scenario["source_identity"])
        if scenario["strategy_config"] != expected_strategy:
            _fail("legacy strategy configuration is inconsistent")
        identity = scenario["source_identity"]
        if type(identity) is not str or _IDENTITY.fullmatch(identity) is None:
            _fail("legacy source identity is invalid")
        if source_identity is None:
            source_identity = identity
        elif source_identity != identity:
            _fail("legacy scenarios have inconsistent source identities")
    return scenarios


def _validate_normalized_config(value: object, configuration: dict[str, Any], candidate: dict[str, Any],
                                cost: str, identity: str) -> None:
    """Check the stored engine snapshot against declarations, without replay."""
    from qte.comparison_config import _build_candidate
    text = _text(value, "normalized config")
    pairs = [part.partition("=") for part in text.split(";")]
    if any(not separator for _, separator, _ in pairs) or len({key for key, _, _ in pairs}) != len(pairs):
        _fail("normalized config is invalid")
    actual = {key: content for key, _, content in pairs}
    experiment = configuration["experiment"]
    historical = configuration["workflow"] == "historical_experiment"
    if historical:
        costs = experiment["base_costs" if cost == "base" else "stressed_costs"]
    else:
        costs = (dict(commission_bps=0, spread_bps=0, slippage_bps=0)
                 if cost == "baseline_zero_cost" else experiment["stressed_costs"])
    expected = {
        "initial_cash": experiment["initial_cash"], **costs,
        "max_order_quantity": experiment["max_order_quantity"] if historical else 2**63 - 1,
        "max_symbol_allocation": experiment["max_symbol_allocation"] if historical else 1.0,
        "max_gross_leverage": experiment["max_gross_leverage"] if historical else 1.0,
        "allow_short": "false", "cash_floor": 0.0,
        "history_capacity": _build_candidate(candidate)[0].history_capacity,
        "random_seed": experiment["random_seed"] if historical else 0,
        "build_identity": identity,
    }
    if set(actual) != set(expected):
        _fail("normalized config fields are inconsistent")
    for key, expected_value in expected.items():
        raw = actual[key]
        if isinstance(expected_value, str):
            valid = raw == expected_value
        elif key in {"max_order_quantity", "history_capacity", "random_seed"}:
            valid = re.fullmatch(r"0|[1-9][0-9]*", raw) is not None and int(raw) == expected_value
        else:
            try:
                parsed = float(raw)
                valid = math.isfinite(parsed) and parsed == expected_value
            except (ValueError, OverflowError):
                valid = False
        if not valid:
            _fail(f"normalized config {key} is inconsistent")


def _comparison_csv(report: dict[str, Any], fields: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(fields)
    writer.writerows(tuple(row[field] for field in fields) for row in report["scenarios"])
    return stream.getvalue().encode()


def _validate_event_csv(raw: bytes, name: str, scenario: dict[str, Any], window: dict[str, Any]) -> list[list[str]]:
    expected_headers = {
        "equity.csv": ["timestamp_ns", "equity", "gross_exposure"],
        "fills.csv": ["fill_id", "order_id", "symbol", "side", "quantity", "timestamp_ns", "price", "commission"],
        "orders.csv": ["order_id", "symbol", "status", "detail"],
    }
    base = name.rsplit("/", 1)[-1]
    try:
        rows = csv.reader(io.StringIO(raw.decode("utf-8")), strict=True)
        header = next(rows)
        if header != expected_headers[base]:
            _fail(f"{name} has an invalid header")
        values = list(rows)
    except (UnicodeDecodeError, csv.Error, StopIteration) as error:
        raise ValueError(f"{name} is invalid CSV") from error
    if any(len(row) != len(header) for row in values):
        _fail(f"{name} has an invalid row")
    if base == "equity.csv" and not values:
        _fail(f"{name} has no equity observations")
    if base == "fills.csv" and len(values) != scenario["fill_count"]:
        _fail(f"{name} count is inconsistent with the report")
    if base == "orders.csv" and len(values) != scenario["order_count"]:
        _fail(f"{name} count is inconsistent with the report")
    previous_timestamp = None
    seen_ids: set[int] = set()
    for row in values:
        try:
            if base in ("equity.csv", "fills.csv"):
                timestamp = int(row[0] if base == "equity.csv" else row[5])
            if base == "equity.csv":
                if not all(math.isfinite(float(value)) for value in row[1:]) or float(row[2]) < 0:
                    raise ValueError
            elif base == "fills.csv":
                fill_id, order_id, quantity = int(row[0]), int(row[1]), int(row[4])
                if not 0 < fill_id < 2**64 or not 0 < order_id < 2**64 or not 0 < quantity < 2**63:
                    raise ValueError
                if (not all(math.isfinite(float(row[i])) for i in (6, 7))
                        or float(row[6]) <= 0 or float(row[7]) < 0 or row[3] not in {"BUY", "SELL"}
                        or row[2] not in window["symbols"]):
                    raise ValueError
            else:
                order_id = int(row[0])
                if (not 0 < order_id < 2**64 or row[1] not in window["symbols"]
                        or row[2] not in {"NEW", "OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"}
                        or "\x00" in row[3]):
                    raise ValueError
            if base != "equity.csv":
                event_id = int(row[0])
                if event_id in seen_ids:
                    raise ValueError
                seen_ids.add(event_id)
        except (ValueError, OverflowError) as error:
            raise ValueError(f"{name} contains an invalid value") from error
        if base in ("equity.csv", "fills.csv") and not window["start_ns"] <= timestamp <= window["end_ns"]:
            _fail(f"{name} timestamp falls outside its mapped window")
        if base in ("equity.csv", "fills.csv"):
            if previous_timestamp is not None and timestamp < previous_timestamp:
                _fail(f"{name} timestamps are not chronological")
            previous_timestamp = timestamp
    if base == "equity.csv" and float(values[-1][1]) != scenario["final_equity"]:
        _fail(f"{name} final equity is inconsistent with the report")
    return values


def _verify_summary(summary: object, report: dict[str, Any], workflow: str) -> None:
    failures = [row for row in report["scenarios"] if row.get("status", "ok") != "ok"]
    expected: dict[str, Any] = {
        "workflow": workflow, "scenario_count": len(report["scenarios"]),
        "successful_scenario_count": len(report["scenarios"]) - len(failures),
        "scenario_failure_count": len(failures), "failures": failures,
    }
    for name in ("selected_candidate", "holdout_status", "holdout_reasons", "parameter_status"):
        if name in report:
            expected[name] = report[name]
    _same(summary, expected, "comparison summary")


def verify_comparison_files(files: dict[str, bytes]) -> dict[str, object]:
    """Verify a complete comparison export and return only catalog-safe classes.

    ``files`` must be the stable, bounded capture of one artifact directory.
    Errors are reported as :class:`ValueError`; callers retain responsibility for
    mapping that failure to their public fail-closed artifact status.
    """
    checksums = _verify_completion(files)
    required = _ROOT_FILES | {"complete.json"}
    if not required <= set(files):
        _fail("comparison artifact root files are incomplete")
    configuration, loaded, input_names = _verify_configuration(files)
    workflow = configuration["workflow"]
    _verify_marker(_json(files["comparison_artifact.json"], "comparison_artifact.json"), workflow)

    manifest = _json(files["manifest.json"], "manifest.json")
    common_manifest = {
        "schema_version", "artifact_kind", "workflow", "source_identity",
        "canonical_configuration_sha256", "input_sha256", "assumptions",
        "holdout_files", "holdout_files_explicit", "mixed_role_files", "scenario_files",
    }
    expected_manifest_fields = common_manifest | (
        {"protocol_id", "report_source_identity"} if workflow == "historical_experiment" else set()
    )
    manifest = _object(manifest, expected_manifest_fields, "comparison manifest")
    if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["artifact_kind"] != "comparison_export_v1" or manifest["workflow"] != workflow):
        _fail("comparison manifest marker is inconsistent")
    if type(manifest["source_identity"]) is not str or _IDENTITY.fullmatch(manifest["source_identity"]) is None:
        _fail("comparison manifest source identity is invalid")
    digest = manifest["canonical_configuration_sha256"]
    if type(digest) is not str or digest != sha256(_canonical(configuration).encode()).hexdigest():
        _fail("canonical configuration checksum is inconsistent")
    expected_inputs = {name: sha256(files[name]).hexdigest() for name in input_names}
    _same(manifest["input_sha256"], expected_inputs, "manifest input checksums")
    _same(manifest["assumptions"], _ASSUMPTIONS, "manifest assumptions")
    if manifest["holdout_files_explicit"] is not True:
        _fail("manifest holdout declaration is not explicit")

    report = _json(files["report.json"], "report.json")
    if type(report) is not dict:
        _fail("comparison report must be an object")
    if workflow == "historical_experiment":
        scenarios, selected = _validate_experiment_report(report, configuration, loaded)
        fields = _EXPERIMENT_SCENARIO_FIELDS
        if manifest["protocol_id"] != report["protocol_id"]:
            _fail("manifest protocol identity is inconsistent")
        if manifest["source_identity"] != report["source_identity"] or manifest["report_source_identity"] != report["source_identity"]:
            _fail("manifest and report source identities are inconsistent")
        protocols = [report["protocol_id"]]
        source_identities = [report["source_identity"]]
    else:
        scenarios = _validate_legacy_report(report, configuration, loaded)
        selected = None
        fields = _LEGACY_SCENARIO_FIELDS
        identities = {row["source_identity"] for row in scenarios}
        if identities != {manifest["source_identity"]}:
            _fail("manifest and legacy source identities are inconsistent")
        protocols = []
        source_identities = [manifest["source_identity"]]
    if files["comparison.csv"] != _comparison_csv(report, fields):
        _fail("comparison.csv is not the exact report projection")
    _verify_summary(_json(files["summary.json"], "summary.json"), report, workflow)

    successful = [row for row in scenarios if row.get("status", "ok") == "ok"]
    mappings = _array(manifest["scenario_files"], "manifest scenario_files")
    if workflow == "moving_average_comparison" and mappings:
        _fail("legacy comparison must not contain scenario event files")
    if workflow == "historical_experiment" and len(mappings) != len(successful):
        _fail("scenario event mapping count is inconsistent")
    mapped_files: set[str] = set()
    windows = {window["name"]: window for window in report.get("windows", [])}
    mapping_pairs = zip(mappings, successful, strict=True) if workflow == "historical_experiment" else ()
    for index, (mapping, scenario) in enumerate(mapping_pairs):
        mapping = _object(mapping, ("index", "candidate", "window", "role", "regime", "cost_scenario", "files"),
                          f"scenario mapping {index}")
        expected_names = [f"runs/scenario-{index:06d}/{name}" for name in ("equity.csv", "fills.csv", "orders.csv")]
        expected_mapping = {key: scenario[key] for key in ("candidate", "window", "role", "regime", "cost_scenario")}
        if type(mapping["index"]) is not int or mapping["index"] != index or any(mapping[key] != value for key, value in expected_mapping.items()):
            _fail("scenario event mapping identity is inconsistent")
        if mapping["files"] != expected_names:
            _fail("scenario event mapping files are inconsistent")
        records = {}
        for name in expected_names:
            if name not in files or name in mapped_files:
                _fail("scenario event file is missing or overlaps another mapping")
            mapped_files.add(name)
            records[name.rsplit("/", 1)[-1]] = _validate_event_csv(files[name], name, scenario, windows[scenario["window"]])
        orders = {int(row[0]): row for row in records["orders.csv"]}
        symbol = next(candidate["symbol"] for candidate in configuration["candidates"]
                      if candidate["name"] == scenario["candidate"])
        if any(order[1] != symbol for order in orders.values()):
            _fail("order symbol differs from its candidate")
        for fill in records["fills.csv"]:
            order = orders.get(int(fill[1]))
            if order is None or order[1] != fill[2] or order[2] not in {"FILLED", "PARTIALLY_FILLED", "CANCELED"}:
                _fail("fill references an inconsistent order")
        for status, field in (("REJECTED", "rejected_order_count"), ("CANCELED", "canceled_order_count")):
            if sum(row[2] == status for row in orders.values()) != scenario[field]:
                _fail("order status count is inconsistent with the report")

    expected_names = required | set(input_names) | mapped_files
    if set(files) != expected_names:
        _fail("comparison artifact contains unknown or unmapped files")
    # The completion inventory already proves these names and bytes are the same
    # capture used by every check above.  Referencing it here makes the exact
    # inventory requirement explicit even when no event files exist.
    if set(checksums) != expected_names - {"complete.json"}:
        _fail("comparison completion inventory is inconsistent")

    test_inputs = {
        input_names[index] for index, window in enumerate(configuration["windows"])
        if window["role"] == "test"
    }
    test_events = {
        name for mapping in mappings if mapping["role"] == "test" for name in mapping["files"]
    }
    derived_holdout = test_inputs | test_events
    # The completion inventory contains hashes for every role, including final
    # test inputs/results. Protect it without rewriting legacy exporter manifests.
    mixed = set(_ROOT_FILES) | {"complete.json"}
    if manifest["holdout_files"] != [
        *[name for name in input_names if name in test_inputs],
        *[name for mapping in mappings if mapping["role"] == "test" for name in mapping["files"]],
    ]:
        _fail("manifest holdout file declaration is inconsistent")
    if manifest["mixed_role_files"] != [
        "comparison_artifact.json", "submitted.json", "configuration.json", "manifest.json",
        "report.json", "summary.json", "comparison.csv",
    ]:
        _fail("manifest mixed-role file declaration is inconsistent")

    evidence = "historical_declared" if all(
        window["evidence"]["kind"] == "historical" for window in configuration["windows"]
    ) else "synthetic"
    holdout_evaluation = "evaluated" if (
        workflow == "moving_average_comparison" or any(row.get("role") == "test" for row in scenarios)
    ) else "not_evaluated"
    return {
        "protected": derived_holdout,
        "mixed": mixed,
        "known": set(files),
        "protocol_ids": protocols,
        "source_identities": source_identities,
        "evidence": evidence,
        "selection": "selected" if selected is not None else "no_selection",
        "holdout_evaluation": holdout_evaluation,
    }
