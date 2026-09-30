from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from qte.gui.artifacts import ArtifactError, inspect_artifact
from qte.gui.lab_views import project_lab


ROOT = Path(__file__).parents[2]
ACTUAL = [ROOT / "build/strategy-lab-20260920", ROOT / "build/strategy-lab-20260922"]
COMPARISON_COLUMNS = ["candidate", "window", "role", "regime", "cost_scenario", "status",
    "total_return", "maximum_drawdown", "trade_count", "open_trade_count", "fill_count",
    "rejected_order_count", "error"]


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)


def _window(name, role, start, end, *, kind="historical"):
    return {"name": name, "role": role, "regime": f"{role}-regime", "start_ns": start,
        "end_ns": end, "interval_ns": 10, "symbols": ["SAFE"], "currency": "USD",
        "dataset_hash": f"hash-{name}", "source_id": f"source-{name}", "bar_count": 10,
        "gap_count": 0, "evidence": {"kind": kind, "source": f"evidence-{name}",
        "source_sha256": "a" * 64, "availability_policy": "recorded",
        "corporate_action_policy": "recorded"}}


def _scenario(candidate, window, role, regime, cost, *, status="ok", error=None, value=0.1):
    return {"candidate": candidate, "window": window, "role": role, "regime": regime,
        "cost_scenario": cost, "status": status, "error": error,
        "total_return": value if status == "ok" else None,
        "maximum_drawdown": 0.02 if status == "ok" else None,
        "average_gross_exposure": 0.5 if status == "ok" else None,
        "turnover": 0.25 if status == "ok" else None, "trade_count": 0,
        "open_trade_count": 0, "order_count": 0, "fill_count": 0,
        "rejected_order_count": 0, "canceled_order_count": 0,
        "final_equity": 100001.0 if status == "ok" else None,
        "normalized_config": None}


def _complete(root: Path) -> None:
    hashes = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in root.rglob("*") if path.is_file() and path.name != "complete.json"}
    _write_json(root / "complete.json", {"schema_version": 1, "sha256": hashes})


def _fixture(tmp_path: Path, *, missing=False, selected=True) -> Path:
    root = tmp_path / "build/lab"; folder = root / "hourly"
    folder.mkdir(parents=True)
    candidates = [{"name": "alpha", "parameters": {"kind": "safe"}, "eligible": True},
                  {"name": "cash", "parameters": {"benchmark": "cash"}, "eligible": False}]
    windows = [_window("train-safe", "train", 0, 100),
               _window("validation-safe", "validation", 110, 210),
               _window("test-SECRET-SENTINEL", "test", 220, 320)]
    scenarios = []
    for candidate in ("alpha", "cash"):
        for window in windows:
            for cost in ("base", "stress"):
                if missing and candidate == "alpha" and window["name"] == "validation-safe" and cost == "stress":
                    continue
                scenarios.append(_scenario(candidate, window["name"], window["role"], window["regime"], cost,
                    status="error" if candidate == "cash" and window["role"] == "validation" and cost == "stress" else "ok",
                    error="safe failure" if candidate == "cash" and window["role"] == "validation" and cost == "stress" else None))
    config = {"initial_cash": 100000, "base_costs": {"commission_bps": 1, "spread_bps": 2, "slippage_bps": 1},
              "stressed_costs": {"commission_bps": 5, "spread_bps": 10, "slippage_bps": 5}}
    report = {"protocol_id": "protocol-safe", "source_identity": "source-safe",
        "config_json": json.dumps(config), "candidates": [
            {"name": c["name"], "eligible": c["eligible"],
             "parameters_json": json.dumps(c["parameters"], sort_keys=True, separators=(",", ":"))}
            for c in candidates],
        "windows": windows, "scenarios": scenarios,
        "assessments": [
            {"candidate": "alpha", "eligible": True, "exclusions": [],
             "validation_score": [0.1, 0.2], "validation_mean_stressed_return": 0.2},
            {"candidate": "cash", "eligible": False, "exclusions": ["benchmark"],
             "validation_score": None, "validation_mean_stressed_return": None}],
        "selected_candidate": "alpha" if selected else None,
        "holdout_status": "completed_SECRET_SENTINEL" if selected else "not_run_no_selection",
        "holdout_reasons": ["SECRET_SENTINEL"], "limitations": [], "parameter_status": "frozen"}
    _write_json(root / "configuration.json", {"schema_version": 1, "timeframes": ["hourly"]})
    _write_json(folder / "slate.json", {"candidates": candidates, "warning": "safe"})
    _write_json(folder / "report.json", report)
    _write_json(folder / "generator.json", {"safe": True})
    _write_json(folder / "macro.json", {"config": {}, "dealer_observations": [], "evidence": "safe",
        "observations": [
            {"feature": "excluded-start", "value": 99, "reference_ns": 110, "available_ns": 111,
             "source_id": "source", "vintage_id": "SECRET-SENTINEL"},
            {"feature": "included", "value": 1.5, "reference_ns": 111, "available_ns": 210,
             "source_id": "source", "vintage_id": "vintage"},
            {"feature": "excluded-late", "value": 99, "reference_ns": 210, "available_ns": 211,
             "source_id": "source", "vintage_id": "SECRET-SENTINEL"}]})
    comparison = []
    for value in scenarios:
        comparison.append({key: ("" if value.get(key) is None else value.get(key)) for key in COMPARISON_COLUMNS})
    _write_csv(folder / "comparison.csv", COMPARISON_COLUMNS, comparison)
    _write_csv(folder / "regime-decisions.csv",
        ["timestamp_ns", "macro_regime", "volatility_regime", "permitted_families"], [
        {"timestamp_ns": 110, "macro_regime": "SECRET-SENTINEL", "volatility_regime": "x", "permitted_families": ""},
        {"timestamp_ns": 111, "macro_regime": "risk_on", "volatility_regime": "low", "permitted_families": "trend,range"},
        {"timestamp_ns": 210, "macro_regime": "risk_off", "volatility_regime": "high", "permitted_families": "range"},
        {"timestamp_ns": 211, "macro_regime": "SECRET-SENTINEL", "volatility_regime": "x", "permitted_families": ""}])
    _write_json(root / "summary.json", {"evidence": "historical_declared", "experiments": [{
        "timeframe": "hourly", "candidate_count": 2, "scenario_count": len(scenarios),
        "scenario_failures": sum(value["status"] != "ok" for value in scenarios),
        "selected_candidate": report["selected_candidate"], "holdout_status": report["holdout_status"]}]})
    _complete(root)
    return root


