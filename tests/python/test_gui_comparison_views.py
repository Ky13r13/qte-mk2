"""Actual exported comparisons are projected without disclosing final tests."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json

import httpx
import pytest

from qte.gui import create_app
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.comparison_views import project_comparison
from test_gui_comparison_catalog import BASE, export, file_id


@pytest.mark.parametrize("example", ["experiment", "sma"])
def test_safe_projection_uses_recorded_results_not_execution(tmp_path, example, monkeypatch):
    root = export(tmp_path, example)
    report = json.loads((root / "report.json").read_text())
    config = json.loads((root / "configuration.json").read_text())
    import qte
    monkeypatch.setattr(qte.BacktestEngine, "run", lambda *a, **k: pytest.fail("view executed a strategy"))
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    detail = catalog.comparison_view(item["id"])
    assert detail["candidate_count"] == len(config["candidates"])
    assert detail["status"]["holdout_evaluation"] == "unknown"
    assert [row["role"] for row in detail["windows"]] == (["train", "validation"] if example == "experiment" else ["train"])
    rows = catalog.comparison_view(item["id"], "candidates", offset=0, limit=1)
    assert rows["total"] == len(config["candidates"]) and len(rows["rows"]) == 1
    assert rows["rows"][0]["parameters"] == config["candidates"][0]["parameters"]
    assert catalog.comparison_view(item["id"], "candidates", offset=999)["rows"] == []
    windows = catalog.comparison_view(item["id"], "windows")["rows"]
    outputs = [detail, rows, windows]
    for window in detail["windows"]:
        comparison = catalog.comparison_view(item["id"], "comparison", window["name"])
        assert comparison["total"] == len(config["candidates"])
        assert comparison["rows"][0]["base_return"] == report["scenarios"][0]["total_return"]
        assert comparison["rows"][0]["result"] == "ok"
        outputs.append(comparison)
    text = json.dumps(outputs)
    final = config["windows"][-1]
    assert final["evidence"]["source_sha256"] not in text
    assert "selected_candidate" not in text and "holdout_reasons" not in text
    if example == "sma":
        assert rows["rows"][0]["eligible"] is None
        assert windows[0]["bar_count"] is None
        assert "out_of_sample" not in text
    else:
        assert rows["rows"][0]["validation_score"] == report["assessments"][0]["validation_score"]
        assert str(report["windows"][-1]["start_ns"]) not in text
    with pytest.raises(ArtifactError) as blocked:
        catalog.read_file(item["id"], file_id("report.json"))
    assert blocked.value.code == "holdout_protected"
    assert not list(catalog.disclosures.root.glob("revealed_outside_protocol-*.json"))
    catalog.reveal(item["id"], "reveal outside protocol")
    assert catalog.comparison_view(item["id"], "windows")["rows"] == windows


@pytest.mark.parametrize(("table", "window", "offset", "limit", "code"), [
    ("comparison", None, 0, 25, "window_required"),
    ("comparison", "test", 0, 25, "window_not_found"),
    ("comparison", "absent", 0, 25, "window_not_found"),
    ("windows", "train", 0, 25, "invalid_query"),
    (None, "train", 0, 25, "invalid_query"),
    ("report", None, 0, 25, "not_recorded"),
    ("candidates", None, -1, 25, "invalid_table_query"),
    ("candidates", None, 0, 201, "invalid_table_query"),
    ("candidates", None, True, 25, "invalid_table_query"),
])
def test_projection_queries_are_bounded(tmp_path, table, window, offset, limit, code):
    export(tmp_path)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    with pytest.raises(ArtifactError) as denied:
        catalog.comparison_view(item["id"], table, window, offset, limit)
    assert denied.value.code == code


@pytest.mark.parametrize("late", [False, True])
def test_safe_projection_cannot_launder_known_protected_input(tmp_path, late):
    root = export(tmp_path)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    if late:
        assert catalog.comparison_view(item["id"], "comparison", "train")["rows"]
    protected = tmp_path / "build/protected"
    protected.mkdir()
    (protected / "raw.csv").write_bytes((root / "inputs/window-000.csv").read_bytes())
    catalog.register("build/protected")
    for table, window in [(None, None), ("candidates", None), ("windows", None), ("comparison", "train")]:
        with pytest.raises(ArtifactError) as denied:
            catalog.comparison_view(item["id"], table, window)
        assert denied.value.code == "holdout_protected"


def test_mutation_after_verification_cannot_reach_projector(tmp_path):
    root = export(tmp_path)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    captured = catalog.inspect(item["id"])
    (root / "report.json").write_text("{}")
    with pytest.raises(ArtifactError) as changed:
        project_comparison(captured)
    assert changed.value.code == "artifact_changed"
    with pytest.raises(ArtifactError) as invalid:
        catalog.comparison_view(item["id"])
    assert invalid.value.code == "comparison_view_unavailable"


@pytest.mark.parametrize("damage", ["corrupt_record", "symlink_record", "symlink_directory"])
def test_broken_disclosure_history_fails_closed(tmp_path, damage):
    root = export(tmp_path)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    identity = "file-sha256:" + sha256((root / "inputs/window-000.csv").read_bytes()).hexdigest()
    catalog.disclosures.mark_protected([identity])
    with pytest.raises(ArtifactError):
        catalog.comparison_view(item["id"])
    journal = catalog.disclosures
    record = journal.root / f"protected_content-{journal.key(identity)}.json"
    if damage == "corrupt_record":
        record.write_text("{}")
    elif damage == "symlink_record":
        record.unlink()
        record.symlink_to(tmp_path / "missing")
    else:
        original = journal.root.with_name("saved-disclosures")
        journal.root.rename(original)
        journal.root.symlink_to(original)
    with pytest.raises(ArtifactError) as denied:
        catalog.comparison_view(item["id"])
    assert denied.value.code == "disclosure_unavailable"


def test_candidate_projection_does_not_disclose_global_test_evidence_gates(tmp_path):
    from qte.comparison_export import run_comparison
    from test_gui_comparison_artifacts import _export, _round_trip_bars
    source = tmp_path / "source"
    source.mkdir()
    _export(source, bar_factory=_round_trip_bars,
            experiment={"required_regimes": ["high_vol"], "min_validation_windows": 1, "min_closed_trades": 1},
            holdout_regime="high_vol")
    config = json.loads((source / "comparison.json").read_text())
    # The first artifact has historical declarations in all windows. The
    # second changes ONLY final-test evidence; safe data and results match.
    first = tmp_path / "build/first"
    run_comparison(source / "comparison.json", first)
    config["windows"][-1]["evidence"]["kind"] = "synthetic"
    (source / "comparison.json").write_text(json.dumps(config))
    second = tmp_path / "build/second"
    run_comparison(source / "comparison.json", second)
    first_report = json.loads((first / "report.json").read_text())
    second_report = json.loads((second / "report.json").read_text())
    assert first_report["assessments"][0]["eligible"] is True
    assert second_report["assessments"][0]["eligible"] is False
    catalog = Catalog(tmp_path)
    ids = [catalog.register(f"build/{name}")["id"] for name in ("first", "second")]
    views = [catalog.comparison_view(value, "candidates") for value in ids]
    assert views[0]["rows"] == views[1]["rows"]
    assert views[0]["rows"][0]["eligible"] is None
    assert "synthetic_not_promotion_evidence" not in json.dumps(views)
    details = [catalog.comparison_view(value) for value in ids]
    assert details[0]["status"] == details[1]["status"]
    assert details[0]["status"]["evidence"] == "unknown"


def test_legacy_multisymbol_window_does_not_invent_single_symbol_universe(tmp_path):
    from test_gui_comparison_artifacts import _bars, _export
    def multi(day):
        raw = _bars(day).decode()
        lines = raw.splitlines()
        return (lines[0] + "\n" + "\n".join(
            row for value in lines[1:] for row in (value, value.replace("SPY,", "QQQ,"))) + "\n").encode()
    destination = tmp_path / "build/comparison"
    destination.mkdir(parents=True)
    _export(destination, workflow="moving_average_comparison", bar_factory=multi)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison/export")
    assert catalog.comparison_view(item["id"], "windows")["rows"][0]["symbols"] is None


def test_failure_is_retained_but_exception_payload_is_not_projected(tmp_path, monkeypatch):
    import qte
    original = qte.BacktestEngine.run
    calls = 0
    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("PRIVATE_EXCEPTION_PAYLOAD")
        return original(*args, **kwargs)
    monkeypatch.setattr(qte.BacktestEngine, "run", fail_once)
    export(tmp_path)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    result = catalog.comparison_view(item["id"], "comparison", "train")
    assert result["rows"][0]["result"] == "failed"
    assert result["rows"][0]["base_return"] is None
    assert result["rows"][0]["stress_return"] is not None
    assert "PRIVATE_EXCEPTION_PAYLOAD" not in json.dumps(result)


def test_authenticated_comparison_api_wire_types_and_queries(tmp_path):
    export(tmp_path)
    async def scenario():
        codes = []
        app = create_app(tmp_path, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                assert (await client.get("/api/v1/comparisons/unknown")).status_code == 401
                login = await client.post("/api/v1/session", headers={"Origin": BASE},
                    json={"schema_version": 1, "code": codes[0]})
                headers = {"X-QTE-Token": login.json()["request_token"], "Origin": BASE}
                registered = await client.post("/api/v1/catalog/register", headers=headers,
                    json={"schema_version": 1, "path": "build/comparison"})
                assert registered.status_code == 200, registered.text
                item_id = registered.json()["artifact"]["id"]
                url = f"/api/v1/comparisons/{item_id}"
                detail = await client.get(url, headers=headers)
                assert detail.status_code == 200, detail.text
                assert detail.json()["comparison"]["candidate_count"] == "2"
                assert detail.headers["cache-control"] == "no-store"
                rows = await client.get(url + "/tables/windows?offset=0&limit=1", headers=headers)
                assert rows.status_code == 200, rows.text
                assert rows.json()["total"] == 2 and rows.json()["limit"] == 1
                assert rows.json()["rows"][0]["start_ns"] == "1704204000000000000"
                for suffix, expected in [("?window=train", 400), ("/tables/comparison", 400),
                        ("/tables/comparison?window=test", 404), ("/tables/report", 404),
                        ("/tables/comparison?window=train&window=test", 400),
                        ("/tables/windows?limit=201", 400), ("/tables/windows?window=train", 400),
                        ("/tables/windows?x=1", 400)]:
                    response = await client.get(url + suffix, headers=headers)
                    assert response.status_code == expected, response.text
                ok = await client.get(url + "/tables/comparison?window=train", headers=headers)
                assert ok.status_code == 200, ok.text
                assert ok.json()["rows"][0]["base"]["trade_count"] == "1"
    asyncio.run(scenario())
