"""Role-filtered projections of verified strategy-lab artifacts."""
from __future__ import annotations

import csv
from hashlib import sha256
import io
import math

from .artifact_io import ArtifactIOError, FILE_LIMIT, read_stable
from .artifacts import Artifact, ArtifactError, strict_json


TIMEFRAMES = {"hourly", "daily_24h"}
TABLES = {"candidates", "windows", "comparison", "macro", "regime_decisions"}
WINDOW_TABLES = {"comparison", "macro", "regime_decisions"}
SAFE_ROLES = {"train", "validation"}
TABLE_COLUMNS = {
    "candidates": ["candidate", "eligible", "validation_score", "exclusions",
                   "validation_mean_stressed_return", "benchmark"],
    "windows": ["name", "role", "regime", "interval_ns", "start_ns", "end_ns", "symbols",
                "currency", "bar_count", "gap_count", "dataset_hash", "source_id", "evidence"],
    "comparison": ["candidate", "base_return", "stress_return", "result", "base", "stress"],
    "macro": ["feature", "value", "reference_ns", "available_ns", "source", "vintage"],
    "regime_decisions": ["timestamp_ns", "macro_regime", "volatility_regime", "permitted_families"],
}
CORE_WARNINGS = [
    "Each research window starts independently in cash.",
    "Validation screening is not statistical significance or a profitability claim.",
    "Final test contents remain withheld.",
]


def _invalid(message: str = "Strategy-lab artifact is invalid.") -> ArtifactError:
    return ArtifactError("artifact_invalid", 422, message)


def _read(artifact: Artifact, name: str) -> bytes:
    if name not in artifact.known or name not in artifact.snapshots:
        raise _invalid()
    try:
        data = read_stable(artifact.root, name, FILE_LIMIT, artifact.snapshots[name])
    except ArtifactIOError as exc:
        raise ArtifactError("artifact_changed", 409, "Artifact changed while being read.") from exc
    expected = artifact.metadata.get("file_hashes", {}).get(name)
    if not isinstance(expected, str) or sha256(data).hexdigest() != expected:
        raise ArtifactError("artifact_changed", 409, "Artifact changed while being read.")
    return data


def _json(artifact: Artifact, name: str) -> dict:
    try:
        value = strict_json(_read(artifact, name))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _invalid() from exc
    if not isinstance(value, dict):
        raise _invalid()
    return value


def _csv(artifact: Artifact, name: str, expected: list[str]) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(_read(artifact, name).decode("utf-8")), strict=True)
        if reader.fieldnames != expected or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError
        rows = [dict(row) for row in reader]
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError
        return rows
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise _invalid() from exc


