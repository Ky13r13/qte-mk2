from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import types

import pytest

from qte.gui.jobs import JobError, JobManager, RunningJob, _worker_result


@dataclass(frozen=True)
class Prepared:
    original_config_path: str = "examples/run.json"
    original_data_path: str = "examples/data.csv"
    adapter: str = "csv"
    config_bytes: bytes = b'{"safe":true}'
    data_bytes: bytes = b"timestamp,value\n1,2\n"
    config_sha256: str = "a" * 64
    data_sha256: str = "b" * 64
    canonical_config_sha256: str = "c" * 64
    effective_config_bytes: bytes = b'{"safe":true}'
    fingerprint: str = "d" * 64


class FakeProcess:
    pid = 424242

    def wait(self):
        return 0


class FakeRunning:
    def __init__(self, gate, order, result=None):
        self.process = FakeProcess(); self.gate = gate; self.order = order
        self.result = result or {"state": "succeeded", "reason": None, "exit_code": 0}
        self.closed = False

    def wait(self):
        self.gate.wait(5)
        return self.result

    def cancel(self):
        self.result = {"state": "canceled", "reason": "canceled", "exit_code": None}
        self.gate.set()

    def close_owner(self): self.closed = True


class FakeSupervisor:
    gate = threading.Event()
    order = []

    def __init__(self, repository, lock_fd): self.repository = repository

    def launch(self, spec_path, environment):
        spec = json.loads(spec_path.read_text())
        self.order.append(spec["job_id"])
        return FakeRunning(self.gate, self.order)


class Catalog:
    def __init__(self): self.protected = []

    def authorize_job_inputs(self, prepared):
        return {"input_identities": [prepared.data_sha256],
                "protected_input_identities": [], "input_disclosure": "not_required"}

    def protect_job_output(self, output, lineage):
        self.protected.append((output, lineage))


@pytest.fixture
def inputs_module(monkeypatch):
    module = types.ModuleType("qte.gui.job_inputs")

    class SnapshotInputs:
        def __init__(self, hashes):
            self.hashes = hashes

    def write_snapshot(repository, job_id, prepared):
        root = repository / "build/gui/jobs" / job_id / "inputs"
        root.mkdir(parents=True)
        files = {"original-config.json": prepared.config_bytes,
                 "effective-config.json": prepared.effective_config_bytes,
                 "input.csv": prepared.data_bytes}
        hashes = {}
        for name, data in files.items():
            (root / name).write_bytes(data); hashes[name] = hashlib.sha256(data).hexdigest()
        manifest = json.dumps(hashes, sort_keys=True).encode(); (root / "input-manifest.json").write_bytes(manifest)
        hashes["input-manifest.json"] = hashlib.sha256(manifest).hexdigest()
        relative = root.relative_to(repository).as_posix()
        return {"job_id": job_id, "input_root": relative,
                "effective_config_path": f"{relative}/effective-config.json",
                "hashes": {"config_sha256": prepared.config_sha256,
                           "data_sha256": prepared.data_sha256,
                           "canonical_config_sha256": prepared.canonical_config_sha256},
                "snapshot_hashes": hashes, "fingerprint": prepared.fingerprint}

    def verify_snapshot(repository, job_id, expected):
        root = repository / "build/gui/jobs" / job_id / "inputs"
        for name, digest in expected.items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        return SnapshotInputs({"canonical_config_sha256": "c" * 64})

    module.write_snapshot = write_snapshot; module.verify_snapshot = verify_snapshot; module.SnapshotInputs = SnapshotInputs
    monkeypatch.setitem(sys.modules, "qte.gui.job_inputs", module)
    return module


