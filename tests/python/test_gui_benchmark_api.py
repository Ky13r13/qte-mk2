"""Benchmark controls are exact, authenticated, passive, and log-free."""
import json

import pytest

from qte.gui import benchmark_inputs as inputs, benchmark_routes as routes
from qte.gui.benchmark_evidence import write_evidence
from test_gui_system_tests_api import visit, prepared


PREFIX = "/api/v1/system/benchmarks"


def body(**changes):
    return {"schema_version": 1, "bars": 10, "strategy": "hold", "timeout_seconds": 30,
            "idempotency_key": "benchmark-request-0001", **changes}


def accepted(repository):
    return inputs.validate_prepared(inputs.PreparedBenchmark(inputs._canonical({
        "schema_version": 1, "bars": 10, "strategy": "hold", "runtime": prepared(repository).value})))


def measurement(runtime):
    return {"schema_version": 1, "artifact_kind": "benchmark_measurement_v1",
        "kind": "synthetic_not_historical", "workload_version": "synthetic-scale-v1",
        "workload_label": "no_op", "bars": 10, "strategy": "hold", "load_seconds": 0.1,
        "replay_seconds": 0.2, "bars_per_second": 50.0, "fills": 0, "final_equity": 100000.0,
        "source_identity": "sha256:" + "a" * 64,
        "machine": {"system": "Linux", "machine": "x86_64", "python": runtime["versions"]["python"], "cpu_count": 4},
        "timing": {"sample_count": 1, "warmup_count": 0, "clock": "time.perf_counter",
                  "load_scope": "synthetic_generation_and_dataset", "replay_scope": "strategy_construction_and_engine_run"}}


