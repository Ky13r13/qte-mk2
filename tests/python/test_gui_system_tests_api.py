"""Authenticated System test-run API: sparse evidence and explicit disclosure."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from qte.gui import create_app
from qte.gui.test_evidence import fixed_test_command
from qte.gui.test_evidence_writer import finalize_test_evidence
from qte.gui.test_preflight import validate_prepared
from qte.gui import test_snapshots
from qte.gui.test_jobs import snapshot_value
from test_gui_test_evidence import xml
from test_gui_test_preflight import _value


BASE = "http://127.0.0.1:8765"
TESTS = "/api/v1/system/tests"


@pytest.fixture
def repository(tmp_path):
    """An otherwise empty repository is enough for inert prepared/evidence fixtures."""
    return tmp_path


def request(*, runner="pytest", key="system-tests-request-0001", timeout=30):
    return {"schema_version": 1, "runner": runner, "timeout_seconds": timeout,
            "idempotency_key": key}


def prepared(repository: Path, runner="pytest"):
    value = _value(runner)
    value["repository_path"] = str(repository)
    return validate_prepared(value)


def _manifest(repository: Path, job_id: str, runner: str, *, outcome="passed"):
    identity = deepcopy(prepared(repository, runner).value["identity"])
    junit = xml('<testcase name="private-case-name" classname="private.module"><failure /></testcase>',
                failures=1) if outcome == "failed" else (
        xml('<testcase name="private-skipped-name" classname="private.module"><skipped /></testcase>',
            skipped=1) if outcome == "skipped" else xml('<testcase name="private-passed-name" classname="private.module"/>'))
    code = 1 if outcome == "failed" else 0
    return junit, {"schema_version": 1, "artifact_kind": "test_evidence_v1", "runner": runner,
        "job_id": job_id, "repository_path": str(repository), "cwd": str(repository),
        "argv": list(fixed_test_command(str(repository), runner, job_id)), "started_ns": "1",
        "finished_ns": "2", "exit_code": code, "termination": "exited",
        "versions": prepared(repository, runner).value["versions"],
        "identity_scope": "explicit_files_v1", "start_identity": identity,
        "end_identity": deepcopy(identity), "source_to_binary": "unknown", "log_truncated": False}


def write_evidence(repository: Path, job_id: str, *, runner="pytest", outcome="passed"):
    root = repository / "build/gui/jobs" / job_id / "artifacts"
    root.mkdir(parents=True)
    junit, manifest = _manifest(repository, job_id, runner, outcome=outcome)
    (root / "junit.xml").write_bytes(junit)
    return finalize_test_evidence(repository, job_id, manifest, b"PRIVATE TEST LOG")


def insert_job(manager, monkeypatch, job_id: str, *, kind="test_run", runner="pytest", state="succeeded"):
    """Insert an accepted terminal queue row without ever launching a runner."""
    manager._open()
    accepted = prepared(manager.repository, runner)
    monkeypatch.setattr(test_snapshots, "recheck_prepared", lambda repository, item: accepted)
    snapshot = snapshot_value(test_snapshots.write_test_snapshot(manager.repository, job_id, accepted))
    with manager._connect() as db:
        db.execute("""INSERT INTO gui_jobs
            (id,idempotency_key,fingerprint,request_id,kind,state,phase,reason,created_ns,start_ns,end_ns,
             timeout_seconds,source_identity,input_hashes,lineage,snapshot,launch_identity,supervisor_pid,
             executable,cwd,exit_code,output_path,log) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, "key-" + job_id, "fingerprint-" + job_id, "request-" + job_id, kind, state,
             "finished", "test_job_failed" if state == "failed" else None, 1, 1, 2, 30,
             "sha256:" + snapshot["request_sha256"], "{}", json.dumps({"input_disclosure": "unknown"}),
             json.dumps(snapshot), None, None, None, None, None,
             f"build/gui/jobs/{job_id}/artifacts", "accepted\n"))


async def login(client, code):
    response = await client.post("/api/v1/session", headers={"Origin": BASE},
                                 json={"schema_version": 1, "code": code})
    assert response.status_code == 200
    return {"Origin": BASE, "X-QTE-Token": response.json()["request_token"]}


