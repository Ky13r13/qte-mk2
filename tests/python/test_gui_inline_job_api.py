"""Inline launch uses only authorized previewed bytes and the fixed local runner."""
import json

import pytest

from qte.gui.catalog import Catalog
from qte.gui.research_forms import compile_configuration
from test_gui_catalog import _unknown
from test_gui_form_api import PREVIEW, body, repository
from test_gui_job_api import finished, visit


async def accepted_preview(client, headers, kind="research_run"):
    payload = body(kind)
    if kind == "strategy_lab":
        payload["configuration"]["regimes"] = ["low_vol_trend"]
    result = await client.post(PREVIEW, headers=headers, json=payload)
    assert result.status_code == 200, result.text
    return {**payload, "preview_fingerprint": result.json()["preview_fingerprint"],
            "idempotency_key": "inline-research-key-123456", "timeout_seconds": 30}


@pytest.mark.parametrize("kind", ["research_run", "strategy_lab"])
def test_real_inline_jobs_deduplicate_match_cli_and_register_only_explicitly(repository, kind):
    async def check(client, headers):
        payload = await accepted_preview(client, headers, kind)
        direct_config = repository / "direct.json"
        direct_config.write_text(json.dumps(compile_configuration(kind, payload["configuration"])))
        if kind == "research_run":
            from qte.cli import run_research
            direct = run_research(direct_config, repository / "direct-results", export_version=2)
        else:
            from qte.strategy_lab import run_lab
            direct = run_lab(direct_config, repository / "direct-results")
        submitted = await client.post("/api/v1/jobs", headers=headers, json=payload)
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["job"]["id"]
        retry = await client.post("/api/v1/jobs", headers=headers, json=payload)
        assert retry.status_code == 200 and retry.json()["job"]["id"] == job_id
        if kind == "research_run":
            (repository / "data/bars.csv").write_bytes(b"edited after accepted capture")
        job = await finished(client, headers, job_id)
        assert job["state"] == "succeeded", job
        assert job["kind"] == kind
        assert job["input_disclosure"] == ("unknown" if kind == "research_run" else "not_required")
        actual = repository / job["output_path"]
        files = {path.relative_to(direct).as_posix() for path in direct.rglob("*") if path.is_file()}
        if kind == "research_run": files -= {"manifest.json", "complete.json"}
        for name in sorted(files):
            assert (actual / name).read_bytes() == (direct / name).read_bytes(), name
        assert Catalog(repository).list()[1] == 0
        registered = await client.post("/api/v1/catalog/register", headers=headers,
            json={"schema_version": 1, "path": job["output_path"]})
        assert registered.status_code == 200, registered.text
        item_id = registered.json()["artifact"]["id"]
        if kind == "strategy_lab":
            projection = await client.get(f"/api/v1/experiments/{item_id}", headers=headers)
            assert projection.status_code == 200, projection.text
            value = projection.json()["experiment"]
            assert value["selection"]["selected_candidate"] is None
            assert value["holdout"]["structured_access"] == "withheld"
            assert {row["role"] for row in value["windows"]} == {"train", "validation"}
            listing = await client.get(f"/api/v1/artifacts/{item_id}/files?limit=200", headers=headers)
            report = next(row for row in listing.json()["items"] if row["name"] == "hourly/report.json")
            assert report["available"] is False
            download = await client.get(f"/api/v1/artifacts/{item_id}/files/{report['id']}/download", headers=headers)
            assert download.status_code == 403
        assert not list((repository / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))
    visit(repository, check)


@pytest.mark.parametrize("mutation", ["valid_data", "malformed_data", "economic_config"])
def test_stale_preview_is_checked_before_adapter_or_financial_diagnostics(repository, mutation, monkeypatch):
    import qte.gui.job_routes as routes
    monkeypatch.setattr(routes, "prepare_form", lambda *_: pytest.fail("stale submission parsed financial inputs"))

    async def check(client, headers):
        payload = await accepted_preview(client, headers)
        path = repository / "data/bars.csv"
        if mutation == "valid_data": path.write_bytes(path.read_bytes().replace(b",1000\n", b",1001\n", 1))
        elif mutation == "malformed_data": path.write_bytes(b"PRIVATE_MALFORMED_DATA")
        else: payload["configuration"]["engine"]["initial_cash"] = -1.0
        response = await client.post("/api/v1/jobs", headers=headers, json=payload)
        assert response.status_code == 409 and response.json()["error"]["code"] == "stale_preview"
        assert "PRIVATE_" not in response.text
        assert not (repository / "build/gui/jobs").exists()
        assert not (repository / ".cache").exists()
    visit(repository, check)


