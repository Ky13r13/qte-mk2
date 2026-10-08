"""Kind-aware inline manager/worker seams leave legacy research behavior intact."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from qte.gui.form_inputs import capture_form_inputs
from qte.gui.inline_inputs import prepare_inline_inputs
from qte.gui.jobs import JobError, JobManager
from qte.gui.research_forms import config_origin, prepare_form
from qte.gui import job_worker

from test_gui_inline_inputs import lab_wire, repository, research_wire
from test_gui_job_worker import _run_worker, _spec


class _Process:
    pid = 12345


class _Running:
    def __init__(self, gate):
        self.process, self.gate = _Process(), gate

    def wait(self):
        self.gate.wait(3)
        return {"state": "succeeded", "reason": None, "exit_code": 0}

    def cancel(self):
        self.gate.set()

    def close_owner(self):
        pass


class _Supervisor:
    gate = threading.Event()
    specs: list[dict] = []

    def __init__(self, *_args):
        pass

    def launch(self, spec_path, _environment):
        self.specs.append(json.loads(spec_path.read_text(encoding="utf-8")))
        return _Running(self.gate)


class _Catalog:
    def authorize_job_inputs(self, prepared):
        return {"input_identities": [prepared.config_sha256, prepared.data_sha256],
                "protected_input_identities": [], "input_disclosure": "not_required"}

    def protect_job_output(self, *_args):
        pass


def _prepared(repository: Path, kind: str):
    wire = research_wire() if kind == "research_run" else lab_wire()
    captured = capture_form_inputs(repository, kind, config_origin(), wire)
    preview = prepare_form(kind, json.loads(captured.configuration_bytes), captured.data_bytes)
    return prepare_inline_inputs(captured, preview)


def _wait(manager, job_id, state):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = manager.get(job_id)
        if result["state"] == state:
            return result
        time.sleep(.01)
    raise AssertionError(manager.get(job_id))


@pytest.mark.parametrize("kind", ["research_run", "strategy_lab"])
def test_manager_accepts_matching_inline_kind_and_persists_fixed_worker_kind(repository, kind):
    _Supervisor.gate = threading.Event(); _Supervisor.specs = []
    manager = JobManager(repository, _Catalog(), supervisor_factory=_Supervisor)
    job = manager.submit("inline-request", kind, _prepared(repository, kind), f"inline_{kind}_key", 30)
    _wait(manager, job["id"], "running")
    assert _Supervisor.specs[0]["kind"] == kind
    _Supervisor.gate.set()
    assert _wait(manager, job["id"], "succeeded")["reason"] is None
    manager.close()


def test_manager_rejects_crossed_inline_kind_and_path_style_lab(repository):
    _Supervisor.gate = threading.Event(); _Supervisor.specs = []
    manager = JobManager(repository, _Catalog(), supervisor_factory=_Supervisor)
    with pytest.raises(JobError) as error:
        manager.submit("crossed", "strategy_lab", _prepared(repository, "research_run"), "crossed_inline_key", 30)
    assert error.value.code == "invalid_job_request"
    with pytest.raises(JobError) as error:
        manager.submit("legacy-lab", "strategy_lab", object(), "legacy_lab_inline_key", 30)
    assert error.value.code == "invalid_job_request"
    assert not (repository / "build/gui/jobs").exists()
    manager.close()


def test_worker_uses_lab_argv_and_strict_lab_verifier(monkeypatch, tmp_path):
    verified = SimpleNamespace(kind="strategy_lab", hashes={"canonical_config_sha256": "a" * 64})
    calls = []
    import qte.gui.job_outputs as outputs
    monkeypatch.setattr(outputs, "verify_fixed_lab_output", lambda output, source, canonical: calls.append(
        (output, source, canonical)) or True)
    spec = _spec(tmp_path, kind="strategy_lab")
    value = json.loads(spec.read_text())
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, spec, tmp_path / "ready", exit_after=.05,
        snapshot=lambda *_: verified)
    code, payload, launch, _ = finish()
    assert code == 0 and payload == {"state": "succeeded", "reason": None, "exit_code": 0}
    assert launch[0] == [str(Path(sys.executable).absolute()), "-m", "qte", "lab", "--config",
                         value["config"], "--output", value["output"]]
    assert calls == [(Path(value["output"]), "test-source", "a" * 64)]
    for descriptor in (parent_w, cancel_w):
        try:
            os.close(descriptor)
        except OSError:
            pass


def test_worker_rejects_lab_snapshot_kind_mismatch_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(job_worker, "_snapshot", lambda *_: SimpleNamespace(
        kind="research_run", hashes={"canonical_config_sha256": "a" * 64}))
    monkeypatch.setattr(job_worker.subprocess, "Popen", lambda *_a, **_k: pytest.fail("mismatch launched"))
    parent_r, parent_w = os.pipe(); cancel_r, cancel_w = os.pipe(); result_r, result_w = os.pipe()
    try:
        assert job_worker.supervise(_spec(tmp_path, kind="strategy_lab"), parent_r, cancel_r, result_w) == 1
        os.close(result_w)
        assert json.loads(os.read(result_r, 4096)) == {
            "state": "failed", "reason": "provenance_changed", "exit_code": None}
    finally:
        for descriptor in (parent_r, parent_w, cancel_r, cancel_w, result_r, result_w):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_lab_completion_requires_strict_verifier_and_cancel_remains_canceled(monkeypatch, tmp_path):
    verified = SimpleNamespace(kind="strategy_lab", hashes={"canonical_config_sha256": "a" * 64})
    import qte.gui.job_outputs as outputs
    monkeypatch.setattr(outputs, "verify_fixed_lab_output", lambda *_: False)
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, _spec(tmp_path, kind="strategy_lab"), tmp_path / "complete", exit_after=.05,
        snapshot=lambda *_: verified)
    code, payload, _, _ = finish()
    assert (code, payload) == (1, {"state": "failed", "reason": "execution_failed", "exit_code": 0})
    for descriptor in (parent_w, cancel_w):
        try:
            os.close(descriptor)
        except OSError:
            pass

    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, _spec(tmp_path / "cancel", kind="strategy_lab"), tmp_path / "cancel-ready",
        snapshot=lambda *_: verified)
    os.write(cancel_w, b"x")
    code, payload, launch, _ = finish()
    assert code == 1 and payload["state"] == payload["reason"] == "canceled"
    assert launch[0][2:4] == ["qte", "lab"]
    for descriptor in (parent_w, cancel_w):
        try:
            os.close(descriptor)
        except OSError:
            pass