@pytest.mark.parametrize(("root", "count"), [(ACTUAL[0], 14), (ACTUAL[1], 15)])
def test_actual_legacy_labs_project_safe_recorded_views(root, count):
    artifact = inspect_artifact(root)
    detail = project_lab(artifact, "hourly")
    assert detail["timeframe"] == "hourly" and detail["timeframes"] == ["hourly", "daily_24h"]
    assert detail["context"]["currency"] == "USD"
    assert detail["context"]["interval_ns"] == 3_600_000_000_000
    assert {row["role"] for row in detail["windows"]} == {"train", "validation"}
    candidates = project_lab(artifact, "hourly", "candidates", offset=count - 1, limit=1)
    assert candidates["total"] == count and len(candidates["rows"]) == 1
    assert candidates["columns"][:4] == ["candidate", "eligible", "validation_score", "exclusions"]
    selected = detail["windows"][0]["name"]
    for table in ("comparison", "macro", "regime_decisions"):
        value = project_lab(artifact, "hourly", table, selected, 0, 2)
        assert value["timeframe"] == "hourly" and value["table"] == table and len(value["rows"]) <= 2
    assert "test-" not in json.dumps(detail)


def test_detail_and_tables_never_expose_test_sentinels_and_keep_missing_trials(tmp_path):
    artifact = inspect_artifact(_fixture(tmp_path, missing=True))
    detail = project_lab(artifact)
    assert detail["selection"] == {"selected_candidate": "alpha", "assessment_source": "recorded_validation"}
    assert detail["holdout"] == {"evaluation": "evaluated", "structured_access": "withheld"}
    candidates = project_lab(artifact, table="candidates")
    assert [row["candidate"] for row in candidates["rows"]] == ["alpha", "cash"]
    assert candidates["rows"][1]["benchmark"] is True
    comparison = project_lab(artifact, table="comparison", window="validation-safe")
    assert [row["candidate"] for row in comparison["rows"]] == ["alpha", "cash"]
    assert comparison["rows"][0]["result"] == "missing"
    assert comparison["rows"][0]["stress"]["status"] == "missing"
    assert comparison["rows"][0]["stress"]["trade_count"] is None
    assert comparison["rows"][1]["result"] == "failed" and comparison["rows"][1]["stress"]["error"] == "safe failure"
    for value in (detail, candidates, comparison,
                  project_lab(artifact, table="windows"),
                  project_lab(artifact, table="macro", window="validation-safe"),
                  project_lab(artifact, table="regime_decisions", window="validation-safe")):
        assert "SECRET-SENTINEL" not in json.dumps(value)