@pytest.mark.parametrize("changed", [False, True])
def test_new_protection_precedes_stale_and_invalid_diagnostics(repository, changed, monkeypatch):
    import qte.gui.job_routes as routes
    monkeypatch.setattr(routes, "form_identity", lambda *_: pytest.fail("protected submission was compiled"))
    catalog = Catalog(repository)

    async def check(client, headers):
        payload = await accepted_preview(client, headers)
        path = repository / "data/bars.csv"
        if changed: path.write_bytes(b"PRIVATE_NEW_PROTECTED_DATA")
        catalog.register(_unknown(repository, "new-protected", path.read_bytes()))
        payload["configuration"]["strategy"]["quantity"] = True
        response = await client.post("/api/v1/jobs", headers=headers, json=payload)
        assert response.status_code == 403 and response.json()["error"]["code"] == "holdout_protected"
        assert "PRIVATE_" not in response.text
        assert not (repository / "build/gui/jobs").exists()
    visit(repository, check)


def test_inline_submission_preserves_auth_and_exact_envelope_limits(repository):
    async def check(client, headers):
        payload = await accepted_preview(client, headers)
        assert (await client.post("/api/v1/jobs", json=payload)).status_code == 403
        assert (await client.post("/api/v1/jobs", headers={**headers, "Origin": "https://elsewhere.invalid"}, json=payload)).status_code == 403
        for changes in ({"command": "forbidden"}, {"config_path": "data/bars.csv"},
                        {"kind": []}, {"preview_fingerprint": "short"}, {"timeout_seconds": True},
                        {"configuration": {"output": "build/arbitrary"}}):
            response = await client.post("/api/v1/jobs", headers=headers, json={**payload, **changes})
            assert response.status_code == 400, response.text
        raw = json.dumps(payload).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)
        assert (await client.post("/api/v1/jobs", headers={**headers, "Content-Type": "application/json"}, content=raw)).status_code == 400
        legacy = {"schema_version": 1, "kind": "research_run", "config_path": "examples/run.json",
                  "idempotency_key": "legacy-key-1234567890", "timeout_seconds": 30}
        response = await client.post("/api/v1/jobs", headers={**headers, "Content-Type": "application/json"},
            content=json.dumps(legacy).encode() + b" " * 4096)
        assert response.status_code == 413
        response = await client.post("/api/v1/jobs", headers={**headers, "Content-Type": "application/json"}, content=b" " * 16385)
        assert response.status_code == 413
        assert not (repository / "build/gui/jobs").exists()
    visit(repository, check)


def test_correctly_fingerprinted_invalid_form_never_creates_a_job(repository):
    from hashlib import sha256
    from qte.gui.research_forms import form_identity

    async def check(client, headers):
        payload = await accepted_preview(client, headers)
        payload["configuration"]["strategy"]["fast_period"] = "4"
        identity = form_identity("research_run", payload["configuration"], sha256((repository / "data/bars.csv").read_bytes()).hexdigest())
        payload["preview_fingerprint"] = identity["preview_fingerprint"]
        response = await client.post("/api/v1/jobs", headers=headers, json=payload)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_configuration"
        assert not (repository / "build/gui/jobs").exists()
    visit(repository, check)


def test_submit_snapshots_the_authorized_read_even_if_source_changes_during_validation(repository, monkeypatch):
    original = Catalog.authorize_job_inputs

    async def check(client, headers):
        payload = await accepted_preview(client, headers)
        source = repository / "data/bars.csv"
        accepted_bytes = source.read_bytes()
        authorizations = []

        def authorize_then_change(self, captured):
            result = original(self, captured)
            authorizations.append(captured.data_sha256)
            source.write_bytes(b"changed after authorized capture, before snapshot")
            return result

        monkeypatch.setattr(Catalog, "authorize_job_inputs", authorize_then_change)
        # The manager captured its bound authorizer when this app was built;
        # the route's first authorization still exercises this mutation seam.
        response = await client.post("/api/v1/jobs", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        job_id = response.json()["job"]["id"]
        job = await finished(client, headers, job_id)
        assert job["state"] == "succeeded", job
        assert authorizations
        assert (repository / "build/gui/jobs" / job_id / "inputs/input.csv").read_bytes() == accepted_bytes
    visit(repository, check)