def visit(repository, check):
    async def scenario():
        codes = []
        app = create_app(repository, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                await check(app, client, await login(client, codes[0]))
    asyncio.run(scenario())


def test_system_test_submit_is_authenticated_exact_and_idempotent(repository, monkeypatch):
    import qte.gui.test_routes as routes

    accepted = prepared(repository)
    calls = []

    async def check(app, client, headers):
        def submit(request_id, kind, value, key, timeout):
            calls.append((request_id, kind, value, key, timeout))
            return {"id": "a" * 32, "kind": kind, "state": "queued", "created_ns": "1",
                    "timeout_seconds": timeout}
        monkeypatch.setattr(routes, "prepare_test_run", lambda root, runner: accepted)
        monkeypatch.setattr(app.state.jobs, "submit", submit)
        response = await client.post(TESTS, headers=headers, json=request())
        assert response.status_code == 200, response.text
        assert response.json()["job"]["id"] == "a" * 32
        assert len(calls) == 1
        _, kind, value, key, timeout = calls[0]
        assert kind == "test_run" and value == accepted and key == request()["idempotency_key"] and timeout == 30
        again = await client.post(TESTS, headers=headers, json=request())
        assert again.status_code == 200
        assert len(calls) == 2  # the durable manager, not the HTTP route, owns idempotency.

        assert (await client.post(TESTS, json=request())).status_code == 403
        assert (await client.post(TESTS, headers={**headers, "Origin": "https://outside.invalid"}, json=request())).status_code == 403
        for body in ({**request(), "command": "echo injected"}, {**request(), "path": "tests"},
                     {**request(), "runner": "shell"}, {**request(), "timeout_seconds": True},
                     {**request(), "timeout_seconds": 1801}, {**request(), "idempotency_key": "short"},
                     {"schema_version": 1, "runner": "pytest"}):
            assert (await client.post(TESTS, headers=headers, json=body)).status_code == 400
        assert (await client.post(TESTS + "?runner=pytest", headers=headers, json=request())).status_code == 400
        assert len(calls) == 2

    visit(repository, check)


def test_system_test_gets_are_sparse_filtered_and_never_prepare_or_execute(repository, monkeypatch):
    import qte.gui.test_routes as routes
    import qte.gui.test_views as views

    test_id, other_id = "b" * 32, "c" * 32

    async def check(app, client, headers):
        insert_job(app.state.jobs, monkeypatch, test_id)
        insert_job(app.state.jobs, monkeypatch, other_id, kind="research_run")
        write_evidence(repository, test_id)
        monkeypatch.setattr(routes, "prepare_test_run", lambda *args: pytest.fail("GET prepared a test"))
        monkeypatch.setattr(views, "prepare_test_run", lambda *args: pytest.fail("GET executed preflight"))
        listing = await client.get(TESTS + "?offset=0&limit=10", headers=headers)
        assert listing.status_code == 200, listing.text
        value = listing.json()
        assert value["total"] == 1 and [item["id"] for item in value["items"]] == [test_id]
        assert value["items"][0]["kind"] == "test_run"
        assert (await client.get(TESTS + "?limit=0", headers=headers)).status_code == 400
        assert (await client.get(TESTS + "?offset=0&offset=1", headers=headers)).status_code == 400
        assert (await client.get(TESTS + "?path=build/private", headers=headers)).status_code == 400
        detail = await client.get(f"{TESTS}/{test_id}?offset=0&limit=25", headers=headers)
        assert detail.status_code == 200, detail.text
        evidence = detail.json()["evidence"]
        assert evidence == {"state": "protected"}
        rendered = detail.text
        for secret in ("private-case-name", "PRIVATE TEST LOG", "private.module"):
            assert secret not in rendered
        assert (await client.get(f"{TESTS}/{other_id}", headers=headers)).status_code == 404
        assert (await client.get(f"{TESTS}/not-an-id", headers=headers)).status_code == 404
        assert (await client.get(f"{TESTS}/{test_id}/log", headers=headers)).status_code == 404
        assert (await client.get(f"{TESTS}/{test_id}?limit=201", headers=headers)).status_code == 400
        assert (await client.get(TESTS)).status_code == 403

    visit(repository, check)


@pytest.mark.parametrize("outcome", ["failed", "skipped"])
def test_reveal_binds_complete_nonpassing_evidence_and_check_current_is_explicit(repository, monkeypatch, outcome):
    import qte.gui.test_views as views

    job_id = ("d" if outcome == "failed" else "e") * 32

    async def check(app, client, headers):
        insert_job(app.state.jobs, monkeypatch, job_id, state="failed")
        write_evidence(repository, job_id, outcome=outcome)
        protected = await client.get(f"{TESTS}/{job_id}", headers=headers)
        assert protected.status_code == 200 and protected.json()["evidence"] == {"state": "protected"}
        # Checking/revealing requires the exact request envelopes and disclosure precedes every read.
        assert (await client.post(f"{TESTS}/{job_id}/check-current", headers=headers,
                                  json={"schema_version": 1})).status_code == 403
        assert (await client.post(f"{TESTS}/{job_id}/reveal", headers=headers,
                                  json={"schema_version": 1, "confirmation": "wrong"})).status_code == 400
        assert (await client.post(f"{TESTS}/{job_id}/reveal?x=1", headers=headers,
                                  json={"schema_version": 1, "confirmation": "reveal outside protocol"})).status_code == 400
        reveal = await client.post(f"{TESTS}/{job_id}/reveal", headers=headers,
                                   json={"schema_version": 1, "confirmation": "reveal outside protocol"})
        assert reveal.status_code == 200, reveal.text
        record = await client.get(f"{TESTS}/{job_id}?offset=0&limit=25", headers=headers)
        assert record.status_code == 200, record.text
        evidence = record.json()["evidence"]
        assert evidence["state"] == "verified" and evidence["outcome"] == outcome
        assert evidence["counts"]["tests"] == 1
        assert evidence["cases"][0]["name"].startswith("private-")
        assert evidence["identity_stability"] == {"source": "same", "tests": "same", "binaries": "same", "build": "same"}
        assert set(evidence["current_match"].values()) == {"unknown"}
        records = list((repository / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))
        assert len(records) == 1
        journal = json.loads(records[0].read_text())
        assert journal["event"] == "revealed_outside_protocol" and isinstance(journal["identity"], str)

        fresh = prepared(repository, runner="pytest")
        changed = fresh.value
        changed["identity"]["source"][next(iter(changed["identity"]["source"]))] = "f" * 64
        changed = validate_prepared(changed)
        monkeypatch.setattr(views, "prepare_test_run", lambda root, runner: changed)
        current = await client.post(f"{TESTS}/{job_id}/check-current", headers=headers,
                                    json={"schema_version": 1})
        assert current.status_code == 200, current.text
        assert current.json()["current_match"] == {"source": "different", "tests": "same",
                                                    "binaries": "same", "build": "same"}

        # Disclosure is bound to the completed bytes, not a durable permission
        # for whichever files happen to occupy the job directory later.
        (repository / f"build/gui/jobs/{job_id}/artifacts/junit.xml").write_bytes(b"changed after reveal")
        changed_record = await client.get(f"{TESTS}/{job_id}?offset=0&limit=25", headers=headers)
        assert changed_record.status_code == 200
        assert changed_record.json()["evidence"] == {"state": "unavailable"}

    visit(repository, check)


def test_cancellation_does_not_bypass_protection_or_create_catalog_entries(repository, monkeypatch):
    job_id = "f" * 32

    async def check(app, client, headers):
        insert_job(app.state.jobs, monkeypatch, job_id, state="canceled")
        write_evidence(repository, job_id)
        canceled = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers,
                                     json={"schema_version": 1})
        assert canceled.status_code == 200 and canceled.json()["job"]["state"] == "canceled"
        detail = await client.get(f"{TESTS}/{job_id}", headers=headers)
        assert detail.status_code == 200 and detail.json()["evidence"] == {"state": "protected"}
        assert not (repository / ".cache/qte-gui/catalog.sqlite3").exists() or app.state.catalog.list()[1] == 0

    visit(repository, check)


