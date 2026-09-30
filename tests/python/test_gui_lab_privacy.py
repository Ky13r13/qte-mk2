"""Content-leak regressions for the role-filtered lab projector."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from qte.gui.artifacts import ArtifactError, inspect_artifact
from qte.gui.catalog import Catalog
from qte.strategy_lab import run_lab


SENTINELS = ("HOLDOUT_REASON_SENTINEL", "HOLDOUT_MACRO_SOURCE_SENTINEL",
             "HOLDOUT_VINTAGE_SENTINEL", "HOLDOUT_REGIME_SENTINEL", "987654321.5")


def _rehash(root: Path) -> None:
    checks = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in root.rglob("*") if path.is_file() and path.name != "complete.json"}
    (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checks}), encoding="utf-8")


def _lab(tmp_path: Path) -> Path:
    config = tmp_path / "input.json"
    config.write_text(json.dumps({"schema_version": 1, "seed": 117,
        "bars_per_window": 128, "timeframes": ["hourly"], "regimes": ["low_vol_trend"]}), encoding="utf-8")
    return run_lab(config, tmp_path / "build" / "lab")


def _sentinel_lab(tmp_path: Path) -> tuple[Path, str]:
    root = _lab(tmp_path)
    report_path = root / "hourly" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    windows = {row["role"]: row for row in report["windows"]}
    report["holdout_reasons"] = ["HOLDOUT_REASON_SENTINEL"]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    macro_path = root / "hourly" / "macro.json"
    macro = json.loads(macro_path.read_text(encoding="utf-8"))
    test_window, train_window, validation_window = windows["test"], windows["train"], windows["validation"]
    test_row = next(row for row in macro["observations"]
                    if test_window["start_ns"] < row["reference_ns"] <= row["available_ns"] <= test_window["end_ns"])
    test_row.update({"value": 987654321.5, "source_id": "HOLDOUT_MACRO_SOURCE_SENTINEL",
                     "vintage_id": "HOLDOUT_VINTAGE_SENTINEL"})
    cross_boundary = next(row for row in macro["observations"]
                          if train_window["start_ns"] < row["reference_ns"] <= row["available_ns"] <= train_window["end_ns"])
    cross_boundary.update({"available_ns": validation_window["start_ns"], "source_id": "HOLDOUT_MACRO_SOURCE_SENTINEL"})
    macro_path.write_text(json.dumps(macro), encoding="utf-8")

    decisions_path = root / "hourly" / "regime-decisions.csv"
    with decisions_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream); assert reader.fieldnames is not None
        columns, decisions = list(reader.fieldnames), list(reader)
    test_decision = next(row for row in decisions if test_window["start_ns"] < int(row["timestamp_ns"]) <= test_window["end_ns"])
    test_decision["macro_regime"] = "HOLDOUT_REGIME_SENTINEL"
    with decisions_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader(); writer.writerows(decisions)
    _rehash(root)
    return root, train_window["name"]


def _serialized(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _assert_no_sentinels(value: object) -> None:
    serialized = _serialized(value)
    for sentinel in SENTINELS:
        assert sentinel not in serialized


def test_safe_lab_projection_omits_test_sentinels_on_every_safe_table_page(tmp_path):
    root, train_name = _sentinel_lab(tmp_path)
    catalog = Catalog(tmp_path)
    artifact = catalog.register("build/lab")
    detail = catalog.experiment_view(artifact["id"])
    _assert_no_sentinels(detail)
    assert {row["role"] for row in detail["windows"]} == {"train", "validation"}
    assert detail["holdout"] == {"evaluation": "not_evaluated", "structured_access": "withheld"}

    for table in ("candidates", "windows"):
        first = catalog.experiment_view(artifact["id"], table=table, limit=1)
        values = [first]
        for offset in range(1, first["total"]):
            values.append(catalog.experiment_view(artifact["id"], table=table, offset=offset, limit=1))
        for page in values: _assert_no_sentinels(page)

    for table in ("comparison", "macro", "regime_decisions"):
        first = catalog.experiment_view(artifact["id"], table=table, window=train_name, limit=1)
        values = [first]
        for offset in range(1, first["total"]):
            values.append(catalog.experiment_view(artifact["id"], table=table, window=train_name, offset=offset, limit=1))
        for page in values: _assert_no_sentinels(page)
        if table == "macro":
            assert "outside the selected safe window" in first["warnings"][0]

    raw_macro = json.loads((root / "hourly" / "macro.json").read_text())
    safe_macro = catalog.experiment_view(artifact["id"], table="macro", window=train_name, limit=200)
    assert safe_macro["total"] < len(raw_macro["observations"])
    assert not list((tmp_path / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))


def test_role_mismatched_scenario_is_rejected_not_projected_as_safe(tmp_path):
    root = _lab(tmp_path)
    report_path = root / "hourly" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    safe = next(row for row in report["scenarios"] if row["role"] == "train")
    safe["role"] = "test"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _rehash(root)

    catalog = Catalog(tmp_path)
    artifact = catalog.register("build/lab")
    with pytest.raises(ArtifactError) as failure:
        catalog.experiment_view(artifact["id"])
    assert failure.value.code == "artifact_invalid"


def test_safe_projection_does_not_make_mixed_raw_report_readable(tmp_path):
    root, _ = _sentinel_lab(tmp_path)
    catalog = Catalog(tmp_path)
    artifact = catalog.register("build/lab")
    catalog.experiment_view(artifact["id"])
    raw = next(row for row in catalog.get(artifact["id"])["files"] if row["name"] == "hourly/report.json")
    assert raw["available"] is False
    with pytest.raises(ArtifactError) as failure:
        catalog.read_file(artifact["id"], raw["id"])
    assert failure.value.code == "holdout_protected"
    assert not list((tmp_path / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))
