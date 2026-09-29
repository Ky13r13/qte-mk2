from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.run_views import RunViews


def _single(repo: Path, name="run", sampled=False, equity: bytes | None = None):
    root = repo / "build" / name; root.mkdir(parents=True)
    interval = 10
    manifest = {"configuration": {"data": {"interval_ns": interval}}, "dataset_end_ns": 9_007_199_254_741_020,
        "dataset_hash": "fixture", "dataset_hash_algorithm": "fixture", "dataset_start_ns": 9_007_199_254_740_990,
        "execution_model": "open_only_v1", "normalized_engine_config": "fixture", "research_label": "fixture",
        "sampling": {"interval_ns": 20, "policy": "last_event_at_or_before_v1"} if sampled else None,
        "schema_version": 1, "source": {"provider": "csv", "schema_id": "fixture", "assumptions": ["raw"]},
        "source_id": "fixture", "source_identity": "sha256:fixture", "strategy": {}}
    report = {"fill_count": 1, "open_trade_count": 0, "order_count": 1, "trade_count": 0,
        "metrics": {"total_return": {"value": 0.01, "undefined_reason": ""},
                    "sharpe_ratio": {"value": None, "undefined_reason": "annualization was not requested"}},
        "returns": [0.0, 0.01]}
    equity = equity or (b"timestamp_ns,equity,gross_exposure\n"
        b"9007199254740993,100.0,0.0\n9007199254740993,90.0,0.2\n"
        b"9007199254741003,120.0,0.1\n9007199254741033,110.0,0.0\n")
    files = {"manifest.json": json.dumps(manifest).encode(), "report.json": json.dumps(report).encode(),
        "equity.csv": equity, "fills.csv": b"fill_id,order_id,symbol,side,quantity,timestamp_ns,price,commission\n1,2,SPY,BUY,3,9007199254740993,10.0,0.1\n",
        "orders.csv": b"order_id,symbol,status,detail\n2,SPY,FILLED,filled\n"}
    if sampled: files["sampled_equity.csv"] = b"timestamp_ns,equity,gross_exposure\n9007199254740993,100.0,0.0\n9007199254741033,110.0,0.0\n"
    for filename, data in files.items(): (root / filename).write_bytes(data)
    checks = {filename: hashlib.sha256(data).hexdigest() for filename, data in files.items()}
    (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checks}))
    catalog = Catalog(repo); detail = catalog.register(f"build/{name}")
    return catalog, detail


def test_detail_exposes_recorded_metrics_provenance_and_missing_capabilities(tmp_path):
    catalog, artifact = _single(tmp_path, sampled=True)
    detail = RunViews(catalog).detail(artifact["id"])
    assert detail["metrics"]["total_return"] == {"value": 0.01, "undefined_reason": "", "unit": "ratio",
        "recorded_sampling_policy": {"interval_ns": 20, "policy": "last_event_at_or_before_v1"}}
    assert detail["metrics"]["sharpe_ratio"]["undefined_reason"] == "annualization was not requested"
    assert detail["provenance"]["dataset_start_ns"] == 9_007_199_254_740_990
    assert detail["capabilities"]["order_events"] == "not_recorded"
    assert detail["recordings"] == {"event_equity": "recorded", "sampled_equity": "recorded"}


def test_series_preserves_exact_time_duplicates_gaps_and_source_ordinals(tmp_path):
    catalog, artifact = _single(tmp_path)
    series = RunViews(catalog).series(artifact["id"], max_points=4)
    assert series["raw_count"] == 4 and series["display_count"] == 4
    assert series["points"][0]["timestamp_ns"] == 9_007_199_254_740_993
    assert series["points"][1]["timestamp_ns"] == series["points"][0]["timestamp_ns"]
    assert [point["row_ordinal"] for point in series["points"]] == [0, 1, 2, 3]
    assert series["points"][-1]["gap_before"] is True
    assert all(point["sequence"] is None for point in series["points"])