def replace_valid_record(repository, job_id, *, log=None, changed_end=False):
    """Deliberately replace fixture bytes and their hashes, keeping a valid record."""
    root = repository / f"build/gui/jobs/{job_id}/artifacts"
    if log is not None:
        (root / "log.txt").write_bytes(log)
    if changed_end:
        manifest = json.loads((root / "manifest.json").read_bytes())
        manifest["end_identity"]["source"][next(iter(manifest["end_identity"]["source"]))] = "f" * 64
        (root / "manifest.json").write_text(json.dumps(manifest))
    marker = {"schema_version": 1, "sha256": {
        name: sha256((root / name).read_bytes()).hexdigest() for name in ("manifest.json", "junit.xml", "log.txt")}}
    (root / "complete.json").write_text(json.dumps(marker))


def test_valid_replacement_requires_new_reveal_and_current_failure_stays_unknown(repository, monkeypatch):
    import qte.gui.test_views as views
    job_id = "1" * 32

    async def check(app, client, headers):
        insert_job(app.state.jobs, monkeypatch, job_id, state="failed")
        write_evidence(repository, job_id, outcome="failed")
        replace_valid_record(repository, job_id, changed_end=True)
        url = f"{TESTS}/{job_id}"
        body = {"schema_version": 1, "confirmation": "reveal outside protocol"}
        assert (await client.post(url + "/reveal", headers=headers, json=body)).status_code == 200
        disclosed = (await client.get(url, headers=headers)).json()["evidence"]
        assert disclosed["identity_stability"]["source"] == "different"
        assert disclosed["outcome"] == "failed"
        replace_valid_record(repository, job_id, log=b"replacement private log")
        assert (await client.get(url, headers=headers)).json()["evidence"] == {"state": "protected"}
        assert (await client.post(url + "/check-current", headers=headers, json={"schema_version": 1})).status_code == 403
        assert (await client.post(url + "/reveal", headers=headers, json=body)).status_code == 200

        def failed_preflight(*args):
            raise ValueError("PRIVATE preflight exception")
        monkeypatch.setattr(views, "prepare_test_run", failed_preflight)
        current = await client.post(url + "/check-current", headers=headers, json={"schema_version": 1})
        assert current.status_code == 200
        assert set(current.json()["current_match"].values()) == {"unknown"}
        assert current.json()["versions_match"] == "unknown"
        assert current.json()["checked_ns"].isdecimal()
        assert "PRIVATE" not in current.text
        assert len(list((repository / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))) == 2
    visit(repository, check)


