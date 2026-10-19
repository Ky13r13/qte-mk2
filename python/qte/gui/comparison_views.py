"""Explicit safe-role projections of verified comparison exports; no execution."""
from __future__ import annotations

from hashlib import sha256

from .artifact_io import ArtifactIOError, FILE_LIMIT, read_stable
from .artifacts import Artifact, ArtifactError, strict_json


SAFE_ROLES = {"train", "validation"}
COLUMNS = {
    "candidates": ["candidate", "eligible", "validation_score", "exclusions",
                   "type", "symbol", "parameters", "configured_eligible",
                   "validation_mean_stressed_return"],
    "windows": ["name", "role", "regime", "interval_ns", "start_ns", "end_ns",
                "symbols", "currency", "bar_count", "gap_count", "dataset_hash",
                "source_id", "evidence"],
    "comparison": ["candidate", "base_return", "stress_return", "result", "base", "stress"],
}
METRICS = ("total_return", "maximum_drawdown", "average_gross_exposure", "turnover",
           "trade_count", "open_trade_count", "order_count", "fill_count",
           "rejected_order_count", "canceled_order_count", "final_equity")
EXCLUSIONS = {
    "scenario_failure", "insufficient_validation_windows", "insufficient_regime_coverage",
    "insufficient_closed_trades", "stressed_return_gate_failed", "worst_window_return_gate_failed",
    "drawdown_gate_failed", "benchmark_only", "unverified_factory_source",
}
WARNINGS = [
    "Each window starts independently in cash; returns are not a continuous portfolio.",
    "Recorded validation screening is not statistical significance or a profitability claim.",
    "Checksums do not authenticate historical data or independently reproduce results.",
    "Final test contents and outcomes remain withheld, including after disclosure.",
    "Promotion eligibility is withheld because it can depend on final-test provenance.",
]


def _json(artifact: Artifact, name: str) -> dict:
    try:
        data = read_stable(artifact.root, name, FILE_LIMIT, artifact.snapshots[name])
        if sha256(data).hexdigest() != artifact.metadata["file_hashes"][name]:
            raise ValueError
        value = strict_json(data)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ArtifactIOError, KeyError, ValueError, UnicodeDecodeError) as exc:
        raise ArtifactError("artifact_changed", 409, "Artifact changed while being read.") from exc


def _scenario(value: dict) -> dict:
    # Arbitrary exception strings/configuration blobs are not a safe projection.
    status = value.get("status", "ok")
    return {"status": status, "cost_scenario": value["cost_scenario"],
            "error": None if status == "ok" else "Scenario failed; raw details require disclosure.",
            **{key: value.get(key) for key in METRICS}}


def project_comparison(artifact: Artifact, table: str | None = None,
                       window: str | None = None, offset: int = 0, limit: int = 25) -> dict:
    """Consume only a verified export, allowlist fields, and never project test rows."""
    if ((table is not None and not isinstance(table, str)) or
            (window is not None and not isinstance(window, str))):
        raise ArtifactError("invalid_query", 400, "Invalid comparison query.")
    if table is not None and table not in COLUMNS:
        raise ArtifactError("not_recorded", 404, "Comparison table unavailable.")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ArtifactError("invalid_table_query", 400, "Invalid table query.")
    if table != "comparison" and window is not None:
        raise ArtifactError("invalid_query", 400, "This view does not accept a window.")
    if artifact.kind != "comparison_export_v1" or artifact.metadata.get("integrity") != "verified":
        raise ArtifactError("comparison_view_unavailable", 409, "Verified comparison view unavailable.")
    config, report = _json(artifact, "configuration.json"), _json(artifact, "report.json")
    workflow = config["workflow"]
    historical = workflow == "historical_experiment"
    safe = [row for row in config["windows"] if row["role"] in SAFE_ROLES]
    windows = [{key: row[key] for key in ("name", "role", "regime")} for row in safe]
    warnings = list(WARNINGS)
    if not historical:
        warnings.append("Legacy SMA records only in-sample baseline/stressed summaries; validation assessments and raw events are not recorded.")
    if table is None:
        return {"workflow": workflow, "tables": list(COLUMNS), "windows": windows,
                "candidate_count": len(config["candidates"]), "warnings": warnings}
    if table == "candidates":
        assessments = {row["candidate"]: row for row in report.get("assessments", [])}
        rows = []
        for candidate in config["candidates"]:
            assessment = assessments.get(candidate["name"], {})
            rows.append({"candidate": candidate["name"], "configured_eligible": candidate["eligible"],
                         **{key: candidate[key] for key in ("type", "symbol", "parameters")},
                         "eligible": None,
                         **{key: assessment.get(key) for key in
                            ("validation_score", "validation_mean_stressed_return")},
                         "exclusions": [value for value in assessment.get("exclusions", [])
                                        if value in EXCLUSIONS]})
    elif table == "windows":
        if historical:
            rows = [{key: row[key] for key in COLUMNS["windows"]} for row in report["windows"]
                    if row["role"] in SAFE_ROLES]
        else:
            # Legacy reports have no bar/gap counts; do not fabricate them.
            source = next(row for row in report["scenarios"] if row["sample"] == "in_sample")
            row = safe[0]
            rows = [{**windows[0], "interval_ns": row["data"]["interval_ns"],
                     "currency": row["data"]["currency"], "evidence": row["evidence"],
                     "symbols": None, "bar_count": None, "gap_count": None,
                     **{key: source[key] for key in ("start_ns", "end_ns", "dataset_hash", "source_id")}}]
    else:
        if window is None:
            raise ArtifactError("window_required", 400, "Select a training or validation window.")
        if window not in {row["name"] for row in safe}:
            raise ArtifactError("window_not_found", 404, "Recorded safe window unavailable.")
        if historical:
            selected = [row for row in report["scenarios"]
                        if row["role"] in SAFE_ROLES and row["window"] == window]
        else:
            selected = [row for row in report["scenarios"] if row["sample"] == "in_sample"]
        rows = []
        for candidate in config["candidates"]:
            scenarios = {row["cost_scenario"]: _scenario(row) for row in selected
                         if not historical or row["candidate"] == candidate["name"]}
            base = scenarios["base" if historical else "baseline_zero_cost"]
            stress = scenarios["stress" if historical else "stressed_costs"]
            rows.append({"candidate": candidate["name"], "base_return": base["total_return"],
                         "stress_return": stress["total_return"], "base": base, "stress": stress,
                         "result": "ok" if base["status"] == stress["status"] == "ok" else "failed"})
    return {"workflow": workflow, "table": table, "columns": COLUMNS[table],
            "rows": rows[offset:offset + limit], "total": len(rows), "offset": offset,
            "limit": limit, "warnings": warnings}