def _wait(manager, job_id, state, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = manager.get(job_id)
        if value["state"] == state: return value
        if value["state"] in {"succeeded", "failed", "canceled", "interrupted"}:
            raise AssertionError(f"job became {value['state']} instead of {state}: {value}")
        time.sleep(0.01)
    raise AssertionError(f"job did not become {state}: {manager.get(job_id)}")


def test_idempotency_fifo_success_metadata_and_sanitized_log(tmp_path, inputs_module):
    FakeSupervisor.gate = threading.Event(); FakeSupervisor.order = []
    catalog = Catalog()
    manager = JobManager(tmp_path, catalog, supervisor_factory=FakeSupervisor)
    first = manager.submit("request-one", "research_run", Prepared(), "same_key_12345678", 30)
    duplicate = manager.submit("request-two", "research_run", Prepared(), "same_key_12345678", 30)
    second = manager.submit("request-three", "research_run", Prepared(data_sha256="e" * 64),
                            "other_key_1234567", 30)
    assert duplicate["id"] == first["id"] and second["state"] == "queued"
    with pytest.raises(JobError) as conflict:
        manager.submit("request", "research_run", Prepared(data_sha256="f" * 64), "same_key_12345678", 30)
    assert conflict.value.code == "idempotency_conflict"
    FakeSupervisor.gate.set()
    assert _wait(manager, first["id"], "succeeded")["output_path"].endswith("/artifacts")
    assert _wait(manager, second["id"], "succeeded")["input_disclosure"] == "not_required"
    assert FakeSupervisor.order == [first["id"], second["id"]]
    assert [item["id"] for item in manager.list(0, 25)[0]] == [second["id"], first["id"]]
    log = manager.log(first["id"], 0, 65536)
    assert log["text"] == "accepted\nrunning\nverified output complete\n"
    assert "safe" not in log["text"] and log["next_offset"] == log["total"]
    assert catalog.protected[0][0] == first["output_path"]
    manager.close()


def test_queue_capacity_and_cancel_before_start(tmp_path, inputs_module):
    FakeSupervisor.gate = threading.Event(); FakeSupervisor.order = []
    manager = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)
    jobs = [manager.submit("request-0", "research_run", Prepared(data_sha256=f"{0:064x}"),
                           f"queue_key_{0:016d}", 30)]
    _wait(manager, jobs[0]["id"], "running")
    jobs.extend(manager.submit(f"request-{index}", "research_run",
                Prepared(data_sha256=f"{index:064x}"), f"queue_key_{index:016d}", 30)
                for index in range(1, 21))
    with pytest.raises(JobError) as full:
        manager.submit("overflow", "research_run", Prepared(data_sha256="f" * 64),
                       "overflow_key_123456", 30)
    assert full.value.code == "job_queue_full"
    canceled = manager.cancel(jobs[-1]["id"])
    assert canceled["state"] == "canceled" and canceled["start_ns"] is None
    FakeSupervisor.gate.set()
    manager.close()


def test_close_cancels_active_job_and_recovery_interrupts_queued_job(tmp_path, inputs_module):
    FakeSupervisor.gate = threading.Event(); FakeSupervisor.order = []
    manager = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)
    active = manager.submit("active-request", "research_run", Prepared(), "active_close_key_1", 30)
    _wait(manager, active["id"], "running")
    queued = manager.submit("queued-request", "research_run", Prepared(data_sha256="e" * 64),
                            "queued_close_key_1", 30)
    manager.close()
    # This manager is intentionally closed, so inspect the durable outcome
    # through a fresh owner. It must never resurrect the accepted queued work.
    recovered = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)
    assert recovered.get(active["id"])["state"] == "canceled"
    queued_record = recovered.get(queued["id"])
    assert (queued_record["state"], queued_record["reason"]) == ("interrupted", "owner_restart")
    assert FakeSupervisor.order == [active["id"]]
    recovered.close()


def test_verified_completion_wins_a_simultaneous_manager_cancel(tmp_path, inputs_module):
    class CompletionWinsRunning(FakeRunning):
        def cancel(self):
            # The supervisor has already observed a valid completion; a late
            # cancellation must not overwrite that verified terminal result.
            self.gate.set()

    class CompletionWinsSupervisor(FakeSupervisor):
        def launch(self, spec_path, environment):
            self.order.append(json.loads(spec_path.read_text())["job_id"])
            return CompletionWinsRunning(self.gate, self.order)

    CompletionWinsSupervisor.gate = threading.Event(); CompletionWinsSupervisor.order = []
    manager = JobManager(tmp_path, Catalog(), supervisor_factory=CompletionWinsSupervisor)
    job = manager.submit("completion-race", "research_run", Prepared(), "completion_race_key", 30)
    _wait(manager, job["id"], "running")
    manager.cancel(job["id"])
    assert _wait(manager, job["id"], "succeeded")["reason"] is None
    manager.close()