def _text(value, *, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise _invalid()
    return value


def _integer(value, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise _invalid()
    return value


def _number(value, *, nullable: bool = False):
    if nullable and value is None:
        return None
    try: valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError: valid = False
    if not valid:
        raise _invalid()
    return value


def _timestamp(value) -> int:
    value = _integer(value)
    if not -(2**63) <= value < 2**63: raise _invalid()
    return value


def _count(value) -> int:
    value = _integer(value, 0)
    if value >= 2**64: raise _invalid()
    return value


def _costs(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"commission_bps", "spread_bps", "slippage_bps"}:
        raise _invalid()
    return {key: _number(value[key]) for key in ("commission_bps", "spread_bps", "slippage_bps")}


def _load(artifact: Artifact, timeframe: str | None):
    if artifact.kind != "strategy_lab_v1" or artifact.metadata.get("integrity") != "verified":
        raise ArtifactError("lab_view_unavailable", 409, "Verified strategy-lab view unavailable.")
    configuration = _json(artifact, "configuration.json")
    timeframes = configuration.get("timeframes")
    if (not isinstance(timeframes, list) or not timeframes
            or any(not isinstance(value, str) for value in timeframes)
            or len(timeframes) != len(set(timeframes)) or any(value not in TIMEFRAMES for value in timeframes)):
        raise _invalid()
    selected_timeframe = timeframes[0] if timeframe is None else timeframe
    if selected_timeframe not in timeframes:
        raise ArtifactError("timeframe_not_found", 404, "Recorded timeframe unavailable.")
    report = _json(artifact, f"{selected_timeframe}/report.json")
    slate = _json(artifact, f"{selected_timeframe}/slate.json")
    return configuration, timeframes, selected_timeframe, report, slate


def _validated(artifact: Artifact, timeframe: str | None):
    configuration, timeframes, selected_timeframe, report, slate = _load(artifact, timeframe)
    slate_candidates = slate.get("candidates")
    report_candidates = report.get("candidates")
    assessments = report.get("assessments")
    windows = report.get("windows")
    scenarios = report.get("scenarios")
    if any(not isinstance(value, list) for value in
           (slate_candidates, report_candidates, assessments, windows, scenarios)):
        raise _invalid()
    slate_names = []
    benchmark = {}; slate_by_name = {}
    for candidate in slate_candidates:
        if not isinstance(candidate, dict) or type(candidate.get("eligible")) is not bool:
            raise _invalid()
        name = _text(candidate.get("name")); parameters = candidate.get("parameters")
        if not isinstance(parameters, dict): raise _invalid()
        slate_names.append(name); benchmark[name] = "benchmark" in parameters
        slate_by_name[name] = candidate
    if not slate_names or len(slate_names) != len(set(slate_names)):
        raise _invalid()
    report_names = [_text(value.get("name")) if isinstance(value, dict) else (_ for _ in ()).throw(_invalid())
                    for value in report_candidates]
    if report_names != slate_names:
        raise _invalid()
    for value in report_candidates:
        name = value["name"]
        if type(value.get("eligible")) is not bool or value["eligible"] != slate_by_name[name]["eligible"]:
            raise _invalid()
        try: parameters = strict_json(_text(value.get("parameters_json")).encode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc: raise _invalid() from exc
        if parameters != slate_by_name[name]["parameters"]: raise _invalid()
    assessment_map = {}
    for value in assessments:
        if not isinstance(value, dict) or type(value.get("eligible")) is not bool:
            raise _invalid()
        name = _text(value.get("candidate")); exclusions = value.get("exclusions")
        score = value.get("validation_score")
        if (name in assessment_map or name not in benchmark or not isinstance(exclusions, list)
                or any(not isinstance(item, str) for item in exclusions)
                or (score is not None and (not isinstance(score, list) or len(score) != 2))):
            raise _invalid()
        if score is not None: score = [_number(item) for item in score]
        if (value["eligible"] and (exclusions or score is None or benchmark[name]
                or not slate_by_name[name]["eligible"])):
            raise _invalid()
        if not slate_by_name[name]["eligible"] and value["eligible"]: raise _invalid()
        assessment_map[name] = {"candidate": name, "eligible": value["eligible"],
            "validation_score": score, "exclusions": exclusions,
            "validation_mean_stressed_return": _number(value.get("validation_mean_stressed_return"), nullable=True),
            "benchmark": benchmark[name]}
    if set(assessment_map) != set(slate_names): raise _invalid()

    window_map = {}; previous_end = None; previous_role = -1; signature = None
    role_order = {"train": 0, "validation": 1, "test": 2}
    safe_windows = []
    for value in windows:
        if not isinstance(value, dict): raise _invalid()
        name, role, regime = _text(value.get("name")), value.get("role"), _text(value.get("regime"))
        if not isinstance(role, str) or role not in role_order or name in window_map: raise _invalid()
        start, end, interval = _timestamp(value.get("start_ns")), _timestamp(value.get("end_ns")), _integer(value.get("interval_ns"), 1)
        if interval >= 2**63: raise _invalid()
        symbols, currency = value.get("symbols"), _text(value.get("currency"))
        if (start >= end or not isinstance(symbols, list) or not symbols or
                any(not isinstance(item, str) or not item for item in symbols)):
            raise _invalid()
        current_signature = (tuple(symbols), currency, interval)
        if signature is None: signature = current_signature
        if current_signature != signature or (previous_end is not None and start < previous_end) or role_order[role] < previous_role:
            raise _invalid()
        previous_end, previous_role = end, role_order[role]
        evidence = value.get("evidence")
        if (not isinstance(evidence, dict) or not isinstance(evidence.get("kind"), str)
                or evidence.get("kind") not in {"synthetic", "historical"}):
            raise _invalid()
        record = {"name": name, "role": role, "regime": regime, "interval_ns": interval,
            "start_ns": start, "end_ns": end, "symbols": symbols, "currency": currency,
            "bar_count": _count(value.get("bar_count")), "gap_count": _count(value.get("gap_count")),
            "dataset_hash": _text(value.get("dataset_hash")), "source_id": _text(value.get("source_id")),
            "evidence": {key: _text(evidence.get(key)) for key in
                ("kind", "source", "source_sha256", "availability_policy", "corporate_action_policy")}}
        window_map[name] = record
        if role in SAFE_ROLES: safe_windows.append({key: record[key] for key in ("name", "role", "regime")})
    if not window_map or {value["role"] for value in window_map.values()} != {"train", "validation", "test"}:
        raise _invalid()
    synthetic = any(value["evidence"]["kind"] == "synthetic" for value in window_map.values())
    if synthetic and any(value["eligible"] for value in assessment_map.values()):
        raise _invalid("Synthetic evidence cannot promote a candidate.")

    scenario_map = {}
    for value in scenarios:
        if not isinstance(value, dict): raise _invalid()
        candidate, window, cost = value.get("candidate"), value.get("window"), value.get("cost_scenario")
        if not all(isinstance(item, str) for item in (candidate, window, cost)):
            raise _invalid()
        key = (candidate, window, cost)
        if (candidate not in benchmark or window not in window_map or cost not in {"base", "stress"}
                or key in scenario_map or value.get("role") != window_map[window]["role"]
                or value.get("regime") != window_map[window]["regime"]):
            raise _invalid()
        status = value.get("status")
        if not isinstance(status, str) or status not in {"ok", "error"}: raise _invalid()
        error = value.get("error")
        if error is not None and not isinstance(error, str): raise _invalid()
        scenario_map[key] = {"status": status, "error": error,
            "total_return": _number(value.get("total_return"), nullable=True),
            "maximum_drawdown": _number(value.get("maximum_drawdown"), nullable=True),
            "average_gross_exposure": _number(value.get("average_gross_exposure"), nullable=True),
            "turnover": _number(value.get("turnover"), nullable=True),
            "final_equity": _number(value.get("final_equity"), nullable=True),
            **{field: _count(value.get(field)) for field in
               ("trade_count", "open_trade_count", "order_count", "fill_count",
                "rejected_order_count", "canceled_order_count")}}
    selected = report.get("selected_candidate")
    if (selected is not None and (not isinstance(selected, str) or selected not in benchmark
            or benchmark[selected] or not assessment_map[selected]["eligible"])):
        raise _invalid()
    if selected is not None and synthetic:
        raise _invalid("Synthetic evidence cannot promote a candidate.")
    for field in ("protocol_id", "source_identity"):
        _text(report.get(field))
    try: recorded_config = strict_json(_text(report.get("config_json")).encode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc: raise _invalid() from exc
    if not isinstance(recorded_config, dict): raise _invalid()
    context = {"currency": signature[1], "interval_ns": signature[2],
               "initial_cash": _number(recorded_config.get("initial_cash")),
               "base_costs": _costs(recorded_config.get("base_costs")),
               "stressed_costs": _costs(recorded_config.get("stressed_costs"))}
    return {"configuration": configuration, "timeframes": timeframes, "timeframe": selected_timeframe,
            "report": report, "slate_names": slate_names, "assessments": assessment_map,
            "windows": window_map, "safe_windows": safe_windows, "scenarios": scenario_map,
            "context": context}


def _warnings(model) -> list[str]:
    synthetic = any(value["evidence"]["kind"] == "synthetic" for value in model["windows"].values())
    prefix = (["Synthetic prices and scripted macro inputs are mechanics-only evidence.",
               "Dealer positioning and trading-flow estimates are not recorded."] if synthetic else [])
    return prefix + CORE_WARNINGS


def _selected_window(model, name: str | None):
    if name is None:
        raise ArtifactError("window_required", 400, "A safe research window is required.")
    value = model["windows"].get(name)
    if value is None or value["role"] not in SAFE_ROLES:
        raise ArtifactError("holdout_protected", 403, "Structured window access is withheld.")
    return value


def _comparison(artifact: Artifact, model, window) -> tuple[list[dict], list[str]]:
    expected = ["candidate", "window", "role", "regime", "cost_scenario", "status", "total_return",
                "maximum_drawdown", "trade_count", "open_trade_count", "fill_count", "rejected_order_count", "error"]
    recorded = _csv(artifact, f"{model['timeframe']}/comparison.csv", expected)
    csv_keys = set()
    for row in recorded:
        key = (row["candidate"], row["window"], row["cost_scenario"])
        if key in csv_keys or key not in model["scenarios"]: raise _invalid()
        scenario = model["scenarios"][key]
        if row["role"] != model["windows"][key[1]]["role"] or row["regime"] != model["windows"][key[1]]["regime"]:
            raise _invalid()
        comparisons = {"status": scenario["status"], "error": scenario["error"] or "",
                       "total_return": "" if scenario["total_return"] is None else str(scenario["total_return"]),
                       "maximum_drawdown": "" if scenario["maximum_drawdown"] is None else str(scenario["maximum_drawdown"]),
                       **{field: str(scenario[field]) for field in
                          ("trade_count", "open_trade_count", "fill_count", "rejected_order_count")}}
        if any(row[field] != value for field, value in comparisons.items()): raise _invalid()
        csv_keys.add(key)
    if csv_keys != set(model["scenarios"]): raise _invalid()
    rows = []
    for candidate in model["slate_names"]:
        values = {}
        for cost in ("base", "stress"):
            values[cost] = model["scenarios"].get((candidate, window["name"], cost),
                 {"status": "missing", "error": None, "total_return": None, "maximum_drawdown": None,
                 "average_gross_exposure": None, "turnover": None, "final_equity": None,
                 "trade_count": None, "open_trade_count": None, "order_count": None, "fill_count": None,
                 "rejected_order_count": None, "canceled_order_count": None})
        result = "missing" if any(value["status"] == "missing" for value in values.values()) else (
                 "failed" if any(value["status"] != "ok" for value in values.values()) else "ok")
        rows.append({"candidate": candidate, "base_return": values["base"]["total_return"],
                     "stress_return": values["stress"]["total_return"], "result": result,
                     "base": values["base"], "stress": values["stress"]})
    return rows, []


def _macro(artifact: Artifact, model, window) -> tuple[list[dict], list[str]]:
    payload = _json(artifact, f"{model['timeframe']}/macro.json")
    observations = payload.get("observations")
    if not isinstance(observations, list): raise _invalid()
    rows, omitted = [], False
    for value in observations:
        if not isinstance(value, dict): raise _invalid()
        reference, available = _timestamp(value.get("reference_ns")), _timestamp(value.get("available_ns"))
        if reference > available: raise _invalid()
        record = {"feature": _text(value.get("feature")), "value": _number(value.get("value")),
                  "reference_ns": reference, "available_ns": available,
                  "source": _text(value.get("source_id")), "vintage": _text(value.get("vintage_id"))}
        if window["start_ns"] < reference <= window["end_ns"] and window["start_ns"] < available <= window["end_ns"]:
            rows.append(record)
        else: omitted = True
    return rows, (["Macro observations outside the selected safe window were omitted."] if omitted else [])


def _decisions(artifact: Artifact, model, window) -> tuple[list[dict], list[str]]:
    expected = ["timestamp_ns", "macro_regime", "volatility_regime", "permitted_families"]
    rows = []
    for value in _csv(artifact, f"{model['timeframe']}/regime-decisions.csv", expected):
        try: timestamp = int(value["timestamp_ns"])
        except ValueError as exc: raise _invalid() from exc
        if str(timestamp) != value["timestamp_ns"] or not -(2**63) <= timestamp < 2**63: raise _invalid()
        if window["start_ns"] < timestamp <= window["end_ns"]:
            rows.append({"timestamp_ns": timestamp, "macro_regime": value["macro_regime"],
                         "volatility_regime": value["volatility_regime"],
                         "permitted_families": [item for item in value["permitted_families"].split(",") if item]})
    return rows, []


def project_lab(artifact: Artifact, timeframe: str | None = None, table: str | None = None,
                window: str | None = None, offset: int = 0, limit: int = 25) -> dict:
    """Return a safe detail or paginated table projection for one verified lab."""
    if ((timeframe is not None and not isinstance(timeframe, str))
            or (table is not None and not isinstance(table, str))
            or (window is not None and not isinstance(window, str))):
        raise ArtifactError("invalid_query", 400, "Invalid lab query.")
    if table is not None and table not in TABLES:
        raise ArtifactError("not_recorded", 404, "Lab table is not recorded.")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ArtifactError("invalid_table_query", 400, "Invalid table query.")
    model = _validated(artifact, timeframe)
    if table is None:
        if window is not None: raise ArtifactError("invalid_query", 400, "Detail view does not accept a window.")
        status = model["report"].get("holdout_status")
        if not isinstance(status, str): raise _invalid()
        return {"timeframe": model["timeframe"], "timeframes": model["timeframes"],
                "protocol_id": model["report"]["protocol_id"],
                "source_identity": model["report"]["source_identity"],
                "selection": {"selected_candidate": model["report"].get("selected_candidate"),
                              "assessment_source": "recorded_validation"},
                "holdout": {"evaluation": "not_evaluated" if status == "not_run_no_selection" else "evaluated",
                            "structured_access": "withheld"},
                "context": model["context"], "windows": model["safe_windows"], "warnings": _warnings(model)}
    if table in WINDOW_TABLES:
        selected_window = _selected_window(model, window)
    elif window is not None:
        raise ArtifactError("invalid_query", 400, "This table does not accept a window.")
    if table == "candidates":
        rows, warnings = ([model["assessments"][name] for name in model["slate_names"]], [])
    elif table == "windows":
        rows, warnings = ([model["windows"][value["name"]] for value in model["safe_windows"]], [])
    elif table == "comparison": rows, warnings = _comparison(artifact, model, selected_window)
    elif table == "macro": rows, warnings = _macro(artifact, model, selected_window)
    else: rows, warnings = _decisions(artifact, model, selected_window)
    return {"timeframe": model["timeframe"], "table": table, "columns": TABLE_COLUMNS[table],
            "rows": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit,
            "warnings": warnings}
