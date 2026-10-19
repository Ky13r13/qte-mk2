from __future__ import annotations

import json
import threading
import time

import pytest

from qte.comparison_export import run_comparison
from qte.gui.artifacts import ArtifactError
from qte.gui.comparison_jobs import verify_comparison_job_output
from qte.gui.jobs import JobError, JobManager
from test_gui_comparison_inputs import fixture


class _Process:
    pid = 424242


class _Running:
    def __init__(self, supervisor):
        self.process = _Process()
        self._supervisor = supervisor
        self._result = {"state": "succeeded", "reason": None, "exit_code": 0}

    def wait(self):
        if not self._supervisor.finish.wait(5):
            raise AssertionError("controlled comparison worker was not released")
        return self._result

    def cancel(self):
        self._result = {"state": "canceled", "reason": "canceled", "exit_code": None}
        self._supervisor.cancel_requested.set()

    def close_owner(self):
        self._supervisor.owner_closed.set()


class _Supervisor:
    launched = threading.Event()
    finish = threading.Event()
    cancel_requested = threading.Event()
    owner_closed = threading.Event()
    job_ids: list[str] = []
    specs: dict[str, dict] = {}

    def __init__(self, repository, lock_fd):
        self.repository = repository

    def launch(self, spec_path, environment):
        spec = json.loads(spec_path.read_text())
        type(self).job_ids.append(spec["job_id"])
        type(self).specs[spec["job_id"]] = spec
        type(self).launched.set()
        return _Running(type(self))


def _reset_supervisor():
    _Supervisor.launched = threading.Event()
    _Supervisor.finish = threading.Event()
    _Supervisor.cancel_requested = threading.Event()
    _Supervisor.owner_closed = threading.Event()
    _Supervisor.job_ids = []
    _Supervisor.specs = {}