def test_snapshot_mutation_fails_before_supervisor_launch(tmp_path, monkeypatch):
    from qte.gui import job_inputs

    examples = tmp_path / "examples"; examples.mkdir()
    (examples / "bars.csv").write_text("symbol,timestamp,open,high,low,close,volume\nSPY,1,1,1,1,1,1\n")
    (examples / "run.json").write_text(json.dumps({
        "data": {"adapter": "csv", "path": "bars.csv", "interval_ns": 1},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY", "fast_period": 1,
                     "slow_period": 2, "quantity": 1}, "engine": {"initial_cash": 1},
    }))
    prepared = job_inputs.collect_inputs(tmp_path, "examples/run.json")
    original_write = job_inputs.write_snapshot

    def tampering_write(repository, job_id, value):
        snapshot = original_write(repository, job_id, value)
        directory = repository / snapshot.input_root
        target = directory / "effective-config.json"
        os.chmod(directory, 0o700); os.chmod(target, 0o600)
        target.write_text("{}")
        os.chmod(target, 0o400); os.chmod(directory, 0o500)
        return snapshot

    monkeypatch.setattr(job_inputs, "write_snapshot", tampering_write)

    class CheckingSupervisor(FakeSupervisor):
        def launch(self, spec_path, environment):
            raise AssertionError("snapshot verification must run before launch")

    manager = JobManager(tmp_path, Catalog(), supervisor_factory=CheckingSupervisor)
    job = manager.submit("request", "research_run", prepared, "mutation_key_12345", 30)
    failed = _wait(manager, job["id"], "failed")
    assert failed["reason"] == "snapshot_invalid"
    assert not (tmp_path / failed["output_path"]).exists()
    manager.close()