def insert(manager, monkeypatch, job_id, kind="benchmark", state="succeeded"):
    manager._open()
    request = accepted(manager.repository)
    monkeypatch.setattr(inputs, "recheck_prepared", lambda root, item: item)
    snapshot = inputs.write_snapshot(manager.repository, job_id, request)
    with manager._connect() as db:
        db.execute("""INSERT INTO gui_jobs
            (id,idempotency_key,fingerprint,request_id,kind,state,phase,created_ns,start_ns,end_ns,
             timeout_seconds,source_identity,input_hashes,lineage,snapshot,output_path,log)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (job_id, job_id, job_id, job_id, kind, state, "finished", 1, 1, 2, 30,
              "sha256:" + snapshot["request_sha256"], "{}", "{}", json.dumps(snapshot),
              f"build/gui/jobs/{job_id}/artifacts", "accepted\n"))
    root = manager.repository / f"build/gui/jobs/{job_id}/artifacts"
    root.mkdir()
    runtime = request.value["runtime"]
    manifest = {"schema_version": 1, "artifact_kind": "benchmark_evidence_v1", "job_id": job_id,
        "repository_path": str(manager.repository), "argv": list(inputs.fixed_command(manager.repository, job_id, 10, "hold")),
        "request_sha256": snapshot["request_sha256"], "started_ns": "1", "finished_ns": "2",
        "start_runtime": runtime, "end_runtime": runtime, "source_to_binary": "unknown",
        "measurement": measurement(runtime)}
    write_evidence(manager.repository, job_id, manifest)
    (root.parent / "runner.log").write_text("PRIVATE RAW BENCHMARK LOG")


def test_benchmark_auth_exact_inputs_and_no_dynamic_commands(tmp_path, monkeypatch):
    calls = []
    async def check(app, client, headers):
        monkeypatch.setattr(routes, "prepare_benchmark", lambda *args: accepted(tmp_path))
        def submit(request_id, kind, value, key, timeout):
            calls.append((kind, value, key, timeout))
            return {"id": "a"*32, "kind": kind, "state": "queued"}
        monkeypatch.setattr(app.state.jobs, "submit", submit)
        assert (await client.post(PREFIX, headers=headers, json=body())).status_code == 200
        assert calls[0] == ("benchmark", accepted(tmp_path), body()["idempotency_key"], 30)
        assert (await client.post(PREFIX, json=body())).status_code == 403
        assert (await client.post(PREFIX, headers={**headers, "Origin": "https://elsewhere.invalid"}, json=body())).status_code == 403
        for changes in ({"bars": True}, {"bars": 0}, {"bars": 100001}, {"bars": 1.0},
                        {"strategy": "buy_hold"}, {"strategy": []}, {"command": "arbitrary"},
                        {"path": "/tmp/secret"}, {"environment": {}}, {"timeout_seconds": True},
                        {"timeout_seconds": 1801}, {"idempotency_key": "short"}):
            response = await client.post(PREFIX, headers=headers, json=body(**changes))
            assert response.status_code == 400, response.text
        assert (await client.post(PREFIX + "?bars=1", headers=headers, json=body())).status_code == 400
        assert len(calls) == 1
        def failed(*args): raise ValueError("PRIVATE PREFLIGHT FAILURE")
        monkeypatch.setattr(routes, "prepare_benchmark", failed)
        response = await client.post(PREFIX, headers=headers, json=body())
        assert response.status_code == 409 and "PRIVATE" not in response.text
    visit(tmp_path, check)


def test_benchmark_passive_filtered_views_and_no_raw_output(tmp_path, monkeypatch):
    async def check(app, client, headers):
        insert(app.state.jobs, monkeypatch, "a"*32)
        insert(app.state.jobs, monkeypatch, "b"*32, kind="research_run")
        def forbidden(*args): pytest.fail("Passive GET performed benchmark preflight.")
        monkeypatch.setattr(routes, "prepare_benchmark", forbidden)
        monkeypatch.setattr(inputs, "prepare_test_run", forbidden)
        listing = await client.get(PREFIX + "?offset=0&limit=1", headers=headers)
        assert listing.status_code == 200 and listing.json()["total"] == 1
        assert [row["id"] for row in listing.json()["items"]] == ["a"*32]
        detail = await client.get(PREFIX + "/" + "a"*32, headers=headers)
        assert detail.status_code == 200
        evidence = detail.json()["evidence"]
        assert evidence["state"] == "verified" and evidence["measurement"]["bars"] == 10
        assert evidence["identity_stability"] == dict.fromkeys(("source", "tests", "binaries", "build"), "same")
        assert evidence["source_to_binary"] == "unknown"
        assert "PRIVATE" not in detail.text and "start_runtime" not in detail.text and "argv" not in detail.text
        assert "source_identity" not in evidence["measurement"]
        for tail in ("/log", "/download", "/manifest.json"):
            assert (await client.get(PREFIX + "/" + "a"*32 + tail, headers=headers)).status_code == 404
        assert (await client.get(PREFIX + "/" + "b"*32, headers=headers)).status_code == 404
        for query in ("?limit=0", "?limit=201", "?offset=-1", "?offset=0&offset=1", "?path=secret"):
            assert (await client.get(PREFIX + query, headers=headers)).status_code == 400
        assert (await client.get(PREFIX)).status_code == 403
        assert (await client.get(PREFIX + "/" + "a"*32 + "?limit=1", headers=headers)).status_code == 400
        # Raw registration never becomes permissible for verified benchmarks.
        response = await client.post("/api/v1/catalog/register", headers=headers,
                                     json={"schema_version": 1, "path": f"build/gui/jobs/{'a'*32}/artifacts"})
        assert response.status_code == 409 and response.json()["error"]["code"] == "job_output_unavailable"
    visit(tmp_path, check)


def test_benchmark_partial_changed_and_projection_races_fail_closed(tmp_path, monkeypatch):
    async def check(app, client, headers):
        insert(app.state.jobs, monkeypatch, "a"*32)
        path = tmp_path / f"build/gui/jobs/{'a'*32}/artifacts/complete.json"
        original = path.read_bytes()
        path.write_bytes(b"{}")
        result = await client.get(PREFIX + "/" + "a"*32, headers=headers)
        assert result.json()["evidence"] == {"state": "unavailable"}
        path.write_bytes(original)
        project = routes.projection
        def race(value):
            result = project(value)
            path.write_bytes(b"{}")
            return result
        monkeypatch.setattr(routes, "projection", race)
        result = await client.get(PREFIX + "/" + "a"*32, headers=headers)
        assert result.json()["evidence"] == {"state": "unavailable"}
    visit(tmp_path, check)