def _wait(manager, job_id, state, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = manager.get(job_id)
        if value["state"] == state:
            return value
        if value["state"] in {"succeeded", "failed", "canceled", "interrupted"}:
            raise AssertionError(f"job became {value['state']} instead of {state}: {value}")
        threading.Event().wait(0.01)
    raise AssertionError(f"job did not become {state}: {manager.get(job_id)}")


def _prepared(repository):
    from qte.gui.comparison_inputs import prepare_comparison_inputs

    catalog, form = fixture(repository)
    return catalog, prepare_comparison_inputs(catalog, form)


def _change_authorization(catalog, prepared, change):
    if change == "protect_safe":
        catalog.disclosures.mark_protected([prepared.sources[0].identities[0]])
    else:
        catalog.disclosures.reveal([prepared.sources[-1].identities[0]])


def _pause_dispatch_authorization(monkeypatch):
    import qte.gui.comparison_jobs as comparison_jobs

    original = comparison_jobs.load_authorized_comparison
    entered, release = threading.Event(), threading.Event()
    guard, calls = threading.Lock(), 0

    def controlled(*args, **kwargs):
        nonlocal calls
        with guard:
            calls += 1
            current = calls
        if current == 2:
            entered.set()
            if not release.wait(5):
                raise AssertionError("dispatch authorization was not released")
        return original(*args, **kwargs)

    monkeypatch.setattr(comparison_jobs, "load_authorized_comparison", controlled)
    return entered, release


@pytest.mark.parametrize("change", ["protect_safe", "reveal_test"])
def test_authorization_change_after_acceptance_fails_before_dispatch(tmp_path, monkeypatch, change):
    _reset_supervisor()
    catalog, prepared = _prepared(tmp_path)
    entered, release = _pause_dispatch_authorization(monkeypatch)
    manager = JobManager(tmp_path, catalog, supervisor_factory=_Supervisor)
    try:
        job = manager.submit("request", "comparison_run", prepared, f"dispatch_{change}_key", 30)
        assert entered.wait(5)
        _change_authorization(catalog, prepared, change)
        release.set()

        failed = _wait(manager, job["id"], "failed")
        assert failed["reason"] == "comparison_job_failed"
        assert failed["start_ns"] is None
        assert _Supervisor.job_ids == []
    finally:
        release.set()
        manager.close()


@pytest.mark.parametrize("change", ["protect_safe", "reveal_test", "source_drift"])
def test_authorization_or_source_change_after_dispatch_fails_finalization(tmp_path, monkeypatch, change):
    import qte.gui.comparison_jobs as comparison_jobs
    import qte.provenance as provenance

    _reset_supervisor()
    catalog, prepared = _prepared(tmp_path)
    manager = JobManager(tmp_path, catalog, supervisor_factory=_Supervisor)
    try:
        job = manager.submit("request", "comparison_run", prepared, f"finalize_{change}_key", 30)
        assert _Supervisor.launched.wait(5)
        assert manager.get(job["id"])["state"] == "running"
        root = tmp_path / "build/gui/jobs" / job["id"]
        output = run_comparison(root / "inputs/effective-config.json", root / "artifacts")
        spec = _Supervisor.specs[job["id"]]
        artifact = verify_comparison_job_output(
            tmp_path, job["id"], spec["snapshot_hashes"], prepared.source_identity)
        assert artifact.root == output and artifact.kind == "comparison_export_v1"

        if change == "source_drift":
            changed = "sha256:" + "f" * 64
            monkeypatch.setattr(provenance, "source_identity", lambda: changed)
            monkeypatch.setattr(comparison_jobs, "source_identity", lambda: changed)
        else:
            _change_authorization(catalog, prepared, change)
        _Supervisor.finish.set()

        failed = _wait(manager, job["id"], "failed")
        assert failed["reason"] == "comparison_job_failed"
        assert failed["exit_code"] is None
        assert _Supervisor.job_ids == [job["id"]]
        with pytest.raises(ArtifactError) as error:
            catalog.register(failed["output_path"])
        assert error.value.code == "job_output_unavailable"
    finally:
        _Supervisor.finish.set()
        manager.close()


def test_comparison_same_key_deduplicates_and_conflicts(tmp_path):
    _reset_supervisor()
    catalog, prepared = _prepared(tmp_path)
    manager = JobManager(tmp_path, catalog, supervisor_factory=_Supervisor)
    try:
        first = manager.submit("first-request", "comparison_run", prepared, "comparison_same_key", 30)
        duplicate = manager.submit("second-request", "comparison_run", prepared, "comparison_same_key", 30)
        assert duplicate["id"] == first["id"]
        with pytest.raises(JobError) as error:
            manager.submit("conflict-request", "comparison_run", prepared, "comparison_same_key", 31)
        assert (error.value.code, error.value.status) == ("idempotency_conflict", 409)
        assert _Supervisor.launched.wait(5)
        manager.cancel(first["id"])
        _Supervisor.finish.set()
        assert _wait(manager, first["id"], "canceled")["reason"] == "canceled"
        assert _Supervisor.job_ids == [first["id"]]
    finally:
        _Supervisor.finish.set()
        manager.close()


def test_comparison_cancel_then_restart_does_not_resurrect_queued_work(tmp_path):
    _reset_supervisor()
    catalog, prepared = _prepared(tmp_path)
    manager = JobManager(tmp_path, catalog, supervisor_factory=_Supervisor)
    active = manager.submit("active", "comparison_run", prepared, "comparison_active_key", 30)
    assert _Supervisor.launched.wait(5)
    queued = manager.submit("queued", "comparison_run", prepared, "comparison_queued_key", 30)

    manager.cancel(active["id"])
    assert _Supervisor.cancel_requested.wait(5)
    closer = threading.Thread(target=manager.close)
    closer.start()
    assert _Supervisor.owner_closed.wait(5)
    _Supervisor.finish.set()
    closer.join(5)
    assert not closer.is_alive()

    recovered = JobManager(tmp_path, catalog, supervisor_factory=_Supervisor)
    try:
        assert recovered.get(active["id"])["state"] == "canceled"
        value = recovered.get(queued["id"])
        assert (value["state"], value["reason"]) == ("interrupted", "owner_restart")
        assert _Supervisor.job_ids == [active["id"]]
    finally:
        recovered.close()


def test_source_drift_after_acceptance_fails_before_dispatch(tmp_path, monkeypatch):
    import qte.gui.comparison_jobs as comparison_jobs
    import qte.provenance as provenance

    _reset_supervisor()
    catalog, prepared = _prepared(tmp_path)
    entered, release = _pause_dispatch_authorization(monkeypatch)
    manager = JobManager(tmp_path, catalog, supervisor_factory=_Supervisor)
    try:
        job = manager.submit("request", "comparison_run", prepared, "comparison_source_key", 30)
        assert entered.wait(5)
        changed = "sha256:" + "f" * 64
        monkeypatch.setattr(provenance, "source_identity", lambda: changed)
        monkeypatch.setattr(comparison_jobs, "source_identity", lambda: changed)
        release.set()

        failed = _wait(manager, job["id"], "failed")
        assert failed["reason"] == "comparison_job_failed"
        assert failed["start_ns"] is None
        assert _Supervisor.job_ids == []
    finally:
        release.set()
        manager.close()