def test_restart_recovery_marks_durable_nonterminal_rows_interrupted(tmp_path, inputs_module):
    FakeSupervisor.gate = threading.Event(); FakeSupervisor.order = []
    first = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)
    first._ensure_storage()
    now = time.time_ns()
    with first._connect() as db:
        first._schema(db)
        db.execute("""INSERT INTO gui_jobs
          (id,idempotency_key,fingerprint,request_id,kind,state,phase,reason,created_ns,start_ns,end_ns,
           timeout_seconds,source_identity,input_hashes,lineage,snapshot,launch_identity,supervisor_pid,
           executable,cwd,exit_code,output_path,log) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("a" * 32, "restart_key_1234", "f" * 64, "request", "research_run", "queued", "queued", None,
           now, None, None, 30, "source", "{}", "{}", "{}", None, None, None, None, None,
           "build/gui/jobs/" + "a" * 32 + "/artifacts", "accepted\n"))
    # Simulate a dead owner after durable acceptance and before dispatcher wakeup.
    second = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)
    recovered = second.get("a" * 32)
    assert recovered["state"] == "interrupted" and recovered["reason"] == "owner_restart"
    assert FakeSupervisor.order == []
    second.close()


def test_connect_revalidates_sqlite_sidecars_before_each_open(tmp_path, inputs_module):
    manager = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)
    manager._ensure_storage()
    with manager._connect() as db:
        manager._schema(db)
    target = tmp_path / "sidecar-target"
    target.write_text("not a database sidecar")
    os.symlink(target, f"{manager.database}-wal")
    with pytest.raises(JobError) as error:
        manager._connect()
    assert (error.value.code, error.value.status) == ("jobs_forbidden", 403)


def test_open_releases_lock_after_schema_failure_and_close_race(tmp_path, inputs_module, monkeypatch):
    manager = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)

    def broken_schema(_db):
        raise RuntimeError("controlled schema failure")

    monkeypatch.setattr(manager, "_schema", broken_schema)
    with pytest.raises(RuntimeError):
        manager.get("a" * 32)
    assert manager._opened is False and manager._lock_fd is None
    replacement = JobManager(tmp_path, Catalog(), supervisor_factory=FakeSupervisor)
    replacement._open()
    replacement.close()

    closing_root = tmp_path / "closing"; closing_root.mkdir()
    closing = JobManager(closing_root, Catalog(), supervisor_factory=FakeSupervisor)
    original_schema = closing._schema
    def mark_closing(db):
        original_schema(db)
        closing._closing = True
    monkeypatch.setattr(closing, "_schema", mark_closing)
    with pytest.raises(JobError) as error:
        closing.get("a" * 32)
    assert error.value.code == "jobs_unavailable"
    assert closing._opened is False and closing._lock_fd is None
    with pytest.raises(JobError) as error:
        closing.submit("request", "research_run", Prepared(), "closed_key_123456", 30)
    assert error.value.code == "jobs_unavailable"


def test_manager_rejects_malformed_or_nonzero_success_worker_result(tmp_path, inputs_module):
    class InvalidSupervisor(FakeSupervisor):
        def launch(self, spec_path, environment):
            gate = threading.Event(); gate.set()
            return FakeRunning(gate, [], {"state": "succeeded", "reason": None, "exit_code": 9})

    manager = JobManager(tmp_path, Catalog(), supervisor_factory=InvalidSupervisor)
    job = manager.submit("request", "research_run", Prepared(), "protocol_key_12345", 30)
    failed = _wait(manager, job["id"], "failed")
    assert failed["reason"] == "worker_protocol_error" and failed["exit_code"] is None
    manager.close()


@pytest.mark.parametrize("value", [
    {"state": "failed", "reason": "source contents leaked here", "exit_code": 1},
    {"state": "canceled", "reason": "canceled", "exit_code": True},
    {"state": "interrupted", "reason": {"untrusted": "reason"}, "exit_code": None},
])
def test_worker_completion_protocol_allows_only_fixed_sanitized_values(value):
    with pytest.raises(ValueError):
        _worker_result(value)


def test_late_cancel_or_owner_close_cannot_touch_recycled_descriptor():
    liveness_r, liveness_w = os.pipe()
    cancel_r, cancel_w = os.pipe()
    result_r, result_w = os.pipe()
    os.write(result_w, b'{"state":"succeeded","reason":null,"exit_code":0}')
    os.close(result_w)
    running = RunningJob(FakeProcess(), liveness_w, cancel_w, result_r)
    assert running.wait() == {"state": "succeeded", "reason": None, "exit_code": 0}
    unrelated_r, unrelated_w = os.pipe()
    try:
        # Linux promptly reuses the closed manager descriptors. A stale write
        # or close here would either add a byte or make fstat fail.
        assert (unrelated_r, unrelated_w) == (liveness_w, cancel_w)
        running.cancel()
        running.close_owner()
        assert os.fstat(unrelated_r)
        os.set_blocking(unrelated_r, False)
        with pytest.raises(BlockingIOError):
            os.read(unrelated_r, 1)
    finally:
        for descriptor in (liveness_r, cancel_r, unrelated_r, unrelated_w):
            try: os.close(descriptor)
            except OSError: pass


def test_real_supervisor_runs_fixed_v2_cli_and_verifies_output(tmp_path, monkeypatch):
    from qte.gui.job_inputs import collect_inputs

    examples = tmp_path / "examples"; examples.mkdir()
    (examples / "bars.csv").write_text(
        "symbol,timestamp,open,high,low,close,volume\n"
        "SPY,2024-01-02T14:00:00Z,100,101,99,100,1000\n"
        "SPY,2024-01-02T15:00:00Z,101,102,100,101,1000\n"
        "SPY,2024-01-02T16:00:00Z,102,103,101,102,1000\n"
        "SPY,2024-01-02T17:00:00Z,103,104,102,103,1000\n",
        encoding="utf-8")
    (examples / "run.json").write_text(json.dumps({
        "data": {"adapter": "csv", "path": "bars.csv", "interval_ns": 3_600_000_000_000,
                 "action_free": True},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY",
                     "fast_period": 2, "slow_period": 3, "quantity": 1},
        "engine": {"initial_cash": 1000, "history_capacity": 8, "random_seed": 0},
    }), encoding="utf-8")
    prepared = collect_inputs(tmp_path, "examples/run.json")
    catalog = Catalog(); monkeypatch.setenv("BROKER_SECRET_SENTINEL", "must-not-be-inherited")
    manager = JobManager(tmp_path, catalog)
    job = manager.submit("real-request", "research_run", prepared, "real_worker_key_123", 30)
    terminal = _wait(manager, job["id"], "succeeded", timeout=20)
    output = tmp_path / terminal["output_path"]
    wrapper = json.loads((output / "research_export.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert wrapper["schema_version"] == 2 and wrapper["artifact_kind"] == "research_export"
    assert manifest["source_identity"] == terminal["source_identity"]
    assert terminal["exit_code"] == 0 and catalog.protected[-1][0] == terminal["output_path"]
    assert not (output.parent / "BROKER_SECRET_SENTINEL").exists()
    manager.close()