@pytest.mark.parametrize("failure", ["write", "readback", "mutation"])
def test_reveal_fails_closed_on_journal_error_or_mutation(repository, monkeypatch, failure):
    from qte.gui.disclosures import DisclosureJournal
    job_id = "2" * 32

    async def check(app, client, headers):
        insert_job(app.state.jobs, monkeypatch, job_id)
        write_evidence(repository, job_id)
        original = DisclosureJournal.reveal

        def reveal(journal, identities):
            if failure == "write":
                raise OSError("private storage failure")
            original(journal, identities)
            if failure == "mutation":
                replace_valid_record(repository, job_id, log=b"mutated during reveal")
        monkeypatch.setattr(DisclosureJournal, "reveal", reveal)
        if failure == "readback":
            monkeypatch.setattr(DisclosureJournal, "revealed", lambda *args: False)
        url = f"{TESTS}/{job_id}"
        response = await client.post(url + "/reveal", headers=headers,
            json={"schema_version": 1, "confirmation": "reveal outside protocol"})
        assert response.status_code == 409
        assert "private" not in response.text
        detail = await client.get(url, headers=headers)
        assert detail.status_code == 200 and detail.json()["evidence"] == {"state": "protected"}
        assert "private-passed-name" not in detail.text
    visit(repository, check)


def test_evidence_read_and_request_mutation_never_serve_previously_revealed_bytes(repository, monkeypatch):
    import qte.gui.test_views as views
    job_id = "3" * 32

    async def check(app, client, headers):
        insert_job(app.state.jobs, monkeypatch, job_id)
        write_evidence(repository, job_id)
        url = f"{TESTS}/{job_id}"
        body = {"schema_version": 1, "confirmation": "reveal outside protocol"}
        assert (await client.post(url + "/reveal", headers=headers, json=body)).status_code == 200
        original = views._case_page

        def changed_page(*args):
            result = original(*args)
            replace_valid_record(repository, job_id, log=b"replacement during projection")
            return result
        monkeypatch.setattr(views, "_case_page", changed_page)
        response = await client.get(url, headers=headers)
        assert response.status_code == 409 and "private-passed-name" not in response.text
        monkeypatch.setattr(views, "_case_page", original)
        assert (await client.post(url + "/reveal", headers=headers, json=body)).status_code == 200
        request_path = repository / f"build/gui/jobs/{job_id}/inputs/test-request.json"
        request_path.chmod(0o600)
        request_path.write_bytes(b"changed accepted request")
        detail = await client.get(url, headers=headers)
        assert detail.status_code == 200 and detail.json()["evidence"] == {"state": "unavailable"}
    visit(repository, check)
