"""Offline acquisition UI: explicit permission, authenticated lineage, no GET work."""
from hashlib import sha256
import json

import pytest

from qte.download_artifact import write_download_output
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.download_views import download_view
from qte.gui.research_forms import config_origin
from test_adapters import ALPACA
from test_gui_job_api import visit


def configuration():
    return {"symbols": ["SPY"], "timeframe": "1Hour", "start": "2024-01-02T14:00:00Z",
            "end": "2024-01-02T17:00:00Z", "feed": "iex", "action_free": True}


def export(repository):
    config = repository / "request.json"
    config.write_text(json.dumps(configuration()))
    rows = json.loads(ALPACA.read_text())["bars"]["SPY"]
    return write_download_output(config, repository / "build/download", page_fetcher=lambda _: {
        "bars": {"SPY": rows}, "next_page_token": None})


def test_default_server_only_previews_and_gets_never_fetch(tmp_path, monkeypatch):
    import qte.adapters.alpaca_download as adapter
    monkeypatch.setattr(adapter, "fetch_page", lambda *a, **k: pytest.fail("unexpected provider access"))
    monkeypatch.setenv("APCA_API_KEY_ID", "SENTINEL_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "SENTINEL_SECRET")

    async def check(client, headers):
        url = "/api/v1/data/alpaca/prerequisites"
        assert (await client.get(url)).status_code == 403
        assert (await client.get(url + "?x=1", headers=headers)).status_code == 400
        response = await client.get(url, headers=headers)
        assert response.json() == {"schema_version": 1, "enabled": False, "credentials_present": True}
        assert response.headers["cache-control"] == "no-store"
        assert "SENTINEL" not in response.text
        assert (await client.get("/data/download/alpaca")).status_code == 200
        assert (await client.get("/api/v1/research/form-options", headers=headers)).status_code == 200
        payload = {"schema_version": 1, "kind": "download_alpaca", "config_origin": config_origin(),
                   "configuration": configuration()}
        preview = await client.post("/api/v1/configs/validate", headers=headers, json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["lineage"]["input_disclosure"] == "unknown"
        assert preview.json()["data_sha256"] is None
        launch = await client.post("/api/v1/jobs", headers=headers, json={**payload,
            "preview_fingerprint": preview.json()["preview_fingerprint"],
            "idempotency_key": "offline_download_request", "timeout_seconds": 30})
        assert launch.status_code == 403, launch.text
        assert "SENTINEL" not in launch.text
        assert not (tmp_path / "build/gui/jobs").exists()
        assert not (tmp_path / ".cache/qte-gui/jobs.lock").exists()
    visit(tmp_path, check)


def test_verified_cache_lineage_auth_and_copied_holdout_protection(tmp_path):
    output = export(tmp_path)
    bars = next(output.glob("cache/*/bars.json"))
    catalog = Catalog(tmp_path)
    catalog.disclosures.mark_protected(["file-sha256:" + sha256(bars.read_bytes()).hexdigest()])
    item = catalog.register("build/download")
    assert item["kind"] == "alpaca_download_v1"
    assert item["status"]["integrity"] == "verified"
    assert "SPY" not in json.dumps(item)
    assert all(not row["available"] for row in item["files"])
    with pytest.raises(ArtifactError, match="disclosure"):
        download_view(catalog, item["id"])

    async def check(client, headers):
        url = f"/api/v1/downloads/{item['id']}"
        assert (await client.get(url)).status_code == 403
        assert (await client.get(url, headers=headers)).status_code == 403
        catalog.reveal(item["id"], "reveal outside protocol")
        response = await client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        view = response.json()["download"]
        assert view["configuration"] == configuration()
        assert view["manifest"]["artifact_kind"] == "alpaca_download_v1"
        assert (await client.get(url + "?unexpected=1", headers=headers)).status_code == 400
        assert (await client.get(f"/data/downloads/{item['id']}")).status_code == 200
        bars.write_text("{}")
        assert (await client.get(url, headers=headers)).status_code == 422
    visit(tmp_path, check)


def test_corrupt_download_never_downgrades_to_lab(tmp_path):
    output = export(tmp_path)
    (output / "download_artifact.json").write_text("{}")
    item = Catalog(tmp_path).register("build/download")
    assert item["kind"] == "alpaca_download_v1"
    assert item["status"]["integrity"] == "invalid"
