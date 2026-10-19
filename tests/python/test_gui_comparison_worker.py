"""Fixed comparison worker boundaries and generic failure reporting."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from qte.gui import job_worker
from qte.gui.comparison_inputs import PreparedComparisonInputs
from qte.gui.comparison_snapshots import ComparisonSnapshotInputs
from test_gui_job_worker import _run_worker, _spec


def _comparison_values(*, snapshot_source: str = "test-source", prepared_source: str = "test-source"):
    job_id = "a" * 32
    root = f"build/gui/jobs/{job_id}/inputs"
    snapshot = ComparisonSnapshotInputs(
        job_id, "comparison_run", root, f"{root}/effective-config.json", (),
        {"canonical_config_sha256": "a" * 64}, {"input-manifest.json": "0" * 64},
        snapshot_source, "b" * 64,
    )
    prepared = PreparedComparisonInputs(
        "comparison_run", b"{}", b"{}", b"{}", (), prepared_source, "b" * 64,
    )
    return snapshot, prepared


def _close(*descriptors: int) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


def test_comparison_worker_uses_fixed_argv_verifier_and_secret_free_environment(monkeypatch, tmp_path):
    values = _comparison_values()
    monkeypatch.setattr(job_worker, "_comparison_snapshot", lambda *_: values)
    monkeypatch.setenv("APCA_API_KEY_ID", "must-not-be-inherited")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "must-not-be-inherited")
    calls = []
    module = ModuleType("qte.gui.comparison_jobs")
    module.verify_comparison_job_output = lambda *args: calls.append(args) or SimpleNamespace(kind="comparison_export_v1")
    monkeypatch.setitem(sys.modules, "qte.gui.comparison_jobs", module)
    spec = _spec(tmp_path, kind="comparison_run")
    raw = json.loads(spec.read_text(encoding="utf-8"))

    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, spec, tmp_path / "ready", exit_after=.05)
    try:
        code, payload, launch, _ = finish()
        assert (code, payload) == (0, {"state": "succeeded", "reason": None, "exit_code": 0})
        assert launch[0] == [str(Path(sys.executable).absolute()), "-m", "qte", "compare", "--config",
                             raw["config"], "--output", raw["output"]]
        assert launch[1]["env"] == raw["environment"]
        assert calls == [(tmp_path, "a" * 32, raw["snapshot_hashes"], "test-source")]
    finally:
        _close(parent_w, cancel_w)


@pytest.mark.parametrize(("snapshot_source", "prepared_source"), [
    ("changed-source", "test-source"),
    ("test-source", "changed-source"),
])
def test_comparison_worker_checks_both_pinned_source_identities_before_launch(
        monkeypatch, tmp_path, snapshot_source, prepared_source):
    monkeypatch.setattr(job_worker, "_comparison_snapshot", lambda *_: _comparison_values(
        snapshot_source=snapshot_source, prepared_source=prepared_source))
    monkeypatch.setattr(job_worker.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("identity mismatch launched a child"))
    import qte.provenance
    monkeypatch.setattr(qte.provenance, "source_identity", lambda: "test-source")
    parent_r, parent_w = os.pipe(); cancel_r, cancel_w = os.pipe(); result_r, result_w = os.pipe()
    try:
        assert job_worker.supervise(
            _spec(tmp_path, kind="comparison_run"), parent_r, cancel_r, result_w) == 1
        os.close(result_w)
        result_w = -1
        assert json.loads(os.read(result_r, 4096)) == {
            "state": "failed", "reason": "provenance_changed", "exit_code": None}
    finally:
        _close(parent_r, parent_w, cancel_r, cancel_w, result_r, result_w)


def test_comparison_verifier_exception_is_not_exposed(monkeypatch, tmp_path):
    monkeypatch.setattr(job_worker, "_comparison_snapshot", lambda *_: _comparison_values())
    module = ModuleType("qte.gui.comparison_jobs")

    def reject(*_args):
        raise ValueError("private comparison verification detail")

    module.verify_comparison_job_output = reject
    monkeypatch.setitem(sys.modules, "qte.gui.comparison_jobs", module)
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, _spec(tmp_path, kind="comparison_run"), tmp_path / "ready", exit_after=.05)
    try:
        code, payload, _, _ = finish()
        assert (code, payload) == (
            1, {"state": "failed", "reason": "execution_failed", "exit_code": 0})
        assert "private" not in json.dumps(payload)
    finally:
        _close(parent_w, cancel_w)
