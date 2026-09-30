"""Actual lab HTTP projections must not disclose held-out result contents."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shutil

import httpx
import pytest

from qte.gui import create_app
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.strategy_lab import run_lab

BASE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def lab_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("lab-api-source")
    config = root / "input.json"
    config.write_text(json.dumps({"schema_version": 1, "seed": 817,
        "bars_per_window": 128, "timeframes": ["hourly"], "regimes": ["low_vol_trend"]}))
    return run_lab(config, root / "output")


@pytest.fixture
def lab_repository(tmp_path, lab_source):
    output = tmp_path / "build/lab"
    shutil.copytree(lab_source, output)
    return tmp_path, output


def report(output):
    return json.loads((output / "hourly/report.json").read_text())


def rehash(output):
    checksums = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in output.rglob("*") if path.is_file() and path.name != "complete.json"}
    (output / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checksums}))


def visit(repository, callback):
    async def scenario():
        codes = []
        app = create_app(repository, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                login = await client.post("/api/v1/session", headers={"Origin": BASE},
                                          json={"schema_version": 1, "code": codes[0]})
                assert login.status_code == 200
                headers = {"Origin": BASE, "X-QTE-Token": login.json()["request_token"]}
                registered = await client.post("/api/v1/catalog/register", headers=headers,
                    json={"schema_version": 1, "path": "build/lab"})
                assert registered.status_code == 200, registered.text
                await callback(client, headers, registered.json()["artifact"]["id"])
    asyncio.run(scenario())


def test_real_lab_http_preserves_slate_safe_windows_and_cost_context(lab_repository):
    repository, output = lab_repository
    source = report(output)
    expected = [item["name"] for item in source["candidates"]]

    async def check(client, headers, item_id):
        base = f"/api/v1/experiments/{item_id}"
        response = await client.get(base, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        detail = response.json()["experiment"]
        assert detail["kind"] == "strategy_lab_v1"
        assert detail["timeframe"] == "hourly" and detail["timeframes"] == ["hourly"]
        assert detail["selection"]["selected_candidate"] is None
        assert detail["holdout"]["structured_access"] == "withheld"
        assert {w["role"] for w in detail["windows"]} == {"train", "validation"}
        config = json.loads(source["config_json"])
        assert detail["context"]["base_costs"] == config["base_costs"]
        assert detail["context"]["stressed_costs"] == config["stressed_costs"]

        rows = []
        for offset in (0, 7, 14):
            page = await client.get(f"{base}/tables/candidates?offset={offset}&limit=7", headers=headers)
            assert page.status_code == 200, page.text
            value = page.json()
            assert value["total"] == 15 and value["offset"] == offset
            rows.extend(value["rows"])
        assert [row["candidate"] for row in rows] == expected
        assert all(row["eligible"] is False for row in rows)
        assert {row["candidate"] for row in rows if row["benchmark"]} == {"cash", "buy-and-hold"}

        window_response = await client.get(f"{base}/tables/windows", headers=headers)
        assert window_response.status_code == 200, window_response.text
        windows = window_response.json()["rows"]
        assert {w["role"] for w in windows} == {"train", "validation"}
        for window in windows:
            original = next(w for w in source["windows"] if w["name"] == window["name"])
            assert window["start_ns"] == str(original["start_ns"])
            assert window["end_ns"] == str(original["end_ns"])
            for table in ("comparison", "macro", "regime_decisions"):
                page = await client.get(f"{base}/tables/{table}", headers=headers,
                    params={"window": window["name"], "limit": "200"})
                assert page.status_code == 200, page.text
                payload = page.json()
                assert "test-low_vol_trend" not in page.text
                if table == "comparison":
                    assert [row["candidate"] for row in payload["rows"]] == expected
                    for row in payload["rows"]:
                        for cost in ("base", "stress"):
                            recorded = next(s for s in source["scenarios"] if s["window"] == window["name"]
                                            and s["candidate"] == row["candidate"] and s["cost_scenario"] == cost)
                            assert row[f"{cost}_return"] == recorded["total_return"]
                            assert row[cost]["status"] == recorded["status"]
                elif table == "macro":
                    assert payload["rows"]
                    assert all(int(window["start_ns"]) <= int(r["reference_ns"]) <= int(r["available_ns"]) <= int(window["end_ns"])
                               for r in payload["rows"])
                else:
                    assert payload["rows"]
                    assert all(int(window["start_ns"]) < int(r["timestamp_ns"]) <= int(window["end_ns"])
                               for r in payload["rows"])
        assert not list((repository / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))

    visit(repository, check)


def test_lab_projection_rejects_unauth_ambiguous_queries_and_protected_windows(lab_repository):
    repository, _ = lab_repository

    async def check(client, headers, item_id):
        base = f"/api/v1/experiments/{item_id}"
        assert (await client.get(base)).status_code == 403
        assert (await client.get(f"{base}/tables/candidates")).status_code == 403
        for suffix in ("?timeframe=hourly&timeframe=hourly", "?timeframe=../../private", "?role=test",
                       "/tables/candidates?limit=201", "/tables/candidates?window=train-low_vol_trend",
                       "/tables/comparison?window=x&window=y", "/tables/macro?limit=true"):
            assert (await client.get(base + suffix, headers=headers)).status_code == 400, suffix
        for table in ("comparison", "macro", "regime_decisions"):
            assert (await client.get(f"{base}/tables/{table}", headers=headers)).status_code == 400
            for window in ("test-low_vol_trend", "nonexistent-window"):
                denied = await client.get(f"{base}/tables/{table}", headers=headers, params={"window": window})
                assert denied.status_code == 403, denied.text
                assert denied.json()["error"]["code"] == "holdout_protected"

    visit(repository, check)


def test_safe_views_do_not_unlock_raw_mixed_bytes_even_after_projection(lab_repository):
    repository, _ = lab_repository

    async def check(client, headers, item_id):
        assert (await client.get(f"/api/v1/experiments/{item_id}", headers=headers)).status_code == 200
        files = await client.get(f"/api/v1/artifacts/{item_id}/files?limit=200", headers=headers)
        mixed = next(row for row in files.json()["items"] if row["name"] == "hourly/report.json")
        assert mixed["available"] is False
        url = f"/api/v1/artifacts/{item_id}/files/{mixed['id']}/download"
        assert (await client.get(url, headers=headers)).status_code == 403
        reveal = await client.post(f"/api/v1/artifacts/{item_id}/reveal-holdout", headers=headers,
            json={"schema_version": 1, "confirmation": "reveal outside protocol"})
        assert reveal.status_code == 200
        assert (await client.get(url, headers=headers)).status_code == 200
        denied = await client.get(f"/api/v1/experiments/{item_id}/tables/comparison", headers=headers,
                                 params={"window": "test-low_vol_trend"})
        assert denied.status_code == 403

    visit(repository, check)


def test_mixed_summary_failure_does_not_leak_through_catalog_status(lab_repository):
    repository, output = lab_repository
    path = output / "summary.json"
    summary = json.loads(path.read_text())
    summary["experiments"][0]["scenario_failures"] += 1
    path.write_text(json.dumps(summary)); rehash(output)
    item = Catalog(repository).register("build/lab")
    assert item["status"]["scenario_outcome"] == "unknown"


def test_lab_projection_rechecks_whole_generation_after_read(lab_repository, monkeypatch):
    repository, output = lab_repository
    import qte.gui.lab_views as views
    original = views.project_lab
    catalog = Catalog(repository); item = catalog.register("build/lab")

    def replacing(*args, **kwargs):
        value = original(*args, **kwargs)
        path = output / "hourly/report.json"
        payload = json.loads(path.read_text()); payload["source_identity"] = "replacement"
        path.write_text(json.dumps(payload)); rehash(output)
        return value

    monkeypatch.setattr(views, "project_lab", replacing)
    with pytest.raises(ArtifactError) as error:
        catalog.experiment_view(item["id"])
    assert error.value.code == "artifact_changed"


@pytest.mark.parametrize("mutation", ["candidate_object", "status_object", "role_object", "huge_number", "timestamp_range"])
def test_malformed_projection_fields_are_sanitized_http_errors(lab_repository, mutation):
    repository, output = lab_repository
    path = output / "hourly/report.json"
    value = json.loads(path.read_text())
    if mutation == "candidate_object": value["scenarios"][0]["candidate"] = {"SECRET": "bad"}
    elif mutation == "status_object": value["scenarios"][0]["status"] = {"SECRET": "bad"}
    elif mutation == "role_object": value["windows"][0]["role"] = ["SECRET"]
    elif mutation == "huge_number": value["scenarios"][0]["final_equity"] = 10**400
    else: value["windows"][0]["start_ns"] = 2**63
    path.write_text(json.dumps(value)); rehash(output)

    async def check(client, headers, item_id):
        response = await client.get(f"/api/v1/experiments/{item_id}", headers=headers)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "artifact_invalid"
        assert "SECRET" not in response.text and "Traceback" not in response.text

    visit(repository, check)
