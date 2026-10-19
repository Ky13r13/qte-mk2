"""Real offline comparison HTTP jobs and accepted-output correspondence."""
from copy import deepcopy
import json

import pytest

from qte.comparison_export import run_comparison
from qte.gui.artifacts import ArtifactError, inspect_artifact
from qte.gui.comparison_inputs import prepare_comparison_inputs
from qte.gui.comparison_jobs import verify_comparison_job_output
from qte.gui.job_inputs import JobInputError, write_snapshot
from test_gui_comparison_inputs import fixture, identity
from test_gui_job_api import finished, visit


PREVIEW = "/api/v1/comparisons/preview"
SUBMIT = "/api/v1/comparisons/jobs"


def body(form):
    return {"schema_version": 1, "kind": "comparison_run", "configuration": form}


def assert_private(value, prepared):
    encoded = json.dumps(value)
    for source in prepared.sources:
        assert source.data_sha256 not in encoded
        assert source.validation_identity not in encoded
        assert source.validation_id not in encoded
        assert source.source_path not in encoded
    assert "safe_identities" not in encoded and "test_identities" not in encoded
    assert "data_configuration" not in encoded and "selected_candidate" not in encoded


@pytest.mark.parametrize("workflow", ["historical_experiment", "moving_average_comparison"])
def test_real_comparison_job_matches_cli_and_keeps_safe_views_usable(tmp_path, workflow):
    catalog, form = fixture(tmp_path, workflow)
    prepared = prepare_comparison_inputs(catalog, form)
    original_count = catalog.list()[1]
    async def check(client, headers):
        response = await client.post(PREVIEW, headers=headers, json=body(form))
        assert response.status_code == 200, response.text
        preview = response.json()
        assert set(preview) == {"schema_version", "kind", "preview_fingerprint", "holdout_history", "assumptions"}
        assert preview["holdout_history"] == "unknown"
        assert_private(preview, prepared)
        assert not (tmp_path / "build/gui/jobs").exists()
        assert not catalog.disclosures.protected(identity(prepared.sources[-1]))
        request = {**body(form), "preview_fingerprint": preview["preview_fingerprint"],
                   "idempotency_key": "comparison_12345678", "timeout_seconds": 30}
        submitted = await client.post(SUBMIT, headers=headers, json=request)
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["job"]["id"]
        assert_private(submitted.json(), prepared)
        duplicate = await client.post(SUBMIT, headers=headers, json=request)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["job"]["id"] == job_id
        job = await finished(client, headers, job_id)
        assert job["state"] == "succeeded", job
        assert job["input_hashes"] == {} and job["input_lineage"] == {"holdout_history": "unknown"}
        assert_private(job, prepared)
        for url in ("/api/v1/jobs", f"/api/v1/jobs/{job_id}", f"/api/v1/jobs/{job_id}/log"):
            result = await client.get(url, headers=headers)
            assert result.status_code == 200, result.text
            assert result.headers["cache-control"] == "no-store"
            assert_private(result.json(), prepared)
        canceled = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers, json={"schema_version": 1})
        assert canceled.json()["job"]["state"] == "succeeded"
        assert_private(canceled.json(), prepared)
        assert catalog.list()[1] == original_count  # no auto-registration
        assert catalog.disclosures.protected(identity(prepared.sources[-1]))
        assert not catalog.disclosures.protected(identity(prepared.sources[0]))
        assert not list(catalog.disclosures.root.glob("revealed_outside_protocol-*"))
        root = tmp_path / "build/gui/jobs" / job_id
        direct = run_comparison(root / "inputs/effective-config.json", tmp_path / "build/direct")
        actual = {p.relative_to(root / "artifacts").as_posix(): p.read_bytes()
                  for p in (root / "artifacts").rglob("*") if p.is_file()}
        assert actual == {p.relative_to(direct).as_posix(): p.read_bytes() for p in direct.rglob("*") if p.is_file()}
        item = catalog.register(job["output_path"])
        assert item["kind"] == "comparison_export_v1" and item["status"]["integrity"] == "verified"
        files = {row["name"]: row for row in item["files"]}
        assert files["inputs/window-000.csv"]["available"]
        assert not files[f"inputs/window-{len(prepared.sources)-1:03d}.csv"]["available"]
        assert not files["report.json"]["available"]
        for name in ("report.json", "complete.json"):
            assert not files[name]["available"]
            with pytest.raises(ArtifactError):
                catalog.read_file(item["id"], files[name]["id"])
            denied = await client.get(f"/api/v1/artifacts/{item['id']}/files/{files[name]['id']}/download", headers=headers)
            assert denied.status_code == 403, denied.text
            assert_private(denied.json(), prepared)
        assert catalog.comparison_view(item["id"], table="candidates")["rows"]
        assert catalog.comparison_view(item["id"], table="windows")["rows"]
    visit(tmp_path, check)


