"""Dataset-validation GUI jobs retain captured input identity through the worker."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import pytest

from qte.data_validation import verify_dataset_output, write_dataset_output
from qte.gui.form_inputs import capture_form_inputs
from qte.gui.inline_inputs import prepare_inline_inputs
from qte.gui.job_inputs import verify_snapshot, write_snapshot
from qte.gui.research_forms import config_origin, prepare_form
from qte.gui import job_worker
from qte.provenance import source_identity


ROOT = Path(__file__).resolve().parents[2]


def _wire() -> dict:
    return {"data": {"adapter": "csv", "path": "data/bars.csv", "interval_ns": "3600000000000",
                     "action_free": True}}


def _spec(repository: Path, job_id: str, snapshot) -> Path:
    root = repository / "build/gui/jobs" / job_id
    for name in ("tmp", "cache"):
        (root / name).mkdir(exist_ok=True)
    value = {
        "repository": repository.as_posix(), "executable": Path(sys.executable).absolute().as_posix(),
        "config": (repository / snapshot.effective_config_path).as_posix(),
        "output": (root / "artifacts").as_posix(), "job_id": job_id, "kind": "dataset_validate",
        "snapshot_hashes": snapshot.snapshot_hashes, "source_identity": source_identity(), "timeout_seconds": 30,
        "environment": {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": (root / "tmp").as_posix(),
                        "XDG_CACHE_HOME": (root / "cache").as_posix()},
    }
    path = root / "worker.json"
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _result(fd: int) -> dict:
    raw = os.read(fd, 4096)
    os.close(fd)
    return json.loads(raw)


def test_dataset_validation_worker_uses_captured_snapshot_and_rejects_rehashed_tampering(tmp_path):
    (tmp_path / "data").mkdir()
    original = (ROOT / "examples/research-bars.csv").read_bytes()
    (tmp_path / "data/bars.csv").write_bytes(original)
    captured = capture_form_inputs(tmp_path, "dataset_validate", config_origin(), _wire())
    preview = prepare_form("dataset_validate", json.loads(captured.configuration_bytes), captured.data_bytes)
    prepared = prepare_inline_inputs(captured, preview)
    job_id = "d" * 32
    snapshot = write_snapshot(tmp_path, job_id, prepared)
    assert snapshot.kind == "dataset_validate"
    assert snapshot.data_sha256 == sha256(original).hexdigest()
    assert verify_snapshot(tmp_path, job_id, snapshot.snapshot_hashes) == snapshot

    # The job must consume only its immutable input copy, never the source path.
    (tmp_path / "data/bars.csv").write_bytes(b"changed after capture")
    spec = _spec(tmp_path, job_id, snapshot)
    parent_r, parent_w = os.pipe()
    cancel_r, cancel_w = os.pipe()
    result_r, result_w = os.pipe()
    try:
        assert job_worker.supervise(spec, parent_r, cancel_r, result_w) == 0
    finally:
        for fd in (parent_r, parent_w, cancel_r, cancel_w, result_w):
            try:
                os.close(fd)
            except OSError:
                pass
    assert _result(result_r) == {"state": "succeeded", "reason": None, "exit_code": 0}

    output = tmp_path / "build/gui/jobs" / job_id / "artifacts"
    direct = tmp_path / "direct"
    write_dataset_output(tmp_path / snapshot.effective_config_path, direct)
    assert {item.name: item.read_bytes() for item in output.iterdir()} == {
        item.name: item.read_bytes() for item in direct.iterdir()}
    checked = verify_dataset_output(output)
    assert checked["manifest"]["source_identity"] == source_identity()
    assert checked["manifest"]["data_sha256"] == sha256(original).hexdigest()

    # Rehashing a semantic modification cannot make an artifact acceptable.
    summary = output / "summary.json"
    changed = json.loads(summary.read_text(encoding="utf-8"))
    changed["bar_count"] += 1
    summary.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    complete = output / "complete.json"
    completion = json.loads(complete.read_text(encoding="utf-8"))
    completion["sha256"]["summary.json"] = sha256(summary.read_bytes()).hexdigest()
    complete.write_text(json.dumps(completion, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_dataset_output(output)
