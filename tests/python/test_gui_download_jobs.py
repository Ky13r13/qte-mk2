"""Boundary tests for the opt-in Alpaca download job integration."""
from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from qte.gui.jobs import JobError, JobManager
from qte.gui import job_worker
from qte.gui.form_inputs import capture_form_inputs
from qte.gui.inline_inputs import prepare_inline_inputs
from qte.gui.research_forms import config_origin, prepare_form
from test_gui_job_worker import _run_worker, _spec, _verified_snapshot


def _request():
    return {"symbols": ["SPY"], "timeframe": "1Hour", "start": "2024-01-02T14:00:00Z",
            "end": "2024-01-02T17:00:00Z", "feed": "iex", "action_free": True}


def _manager(root: Path, *, enabled: bool) -> JobManager:
    return JobManager(root, SimpleNamespace(), alpaca_download_enabled=enabled)


def test_download_disabled_rejects_before_storage_or_queue(tmp_path):
    manager = _manager(tmp_path, enabled=False)
    with pytest.raises(JobError) as error:
        manager.submit("request", "download_alpaca", None, "download-disabled-01", 30)
    assert error.value.code == "alpaca_download_disabled"
    assert not (tmp_path / ".cache").exists()
    assert not (tmp_path / "build").exists()


def test_download_enabled_requires_both_bounded_credentials_before_storage(monkeypatch, tmp_path):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    manager = _manager(tmp_path, enabled=True)
    assert manager.download_prerequisites() == {"enabled": True, "credentials_present": False}
    with pytest.raises(JobError) as error:
        manager.submit("request", "download_alpaca", None, "download-no-keys-01", 30)
    assert error.value.code == "alpaca_credentials_unavailable"
    assert not (tmp_path / ".cache").exists()
    assert not (tmp_path / "build").exists()


def test_download_launch_keeps_secrets_out_of_worker_spec_and_regular_env(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "key-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret-value")
    captured = {}

    class Supervisor:
        def __init__(self, *_args):
            pass

        def launch(self, spec_path, environment):
            captured["spec"] = Path(spec_path).read_text()
            captured["environment"] = dict(environment)
            return SimpleNamespace(process=SimpleNamespace(pid=19), cancel=lambda: None,
                                   close_owner=lambda: None, wait=lambda: {})

    manager = JobManager(tmp_path, SimpleNamespace(), supervisor_factory=Supervisor,
                         alpaca_download_enabled=True)
    manager._open()
    job_id = "a" * 32
    root = tmp_path / "build/gui/jobs" / job_id
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "effective-config.json").write_text("{}")
    row = {"id": job_id, "kind": "download_alpaca", "source_identity": "source",
           "timeout_seconds": 30}
    snapshot = {"effective_config_path": f"build/gui/jobs/{job_id}/inputs/effective-config.json",
                "snapshot_hashes": {"submitted-form.json": "0" * 64}}
    try:
        manager._launch(row, snapshot)
        assert "APCA_API_KEY_ID" not in captured["spec"]
        assert "APCA_API_SECRET_KEY" not in captured["spec"]
        assert "key-id" not in captured["spec"]
        assert "secret-value" not in captured["spec"]
        assert captured["environment"]["APCA_API_KEY_ID"] == "key-id"
        assert captured["environment"]["APCA_API_SECRET_KEY"] == "secret-value"
        assert "APCA_API_KEY_ID" not in manager._environment(root)
        assert "APCA_API_SECRET_KEY" not in manager._environment(root)
        assert json.loads(captured["spec"])["environment"].keys() == manager._environment(root).keys()
    finally:
        manager.close()


def test_download_worker_routes_only_credential_pair_to_fixed_child(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "key-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret-value")
    spec = _spec(tmp_path, kind="download_alpaca")
    value = json.loads(spec.read_text())
    Path(value["config"]).write_text("{}")
    import qte.download_artifact as artifacts
    monkeypatch.setattr(artifacts, "verify_download_output", lambda _: {
        "manifest": {"artifact_kind": "alpaca_download_v1", "source_identity": "test-source"},
        "configuration": {}, "summary": {}})
    verified = _verified_snapshot("download_alpaca")
    verified = verified.__class__(
        verified.job_id, verified.kind, verified.input_root, verified.effective_config,
        verified.original_config_path, verified.original_data_path, verified.adapter,
        verified.effective_config_path, verified.data_path, verified.data_sha256,
        {"canonical_config_sha256": sha256(b"{}").hexdigest(),
         "effective_config_sha256": sha256(b"{}").hexdigest(), "data_sha256": None},
        verified.snapshot_hashes, verified.preview_fingerprint)
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, spec, tmp_path / "ready", exit_after=.05,
        snapshot=lambda *_: verified)
    code, payload, launch, _ = finish()
    assert (code, payload) == (0, {"state": "succeeded", "reason": None, "exit_code": 0})
    assert launch[0] == [str(Path(__import__("sys").executable).absolute()), "-m", "qte",
                         "download-alpaca-config", "--config", value["config"], "--output", value["output"]]
    assert launch[1]["env"]["APCA_API_KEY_ID"] == "key-id"
    assert launch[1]["env"]["APCA_API_SECRET_KEY"] == "secret-value"
    for descriptor in (parent_w, cancel_w):
        try:
            os.close(descriptor)
        except OSError:
            pass