def test_comparison_envelopes_auth_stale_preview_and_protected_errors(tmp_path):
    catalog, form = fixture(tmp_path)
    prepared = prepare_comparison_inputs(catalog, form)
    async def check(client, headers):
        assert (await client.post(PREVIEW, json=body(form))).status_code == 403
        assert (await client.post(PREVIEW, headers={**headers, "Origin": "https://elsewhere.invalid"}, json=body(form))).status_code == 403
        assert (await client.post(PREVIEW + "?execute=1", headers=headers, json=body(form))).status_code == 400
        for bad in ({**body(form), "command": "SECRET"}, {**body(form), "kind": "download_alpaca"}):
            result = await client.post(PREVIEW, headers=headers, json=bad)
            assert result.status_code == 400 and "SECRET" not in result.text
        preview = await client.post(PREVIEW, headers=headers, json=body(form))
        request = {**body(form), "preview_fingerprint": preview.json()["preview_fingerprint"],
                   "idempotency_key": "comparison_12345678", "timeout_seconds": 30}
        assert (await client.post("/api/v1/jobs", headers=headers, json=request)).status_code == 400
        assert (await client.post(SUBMIT, json=request)).status_code == 403
        assert (await client.post(SUBMIT + "?execute=1", headers=headers, json=request)).status_code == 400
        for bad in ({**request, "config_origin": {}}, {**request, "kind": "research_run"},
                    {**request, "config_path": "SECRET"}, {**request, "preview_fingerprint": "x"}):
            assert (await client.post(SUBMIT, headers=headers, json=bad)).status_code == 400
        changed = deepcopy(request)
        changed["configuration"]["candidates"][0]["parameters"]["quantity"] = "2"
        stale = await client.post(SUBMIT, headers=headers, json=changed)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_preview"
        assert not (tmp_path / "build/gui/jobs").exists()
        catalog.disclosures.reveal([identity(prepared.sources[-1])])
        for url, payload in ((PREVIEW, body(form)), (SUBMIT, request)):
            denied = await client.post(url, headers=headers, json=payload)
            assert denied.status_code == 409, denied.text
            assert_private(denied.json(), prepared)
            assert denied.json()["error"]["message"] == "Comparison inputs are unavailable or inconsistent."
        assert not (tmp_path / "build/gui/jobs").exists()
    visit(tmp_path, check)


@pytest.mark.parametrize("change", ["candidate", "input", "source", "submitted"])
def test_valid_but_unrelated_export_is_not_accepted_job_output(tmp_path, change):
    catalog, form = fixture(tmp_path)
    prepared = prepare_comparison_inputs(catalog, form)
    snapshot = write_snapshot(tmp_path, "a" * 32, prepared)
    effective = json.loads(prepared.effective_config_bytes)
    # Keep the snapshot immutable; build a separately valid substituted export.
    alternative = tmp_path / "build/alternative"
    alternative.mkdir()
    for index, source in enumerate(prepared.sources):
        (alternative / f"window-{index:03d}.csv").write_bytes(source.data_bytes)
    if change == "candidate":
        effective["candidates"][0]["parameters"]["quantity"] = 2
    elif change == "input":
        from hashlib import sha256
        raw = prepared.sources[-1].data_bytes.replace(b",100\n", b",101\n")
        (alternative / f"window-{len(prepared.sources)-1:03d}.csv").write_bytes(raw)
        effective["windows"][-1]["data"]["expected_sha256"] = sha256(raw).hexdigest()
        effective["windows"][-1]["evidence"]["source_sha256"] = sha256(raw).hexdigest()
    elif change == "submitted":
        # An alternate normalized path is legitimate CLI input, but was not accepted.
        effective["windows"][0]["data"]["path"] = "./window-000.csv"
    config = alternative / "comparison.json"
    config.write_text(json.dumps(effective))
    output = run_comparison(config, tmp_path / "build/gui/jobs" / snapshot.job_id / "artifacts")
    assert inspect_artifact(output).metadata["integrity"] == "verified"
    source = prepared.source_identity if change != "source" else "sha256:" + "0" * 64
    with pytest.raises(JobInputError):
        verify_comparison_job_output(tmp_path, snapshot.job_id, snapshot.snapshot_hashes, source)
