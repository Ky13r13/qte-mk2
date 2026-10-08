"""Authenticated job boundary and real offline CLI integration."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil

import httpx
import pytest

from qte.cli import run_research
from qte.gui import create_app
from qte.gui.catalog import Catalog

BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).parents[2]


@pytest.fixture
def repository(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    for name in ("research-run.json", "research-bars.csv"):
        shutil.copyfile(ROOT / "examples" / name, examples / name)
    return tmp_path


def request_body(key="a" * 32):
    return {"schema_version": 1, "kind": "research_run", "config_path": "examples/research-run.json",
            "idempotency_key": key, "timeout_seconds": 30}


def visit(repository, check):
    async def scenario():
        codes = []
        app = create_app(repository, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                login = await client.post("/api/v1/session", headers={"Origin": BASE},
                                         json={"schema_version": 1, "code": codes[0]})
                assert login.status_code == 200
                headers = {"Origin": BASE, "X-QTE-Token": login.json()["request_token"]}
                await check(client, headers)
    asyncio.run(scenario())


async def finished(client, headers, job_id):
    for _ in range(1000):
        response = await client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["state"] not in {"queued", "running"}:
            return job
        await asyncio.sleep(0.02)
    pytest.fail("local research job did not reach a terminal state")


def test_http_job_deduplicates_preserves_cli_results_and_registers_only_explicitly(repository):
    direct = run_research(repository / "examples/research-run.json", repository / "build/direct", export_version=2)

    async def check(client, headers):
        initial = await client.get("/api/v1/jobs", headers=headers)
        assert initial.status_code == 200 and initial.json()["total"] == 0
        assert not (repository / "build/gui/jobs").exists()
        submitted = await client.post("/api/v1/jobs", headers=headers, json=request_body())
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["job"]["id"]
        repeat = await client.post("/api/v1/jobs", headers=headers, json=request_body())
        assert repeat.status_code == 200 and repeat.json()["job"]["id"] == job_id
        job = await finished(client, headers, job_id)
        assert job["state"] == "succeeded", job
        assert isinstance(job["created_ns"], str)
        assert job["output_path"] == f"build/gui/jobs/{job_id}/artifacts"
        output = repository / job["output_path"]
        for name in ("report.json", "fills.csv", "equity.csv", "order_events.csv", "positions.csv"):
            assert (output / name).read_bytes() == (direct / name).read_bytes(), name
        assert Catalog(repository).list()[1] == 0
        registered = await client.post("/api/v1/catalog/register", headers=headers,
            json={"schema_version": 1, "path": job["output_path"]})
        assert registered.status_code == 200
        assert registered.json()["artifact"]["status"]["integrity"] == "verified"
        log = await client.get(f"/api/v1/jobs/{job_id}/log?limit=12", headers=headers)
        assert log.status_code == 200
        assert len(log.json()["text"].encode()) <= 12
        assert log.headers["cache-control"] == "no-store"
        assert (await client.get("/api/v1/jobs?limit=1", headers=headers)).json()["total"] == 1
        conflict = await client.post("/api/v1/jobs", headers=headers, json={**request_body(), "timeout_seconds": 29})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"

    visit(repository, check)


def test_job_api_rejects_auth_queries_commands_and_malformed_fields_without_launch(repository):
    async def check(client, headers):
        for url in ("/api/v1/jobs", "/api/v1/jobs/" + "a" * 32, "/api/v1/jobs/" + "a" * 32 + "/log"):
            assert (await client.get(url)).status_code == 403
        assert (await client.post("/api/v1/jobs", json=request_body())).status_code == 403
        assert (await client.post("/api/v1/jobs", headers={**headers, "Origin": "https://outside.invalid"},
                                  json=request_body())).status_code == 403
        for body in ({**request_body(), "kind": "download_alpaca"}, {**request_body(), "command": "echo injected"},
                     {**request_body(), "timeout_seconds": True}, {**request_body(), "timeout_seconds": 1801},
                     {**request_body(), "idempotency_key": "short"}, {**request_body(), "config_path": []}):
            assert (await client.post("/api/v1/jobs", headers=headers, json=body)).status_code == 400
        for query in ("?limit=201", "?offset=0&offset=1", "?command=echo"):
            assert (await client.get("/api/v1/jobs" + query, headers=headers)).status_code == 400
        assert (await client.post("/api/v1/jobs?x=1", headers=headers, json=request_body())).status_code == 400
        for suffix in ("?limit=65537", "?offset=-1", "?limit=1&limit=2", "?path=private"):
            assert (await client.get("/api/v1/jobs/" + "a" * 32 + "/log" + suffix, headers=headers)).status_code == 400
        assert (await client.get("/api/v1/jobs/not-an-id", headers=headers)).status_code == 404
        cancel_url = "/api/v1/jobs/" + "a" * 32 + "/cancel"
        assert (await client.post(cancel_url, headers={"Origin": BASE},
                                  json={"schema_version": 1})).status_code == 403
        for body in ({"schema_version": True}, {"schema_version": 1, "force": True}, {}):
            assert (await client.post(cancel_url, headers=headers, json=body)).status_code == 400
        assert (await client.post(cancel_url + "?force=1", headers=headers,
                                  json={"schema_version": 1})).status_code == 400
        assert not (repository / "build/gui/jobs").exists()

    visit(repository, check)


def test_disclosed_input_job_preserves_output_protection_without_implicit_reveal(repository):
    catalog = Catalog(repository)
    protected = repository / "build/protected"
    protected.mkdir(parents=True)
    shutil.copyfile(repository / "examples/research-bars.csv", protected / "attachment.bin")
    original = catalog.register("build/protected")

    async def check(client, headers):
        denied = await client.post("/api/v1/jobs", headers=headers, json=request_body())
        assert denied.status_code == 403
        disclosed = await client.post(f"/api/v1/artifacts/{original['id']}/reveal-holdout",
            headers=headers, json={"schema_version": 1, "confirmation": "reveal outside protocol"})
        assert disclosed.status_code == 200, disclosed.text
        reveal_records = set(catalog.disclosures.root.glob("revealed_outside_protocol-*.json"))
        assert reveal_records
        submitted = await client.post("/api/v1/jobs", headers=headers, json=request_body())
        assert submitted.status_code == 200, submitted.text
        job = await finished(client, headers, submitted.json()["job"]["id"])
        assert job["state"] == "succeeded", job
        assert job["input_disclosure"] == "revealed_outside_protocol"
        assert job["input_lineage"]["protected_input_identities"]
        assert Catalog(repository).list()[1] == 1  # only the explicitly registered source
        registered = await client.post("/api/v1/catalog/register", headers=headers,
            json={"schema_version": 1, "path": job["output_path"]})
        assert registered.status_code == 200, registered.text
        artifact = registered.json()["artifact"]
        assert artifact["needs_disclosure"] is True
        files = await client.get(f"/api/v1/artifacts/{artifact['id']}/files?limit=200", headers=headers)
        assert files.status_code == 200
        assert files.json()["items"] and all(not item["available"] for item in files.json()["items"])
        interpreted = await client.get(f"/api/v1/runs/{artifact['id']}", headers=headers)
        assert interpreted.status_code == 403, interpreted.text
        assert interpreted.json()["error"]["code"] == "holdout_protected"
        assert set(catalog.disclosures.root.glob("revealed_outside_protocol-*.json")) == reveal_records

    visit(repository, check)


def test_job_input_path_and_holdout_protection_fail_closed(repository):
    catalog = Catalog(repository)
    protected = repository / "build/protected"
    protected.mkdir(parents=True)
    shutil.copyfile(repository / "examples/research-bars.csv", protected / "attachment.bin")
    catalog.register("build/protected")

    async def check(client, headers):
        denied = await client.post("/api/v1/jobs", headers=headers, json=request_body())
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["code"] == "holdout_protected"
        for path in (".git/config", "../private.json", "/etc/passwd", "build/gui/disclosures/private.json"):
            response = await client.post("/api/v1/jobs", headers=headers, json={**request_body(), "config_path": path})
            assert response.status_code in {400, 403}, response.text
            assert "Traceback" not in response.text and "/etc/passwd" not in response.text
        assert not list((repository / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))

    visit(repository, check)


def test_invalid_financial_config_remains_visible_as_failed_job(repository):
    path = repository / "examples/research-run.json"
    config = json.loads(path.read_text())
    config["strategy"]["fast_period"] = 0
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        run_research(path, repository / "build/invalid-direct", export_version=2)

    async def check(client, headers):
        response = await client.post("/api/v1/jobs", headers=headers, json=request_body())
        assert response.status_code == 200, response.text
        job = await finished(client, headers, response.json()["job"]["id"])
        assert job["state"] == "failed", job
        assert not (repository / job["output_path"] / "complete.json").exists()
        log = await client.get(f"/api/v1/jobs/{job['id']}/log", headers=headers)
        assert log.status_code == 200 and "Traceback" not in log.text
        canceled = await client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=headers, json={"schema_version": 1})
        assert canceled.status_code == 200 and canceled.json()["job"]["state"] == "failed"

    visit(repository, check)


def test_edits_to_original_files_after_acceptance_do_not_change_copied_job(repository):
    direct = run_research(repository / "examples/research-run.json", repository / "build/direct", export_version=2)

    async def check(client, headers):
        response = await client.post("/api/v1/jobs", headers=headers, json=request_body())
        assert response.status_code == 200, response.text
        # Acceptance has copied and hashed both inputs. Subsequent edits must
        # neither affect execution nor be mistaken for snapshot corruption.
        (repository / "examples/research-run.json").write_text("not the accepted configuration")
        (repository / "examples/research-bars.csv").write_text("not the accepted dataset")
        job = await finished(client, headers, response.json()["job"]["id"])
        assert job["state"] == "succeeded", job
        output = repository / job["output_path"]
        for name in ("report.json", "fills.csv", "equity.csv", "order_events.csv", "positions.csv"):
            assert (output / name).read_bytes() == (direct / name).read_bytes(), name

    visit(repository, check)