def test_macro_and_decision_boundaries_are_same_safe_window_only(tmp_path):
    artifact = inspect_artifact(_fixture(tmp_path))
    macro = project_lab(artifact, table="macro", window="validation-safe")
    assert [row["feature"] for row in macro["rows"]] == ["included"]
    assert macro["rows"][0]["reference_ns"] == 111 and macro["rows"][0]["available_ns"] == 210
    assert macro["warnings"] == ["Macro observations outside the selected safe window were omitted."]
    decisions = project_lab(artifact, table="regime_decisions", window="validation-safe")
    assert [row["timestamp_ns"] for row in decisions["rows"]] == [111, 210]
    assert decisions["rows"][0]["permitted_families"] == ["trend", "range"]


def test_window_queries_fail_closed_without_exposing_test_existence(tmp_path):
    artifact = inspect_artifact(_fixture(tmp_path))
    for table in ("comparison", "macro", "regime_decisions"):
        with pytest.raises(ArtifactError) as missing:
            project_lab(artifact, table=table)
        assert (missing.value.code, missing.value.status) == ("window_required", 400)
        for name in ("test-SECRET-SENTINEL", "does-not-exist"):
            with pytest.raises(ArtifactError) as protected:
                project_lab(artifact, table=table, window=name)
            assert (protected.value.code, protected.value.status, protected.value.message) == (
                "holdout_protected", 403, "Structured window access is withheld.")


@pytest.mark.parametrize("mutation", ["overlap", "role_mismatch", "duplicate_scenario", "bad_cost",
                                      "unhashable_candidate", "huge_number", "parameters_mismatch",
                                      "ineligible_selection"])
def test_ambiguous_window_and_scenario_joins_are_rejected(tmp_path, mutation):
    root = _fixture(tmp_path)
    report_path = root / "hourly/report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if mutation == "overlap": report["windows"][1]["start_ns"] = 99
    elif mutation == "role_mismatch": report["scenarios"][0]["role"] = "validation"
    elif mutation == "duplicate_scenario": report["scenarios"].append(dict(report["scenarios"][0]))
    elif mutation == "bad_cost": report["scenarios"][0]["cost_scenario"] = "mystery"
    elif mutation == "unhashable_candidate": report["scenarios"][0]["candidate"] = []
    elif mutation == "huge_number": report["scenarios"][0]["final_equity"] = 10**1000
    elif mutation == "parameters_mismatch": report["candidates"][0]["parameters_json"] = "{}"
    else: report["selected_candidate"] = "cash"
    _write_json(report_path, report); _complete(root)
    artifact = inspect_artifact(root)
    with pytest.raises(ArtifactError) as failure:
        project_lab(artifact)
    assert failure.value.code == "artifact_invalid"


def test_projection_rechecks_file_snapshot_and_hash(tmp_path):
    root = _fixture(tmp_path)
    artifact = inspect_artifact(root)
    report = root / "hourly/report.json"
    report.write_bytes(report.read_bytes() + b" ")
    with pytest.raises(ArtifactError) as failure:
        project_lab(artifact)
    assert failure.value.code == "artifact_changed"


def test_projector_requires_verified_strategy_lab_kind(tmp_path):
    root = tmp_path / "build/unknown"; root.mkdir(parents=True); (root / "note.txt").write_text("x")
    artifact = inspect_artifact(root)
    with pytest.raises(ArtifactError) as failure:
        project_lab(artifact)
    assert (failure.value.code, failure.value.status) == ("lab_view_unavailable", 409)


@pytest.mark.parametrize(("field", "value"), [("timeframe", []), ("table", {}), ("window", [])])
def test_public_projector_rejects_nonscalar_queries(tmp_path, field, value):
    artifact = inspect_artifact(_fixture(tmp_path))
    arguments = {field: value}
    with pytest.raises(ArtifactError) as failure:
        project_lab(artifact, **arguments)
    assert (failure.value.code, failure.value.status) == ("invalid_query", 400)


def test_synthetic_evidence_cannot_mark_an_assessment_eligible(tmp_path):
    root = _fixture(tmp_path, selected=False)
    report_path = root / "hourly/report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for window in report["windows"]: window["evidence"]["kind"] = "synthetic"
    _write_json(report_path, report); _complete(root)
    with pytest.raises(ArtifactError) as failure:
        project_lab(inspect_artifact(root))
    assert failure.value.code == "artifact_invalid"
