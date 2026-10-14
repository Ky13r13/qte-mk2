"""G6a data-only API, verified selection, provenance and disclosure regressions."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from qte.data_validation import write_dataset_output
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.dataset_views import dataset_view
from qte.gui.research_forms import config_origin
from test_gui_job_api import finished, visit

ROOT = Path(__file__).parents[2]
PREVIEW = "/api/v1/configs/validate"


@pytest.fixture
def repository(tmp_path):
    (tmp_path / "data").mkdir()
    shutil.copyfile(ROOT / "examples/research-bars.csv", tmp_path / "data/bars.csv")
    return tmp_path


def body():
    return {"schema_version": 1, "kind": "dataset_validate", "config_origin": config_origin(),
            "configuration": {"data": {"adapter": "csv", "path": "data/bars.csv",
                                        "interval_ns": "3600000000000", "action_free": True}}}


def export(repository):
    config = deepcopy(body()["configuration"])
    config["data"]["path"] = "bars.csv"
    config["data"]["interval_ns"] = 3600000000000
    path = repository / "data/validate.json"
    path.write_text(json.dumps(config))
    return write_dataset_output(path, repository / "build/validation")


def test_dataset_catalog_has_no_content_projection_until_authorized(repository):
    output = export(repository)
    catalog = Catalog(repository)
    # A protected copy must propagate to every summary/config file, not only raw.
    catalog.disclosures.mark_protected(["file-sha256:" + sha256((output / "input.csv").read_bytes()).hexdigest()])
    item = catalog.register("build/validation")
    assert item["kind"] == "dataset_validation_v1"
    assert item["status"]["integrity"] == "verified"
    assert item["needs_disclosure"]
    assert "symbols" not in json.dumps(item)
    assert "bar_count" not in json.dumps(item)
    assert all(not row["available"] for row in item["files"])
    with pytest.raises(ArtifactError, match="disclosure"):
        dataset_view(catalog, item["id"])
    catalog.reveal(item["id"], "reveal outside protocol")
    selected = dataset_view(catalog, item["id"])
    assert selected["source"]["state"] == "available"
    assert selected["source"]["data"]["expected_sha256"] == sha256((output / "input.csv").read_bytes()).hexdigest()
    assert selected["summary"]["availability"]["exchange_calendar_validation"] is False


def test_dataset_api_auth_no_page_load_work_and_protected_diagnostics(repository, monkeypatch):
    from qte.gui.jobs import JobManager
    monkeypatch.setattr(JobManager, "submit", lambda *args, **kwargs: pytest.fail("read/preview launched job"))
    output = export(repository)
    item = Catalog(repository).register("build/validation")

    async def check(client, headers):
        url = f"/api/v1/datasets/{item['id']}"
        assert (await client.get(url)).status_code == 403
        assert (await client.get(url + "?x=1", headers=headers)).status_code == 400
        response = await client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert isinstance(response.json()["dataset"]["summary"]["dataset_start_ns"], str)
        for route in ("/data", "/data/new", f"/data/{item['id']}"):
            assert (await client.get(route, headers={"sec-fetch-site": "none"})).status_code == 200
        assert (await client.get("/assets/datasets.js")).status_code == 200
        raw = b"SECRET_MALFORMED_DATA"
        (repository / "data/bars.csv").write_bytes(raw)
        Catalog(repository).disclosures.mark_protected(["file-sha256:" + sha256(raw).hexdigest()])
        denied = await client.post(PREVIEW, headers=headers, json=body())
        assert denied.status_code == 403
        assert "SECRET" not in denied.text
        assert not (repository / "build/gui/jobs").exists()
    visit(repository, check)


@pytest.mark.parametrize("change", ["changed", "deleted", "reexport"])
def test_real_dataset_job_selection_preserves_mapping_and_pins_source(repository, change):
    original = (repository / "data/bars.csv").read_text()
    (repository / "data/bars.csv").write_text(original.replace("symbol,", "ticker,", 1))
    payload = body()
    payload["configuration"]["data"]["schema"] = {"symbol": "ticker"}

    async def check(client, headers):
        preview = await client.post(PREVIEW, headers=headers, json=payload)
        assert preview.status_code == 200, preview.text
        submitted = await client.post("/api/v1/jobs", headers=headers, json={
            **payload, "preview_fingerprint": preview.json()["preview_fingerprint"],
            "idempotency_key": "dataset_" + change * 5, "timeout_seconds": 30})
        assert submitted.status_code == 200, submitted.text
        job = await finished(client, headers, submitted.json()["job"]["id"])
        assert job["state"] == "succeeded", job
        assert Catalog(repository).list()[1] == 0
        registered = await client.post("/api/v1/catalog/register", headers=headers,
                                      json={"schema_version": 1, "path": job["output_path"]})
        assert registered.status_code == 200, registered.text
        identifier = registered.json()["artifact"]["id"]
        url = f"/api/v1/datasets/{identifier}"
        view = await client.get(url, headers=headers)
        assert view.status_code == 200, view.text
        source = view.json()["dataset"]["source"]
        assert source["state"] == "available"
        assert source["data"]["schema"] == {"symbol": "ticker"}
        assert source["data"]["path"] == "data/bars.csv"
        research = {**body(), "kind": "research_run", "configuration": {
            "data": source["data"], "strategy": {"type": "moving_average_regime", "symbol": "SPY",
                "fast_period": "2", "slow_period": "3", "quantity": "1"},
            "engine": {"initial_cash": 100000.0}}}
        ready = await client.post(PREVIEW, headers=headers, json=research)
        assert ready.status_code == 200, ready.text
        # Check changed/deleted sources remain inspectable, but cannot be reused.
        path = repository / "data/bars.csv"
        if change == "changed": path.write_text(path.read_text() + "\n")
        elif change == "deleted": path.unlink()
        else:
            # Same raw bytes and self-consistent checksums cannot substitute a
            # different currency/configuration into a completed job's output.
            alternative = deepcopy(payload["configuration"])
            alternative["data"].update(path="bars.csv", interval_ns=3600000000000, currency="EUR")
            config_path = repository / "data/reexport.json"
            config_path.write_text(json.dumps(alternative))
            replacement = write_dataset_output(config_path, repository / "build/reexport")
            for name in replacement.iterdir():
                shutil.copyfile(name, repository / job["output_path"] / name.name)
        unavailable = await client.get(url, headers=headers)
        assert unavailable.status_code == 200
        assert unavailable.json()["dataset"]["source"]["state"] == "unavailable"
        if change == "reexport":
            assert unavailable.json()["dataset"]["summary"]["currency"] == "EUR"
            assert (await client.get("/api/v1/jobs", headers=headers)).json()["total"] == 1
            return
        assert (await client.post(PREVIEW, headers=headers, json=research)).status_code in (400, 403)
        rejected = await client.post("/api/v1/jobs", headers=headers, json={
            **research, "preview_fingerprint": ready.json()["preview_fingerprint"],
            "idempotency_key": "reuse_dataset_" + change * 5, "timeout_seconds": 30})
        assert rejected.status_code in (403, 409), rejected.text
        assert (await client.get("/api/v1/jobs", headers=headers)).json()["total"] == 1
    visit(repository, check)


def test_invalid_dataset_does_not_downgrade_to_lab(repository):
    output = export(repository)
    (output / "summary.json").write_text("{}")
    item = Catalog(repository).register("build/validation")
    assert item["kind"] == "dataset_validation_v1"
    assert item["status"]["integrity"] == "invalid"
    assert item["needs_disclosure"]


@pytest.mark.parametrize("data", [None, []])
def test_malformed_rehashed_dataset_is_invalid_not_an_uncaught_catalog_error(repository, data):
    output = export(repository)
    (output / "configuration.json").write_text(json.dumps({"data": data}))
    marker = json.loads((output / "complete.json").read_text())
    marker["sha256"]["configuration.json"] = sha256((output / "configuration.json").read_bytes()).hexdigest()
    (output / "complete.json").write_text(json.dumps(marker))
    catalog = Catalog(repository)
    item = catalog.register("build/validation")
    assert item["status"]["integrity"] == "invalid"
    assert catalog.list()[0][0]["status"]["integrity"] == "invalid"
    with pytest.raises(ArtifactError, match="verified dataset"):
        dataset_view(catalog, item["id"])