def test_sampled_series_and_string_tables(tmp_path):
    catalog, artifact = _single(tmp_path, sampled=True)
    view = RunViews(catalog)
    sampled = view.series(artifact["id"], sampling="sampled")
    assert sampled["sampling"] == "sampled" and sampled["gap_policy"] == "recorded_fixed_interval"
    fills = view.table(artifact["id"], "fills")
    assert fills["rows"][0]["fill_id"] == "1"
    assert fills["rows"][0]["timestamp_ns"] == "9007199254740993"
    assert fills["rows"][0]["quantity"] == "3"


def test_range_filter_and_query_validation(tmp_path):
    catalog, artifact = _single(tmp_path)
    view = RunViews(catalog)
    result = view.series(artifact["id"], start_ns=9_007_199_254_741_000, end_ns=9_007_199_254_741_010)
    assert [point["row_ordinal"] for point in result["points"]] == [2]
    with pytest.raises(ArtifactError): view.series(artifact["id"], max_points=True)
    with pytest.raises(ArtifactError): view.table(artifact["id"], "unknown")


def test_every_view_rechecks_catalog_hash_and_holdout_policy(tmp_path):
    equity = b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n"
    unknown = tmp_path / "build/unknown"; unknown.mkdir(parents=True); (unknown / "attachment.bin").write_bytes(equity)
    Catalog(tmp_path).register("build/unknown")
    catalog, artifact = _single(tmp_path, "copied", equity=equity)
    view = RunViews(catalog)
    with pytest.raises(ArtifactError) as protected: view.series(artifact["id"])
    assert protected.value.code == "holdout_protected"

    other_catalog, other = _single(tmp_path, "tampered")
    (tmp_path / "build/tampered/equity.csv").write_bytes(b"timestamp_ns,equity,gross_exposure\n1,9.0,0.0\n")
    with pytest.raises(ArtifactError): RunViews(other_catalog).series(other["id"])


def _rehash(root):
    checks = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in root.iterdir() if path.name != "complete.json"}
    (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checks}))


def test_views_reject_a_valid_generation_swap_between_reads(tmp_path, monkeypatch):
    catalog, artifact = _single(tmp_path)
    root = tmp_path / "build/run"
    read_file = catalog.read_file
    swapped = False

    def swapping_read(*args):
        nonlocal swapped
        value = read_file(*args)
        if not swapped:
            swapped = True
            path = root / "manifest.json"
            changed = json.loads(path.read_text())
            changed["dataset_hash"] = "replacement-generation"
            path.write_text(json.dumps(changed))
            _rehash(root)
        return value

    monkeypatch.setattr(catalog, "read_file", swapping_read)
    with pytest.raises(ArtifactError) as failure:
        RunViews(catalog).detail(artifact["id"])
    assert failure.value.code == "artifact_changed"


def test_series_rejects_reversed_source_order_even_outside_selected_range(tmp_path):
    equity = b"timestamp_ns,equity,gross_exposure\n10,100,0\n9,101,0\n20,102,0\n"
    catalog, artifact = _single(tmp_path, equity=equity)
    with pytest.raises(ArtifactError) as failure:
        RunViews(catalog).series(artifact["id"], start_ns=20)
    assert failure.value.code == "artifact_invalid"


@pytest.mark.parametrize("field,value", [("data", None), ("sampling", [])])
def test_malformed_optional_contract_is_sanitized(tmp_path, field, value):
    catalog, artifact = _single(tmp_path)
    root = tmp_path / "build/run"
    manifest = json.loads((root / "manifest.json").read_text())
    if field == "data": manifest["configuration"]["data"] = value
    else: manifest["sampling"] = value
    (root / "manifest.json").write_text(json.dumps(manifest))
    if field == "sampling":
        (root / "sampled_equity.csv").write_bytes((root / "equity.csv").read_bytes())
    _rehash(root)
    with pytest.raises(ArtifactError) as failure:
        RunViews(catalog).detail(artifact["id"])
    assert failure.value.code == "artifact_invalid"
