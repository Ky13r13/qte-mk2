"""Fixed tests share the durable queue without acquiring research privileges."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from qte.gui.catalog import Catalog
from qte.gui.artifacts import ArtifactError
from qte.gui.jobs import JobError, JobManager
from qte.gui.test_evidence import fixed_test_command, read_test_evidence
from qte.gui.test_evidence_writer import finalize_test_evidence
from qte.gui.test_preflight import validate_prepared
from qte.gui import test_snapshots
from test_gui_test_evidence import xml
from test_gui_test_preflight import _value


def wait(manager, job_id, state):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["state"] == state:
            return job
        if job["state"] in {"succeeded", "failed", "canceled", "interrupted"}:
            raise AssertionError(job)
        time.sleep(0.01)
    raise AssertionError(manager.get(job_id))


def evidence(repository, spec, mode):
    value = json.loads((repository / f'build/gui/jobs/{spec["job_id"]}/inputs/test-request.json').read_bytes())
    output = Path(spec["output"]); output.mkdir()
    junit = (xml('<testcase name="failure" classname="fixture"><failure /></testcase>', failures=1)
             if mode == "fail" else
             xml('<testcase name="skip" classname="fixture"><skipped /></testcase>', skipped=1)
             if mode == "skipped" else xml())
    (output / "junit.xml").write_bytes(junit)
    identity = deepcopy(value["identity"])
    if mode == "mismatch":
        identity["source"][next(iter(identity["source"]))] = "f" * 64
    manifest = {"schema_version": 1, "artifact_kind": "test_evidence_v1", "runner": value["runner"],
        "job_id": spec["job_id"], "repository_path": str(repository), "cwd": str(repository),
        "argv": list(fixed_test_command(str(repository), value["runner"], spec["job_id"])),
        "started_ns": "1", "finished_ns": "2", "exit_code": 1 if mode == "fail" else 0,
        "termination": "exited", "versions": value["versions"], "identity_scope": "explicit_files_v1",
        "start_identity": identity, "end_identity": deepcopy(identity), "source_to_binary": "unknown",
        "log_truncated": False}
    return finalize_test_evidence(repository, spec["job_id"], manifest, b"private testcase sentinel")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    value = _value(); value["repository_path"] = str(tmp_path)
    prepared = validate_prepared(value)
    monkeypatch.setattr(test_snapshots, "recheck_prepared", lambda repository, item: validate_prepared(item))
    monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv/bin/python"))
    launched = []
    gate = threading.Event()
    mode = ["pass"]

    class Running:
        process = SimpleNamespace(pid=424242)
        canceled = False
        def __init__(self, spec): self.spec = spec
        def cancel(self): self.canceled = True; gate.set()
        def close_owner(self): pass
        def wait(self):
            assert gate.wait(5)
            if self.canceled:
                return {"state": "canceled", "reason": "canceled", "exit_code": None}
            if mode[0] != "lie": evidence(tmp_path, self.spec, mode[0])
            failed = mode[0] in {"fail", "skipped"}
            return {"state": "failed" if failed else "succeeded",
                    "reason": "execution_failed" if failed else None,
                    "exit_code": 1 if mode[0] == "fail" else 0}

    class Supervisor:
        def __init__(self, repository, lock_fd): assert repository == tmp_path and lock_fd >= 0
        def launch(self, path, environment):
            spec = json.loads(path.read_bytes())
            launched.append((spec, environment))
            return Running(spec)

    def forbidden(*args): pytest.fail("test job acquired research authorization/publication")
    manager = JobManager(tmp_path, SimpleNamespace(), supervisor_factory=Supervisor,
                         input_authorizer=forbidden, output_protector=forbidden)
    yield manager, prepared, gate, mode, launched
    gate.set(); manager.close()


def submit(manager, prepared, key="test_request_123456", timeout=30):
    return manager.submit("request", "test_run", prepared, key, timeout)


def test_fixed_queue_idempotency_and_private_success(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    first = submit(manager, prepared)
    assert submit(manager, prepared)["id"] == first["id"]
    with pytest.raises(JobError) as exc: submit(manager, prepared, timeout=31)
    assert exc.value.code == "idempotency_conflict"
    other = prepared.value; other["identity"]["tests"]["tests/python/new.py"] = "c" * 64
    with pytest.raises(JobError) as exc: submit(manager, validate_prepared(other))
    assert exc.value.code == "idempotency_conflict"
    wait(manager, first["id"], "running")
    second = submit(manager, prepared, "test_request_789012")
    assert second["state"] == "queued" and len(launched) == 1
    gate.set()
    result = wait(manager, first["id"], "succeeded")
    wait(manager, second["id"], "succeeded")
    assert [spec["job_id"] for spec, _ in launched] == [first["id"], second["id"]]
    spec, env = launched[0]
    assert spec["kind"] == "test_run" and spec["config"].endswith("/inputs/test-request.json")
    assert set(spec["snapshot_hashes"]) == {"test-request.json", "input-manifest.json"}
    assert set(env) == {"LANG", "LC_ALL", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "TMPDIR", "XDG_CACHE_HOME"}
    assert result["input_hashes"] == {} and result["source_identity"] is None and result["exit_code"] is None
    assert "private testcase" not in json.dumps(result) + manager.log(first["id"])["text"]
    assert read_test_evidence(tmp_path, result["output_path"]).outcome == "passed"
    with pytest.raises(ArtifactError) as exc: Catalog(tmp_path).register(result["output_path"])
    assert exc.value.code == "job_output_unavailable"


@pytest.mark.parametrize("mode_name", ["fail", "skipped", "lie", "mismatch"])
def test_nonpasses_and_false_success_never_publish(setup, tmp_path, mode_name):
    manager, prepared, gate, mode, launched = setup
    mode[0] = mode_name
    job = submit(manager, prepared); gate.set()
    result = wait(manager, job["id"], "failed")
    assert result["reason"] == "test_job_failed"
    if mode_name in {"fail", "skipped"}:
        record = read_test_evidence(tmp_path, result["output_path"])
        assert record.outcome == ("failed" if mode_name == "fail" else "skipped")
    with pytest.raises(ArtifactError): Catalog(tmp_path).register(result["output_path"])


def test_cancel_queued_test_never_launches(setup):
    manager, prepared, gate, mode, launched = setup
    active = submit(manager, prepared)
    wait(manager, active["id"], "running")
    queued = submit(manager, prepared, "other_test_request_1")
    assert manager.cancel(queued["id"])["state"] == "canceled"
    gate.set(); wait(manager, active["id"], "succeeded")
    assert [spec["job_id"] for spec, _ in launched] == [active["id"]]


def test_changed_snapshot_before_dispatch_is_rejected(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    active = submit(manager, prepared); wait(manager, active["id"], "running")
    queued = submit(manager, prepared, "other_test_request_1")
    path = tmp_path / f'build/gui/jobs/{queued["id"]}/inputs/test-request.json'
    path.chmod(0o600); path.write_bytes(b"{}")
    gate.set(); wait(manager, active["id"], "succeeded")
    wait(manager, queued["id"], "failed")
    assert len(launched) == 1


def test_parent_rechecks_current_identity_after_child_success(setup, monkeypatch):
    manager, prepared, gate, mode, launched = setup
    job = submit(manager, prepared); wait(manager, job["id"], "running")
    def changed(*args): raise ValueError("source changed")
    monkeypatch.setattr(test_snapshots, "recheck_prepared", changed)
    gate.set()
    assert wait(manager, job["id"], "failed")["reason"] == "test_job_failed"


def test_test_request_requires_typed_preflight_before_storage(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    with pytest.raises(JobError) as exc: submit(manager, prepared.value)
    assert exc.value.code == "invalid_job_request"
    assert not (tmp_path / "build/gui/jobs").exists()


def test_existing_job_owner_still_excludes_a_second_manager(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    first = submit(manager, prepared); wait(manager, first["id"], "running")
    other = JobManager(tmp_path, SimpleNamespace())
    try:
        with pytest.raises(JobError) as exc: other.get(first["id"])
        assert exc.value.code == "jobs_owner_active"
    finally:
        other.close()


def test_close_and_restart_never_resumes_queued_test(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    first = submit(manager, prepared); wait(manager, first["id"], "running")
    queued = submit(manager, prepared, "other_test_request_1")
    manager.close()
    other = JobManager(tmp_path, SimpleNamespace())
    try:
        assert other.get(queued["id"])["state"] == "interrupted"
        assert other.get(queued["id"])["reason"] == "owner_restart"
        assert len(launched) == 1
    finally:
        other.close()