def test_download_worker_missing_credentials_fails_before_child(monkeypatch, tmp_path):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setattr(job_worker, "_snapshot", lambda *_: _verified_snapshot("download_alpaca"))
    monkeypatch.setattr(job_worker.subprocess, "Popen", lambda *_a, **_k: pytest.fail("download launched"))
    parent_r, parent_w = os.pipe(); cancel_r, cancel_w = os.pipe(); result_r, result_w = os.pipe()
    try:
        assert job_worker.supervise(_spec(tmp_path, kind="download_alpaca"), parent_r, cancel_r, result_w) == 1
        os.close(result_w)
        assert json.loads(os.read(result_r, 4096)) == {
            "state": "failed", "reason": "worker_setup_failed", "exit_code": None}
    finally:
        for descriptor in (parent_r, parent_w, cancel_r, cancel_w, result_r, result_w):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_download_worker_rejects_corrupt_verified_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "key-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret-value")
    spec = _spec(tmp_path, kind="download_alpaca")
    value = json.loads(spec.read_text())
    Path(value["config"]).write_text("{}")
    import qte.download_artifact as artifacts
    monkeypatch.setattr(artifacts, "verify_download_output", lambda _: {
        "manifest": {"artifact_kind": "alpaca_download_v1", "source_identity": "test-source"},
        "configuration": {"corrupt": True}, "summary": {}})
    verified = _verified_snapshot("download_alpaca")
    verified = verified.__class__(
        verified.job_id, verified.kind, verified.input_root, verified.effective_config,
        verified.original_config_path, verified.original_data_path, verified.adapter,
        verified.effective_config_path, verified.data_path, verified.data_sha256,
        {"canonical_config_sha256": sha256(b"{}").hexdigest(),
         "effective_config_sha256": sha256(b"{}").hexdigest(), "data_sha256": None},
        verified.snapshot_hashes, verified.preview_fingerprint)
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, spec, tmp_path / "corrupt", exit_after=.05, snapshot=lambda *_: verified)
    code, payload, _, _ = finish()
    assert (code, payload) == (1, {"state": "failed", "reason": "execution_failed", "exit_code": 0})
    for descriptor in (parent_w, cancel_w):
        try:
            os.close(descriptor)
        except OSError:
            pass


def test_download_worker_cancellation_is_inherited(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "key-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret-value")
    spec = _spec(tmp_path, kind="download_alpaca")
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, spec, tmp_path / "cancel", snapshot=lambda *_: _verified_snapshot("download_alpaca"))
    os.write(cancel_w, b"x")
    code, payload, launch, _ = finish()
    assert code == 1 and payload["state"] == payload["reason"] == "canceled"
    assert launch[0][2:4] == ["qte", "download-alpaca-config"]
    for descriptor in (parent_w, cancel_w):
        try:
            os.close(descriptor)
        except OSError:
            pass


def test_queued_download_rechecks_revoked_credentials_before_launch(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "key-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret-value")
    captured = capture_form_inputs(tmp_path, "download_alpaca", config_origin(), _request())
    preview = prepare_form("download_alpaca", _request(), None)
    prepared = prepare_inline_inputs(captured, preview)
    launches = []

    class Supervisor:
        def __init__(self, *_args):
            pass
        def launch(self, *_args):
            launches.append(True)
            pytest.fail("revoked credentials launched a child")

    calls = 0
    def authorize(_prepared):
        nonlocal calls
        calls += 1
        if calls == 2:
            monkeypatch.delenv("APCA_API_KEY_ID")
        return {"input_identities": [], "protected_input_identities": [], "input_disclosure": "not_required"}

    manager = JobManager(tmp_path, SimpleNamespace(), input_authorizer=authorize,
                         output_protector=lambda *_: None, supervisor_factory=Supervisor,
                         alpaca_download_enabled=True)
    try:
        job = manager.submit("request", "download_alpaca", prepared, "download-revoked-01", 30)
        deadline = time.monotonic() + 3
        while manager.get(job["id"])["state"] == "queued" and time.monotonic() < deadline:
            time.sleep(.01)
        finished = manager.get(job["id"])
        assert (finished["state"], finished["reason"]) == ("failed", "alpaca_credentials_unavailable")
        assert not launches
    finally:
        manager.close()
