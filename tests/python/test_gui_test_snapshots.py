"""Pinned test requests stay inert; dispatch separately rechecks real identities."""
from hashlib import sha256
import json
import os

import pytest

from qte.gui import test_snapshots as module
from qte.gui.test_preflight import validate_prepared
from qte.gui.test_snapshots import (
    TestSnapshotError, load_test_snapshot, recheck_test_snapshot, write_test_snapshot,
)


JOB = "a" * 32


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    value = validate_prepared({
        "schema_version": 1, "runner": "pytest", "repository_path": str(tmp_path),
        "identity_scope": "explicit_files_v1", "source_to_binary": "unknown",
        "identity": {group: {name: "a" * 64} for group, name in (
            ("source", "python/qte/__init__.py"), ("tests", "tests/python/test_one.py"),
            ("binaries", ".venv/lib/python3.14/site-packages/qte/_core.so"),
            ("build", "pyproject.toml"))},
        "versions": {"python": "3.14", "pytest": "9.1", "ctest": "4.2", "platform": "Linux"},
        "ctest_tests": [],
    })
    # Producer discovery has dedicated tests. No recursive suite is ever run.
    monkeypatch.setattr(module, "recheck_prepared", lambda repository, item: validate_prepared(item))
    return value


def unlock(root):
    os.chmod(root, 0o700)
    for path in root.iterdir():
        os.chmod(path, 0o600)


def test_snapshot_roundtrip_is_private_owned_and_never_executes(tmp_path, prepared, monkeypatch):
    snapshot = write_test_snapshot(tmp_path, JOB, prepared)
    root = tmp_path / snapshot.input_root
    assert not root.stat().st_mode & 0o222
    assert all(not path.stat().st_mode & 0o222 for path in root.iterdir())
    assert not (root.parent / "artifacts").exists()
    hashes = snapshot.snapshot_hashes
    hashes.clear()
    assert len(snapshot.snapshot_hashes) == 2
    monkeypatch.setattr(module, "recheck_prepared", lambda *args: pytest.fail("inert load ran discovery"))
    loaded, restored = load_test_snapshot(tmp_path, JOB, snapshot.snapshot_hashes)
    assert loaded == snapshot and restored == prepared
    value = restored.value
    value["identity"]["source"].clear()
    assert restored.value == prepared.value
    assert set(path.name for path in root.iterdir()) == {"test-request.json", "input-manifest.json"}
    monkeypatch.setattr(module, "recheck_prepared", lambda repository, item: item)
    with pytest.raises(TestSnapshotError):
        write_test_snapshot(tmp_path, JOB, prepared)
    assert load_test_snapshot(tmp_path, JOB, snapshot.snapshot_hashes) == (snapshot, prepared)


@pytest.mark.parametrize("job_id", ["../escape", "a" * 31, "A" * 32, "", None])
def test_invalid_job_rejected_before_writes(tmp_path, prepared, job_id):
    with pytest.raises(TestSnapshotError):
        write_test_snapshot(tmp_path, job_id, prepared)
    assert not (tmp_path / "build").exists()


def test_changed_preflight_and_other_repository_rejected_before_writes(tmp_path, prepared, monkeypatch):
    def changed(*args):
        raise ValueError("changed source")
    monkeypatch.setattr(module, "recheck_prepared", changed)
    with pytest.raises(TestSnapshotError):
        write_test_snapshot(tmp_path, JOB, prepared)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(TestSnapshotError):
        write_test_snapshot(other, JOB, prepared)
    assert not (tmp_path / "build").exists() and not (other / "build").exists()


@pytest.mark.parametrize("change", ["extra", "missing", "tamper", "symlink", "fifo",
                                     "bad_inventory", "bad_hash", "schema", "job", "runner"])
def test_snapshot_tamper_and_unsupported_inventory_fail_closed(tmp_path, prepared, change):
    snapshot = write_test_snapshot(tmp_path, JOB, prepared)
    root = tmp_path / snapshot.input_root
    unlock(root)
    hashes = snapshot.snapshot_hashes
    request = root / "test-request.json"
    if change == "extra": (root / "extra").write_bytes(b"x")
    elif change == "missing": request.unlink()
    elif change == "tamper": request.write_bytes(b"{}")
    elif change == "symlink":
        request.unlink()
        request.symlink_to(root / "input-manifest.json")
    elif change == "fifo":
        request.unlink()
        os.mkfifo(request)
    elif change == "bad_inventory": hashes["../escape"] = "0" * 64
    elif change == "bad_hash": hashes["test-request.json"] = "g" * 64
    else:
        path = root / "input-manifest.json"
        value = json.loads(path.read_bytes())
        value[{"schema": "schema_version", "job": "job_id", "runner": "runner"}[change]] = (
            1 if change == "schema" else "b" * 32 if change == "job" else "ctest")
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        hashes["input-manifest.json"] = sha256(path.read_bytes()).hexdigest()
    with pytest.raises(TestSnapshotError):
        load_test_snapshot(tmp_path, JOB, hashes)


def test_partial_write_preserved_and_never_reused(tmp_path, prepared, monkeypatch):
    original = module._write_exclusive
    def fail(directory, name, raw):
        if name == "input-manifest.json": raise OSError("disk full")
        original(directory, name, raw)
    monkeypatch.setattr(module, "_write_exclusive", fail)
    with pytest.raises(TestSnapshotError):
        write_test_snapshot(tmp_path, JOB, prepared)
    root = tmp_path / f"build/gui/jobs/{JOB}/inputs"
    assert (root / "test-request.json").read_bytes() == prepared.payload
    assert not (root / "input-manifest.json").exists()
    monkeypatch.setattr(module, "_write_exclusive", original)
    with pytest.raises(TestSnapshotError):
        write_test_snapshot(tmp_path, JOB, prepared)


def test_snapshot_detects_file_replaced_during_read(tmp_path, prepared, monkeypatch):
    snapshot = write_test_snapshot(tmp_path, JOB, prepared)
    root = tmp_path / snapshot.input_root
    unlock(root)
    original = module._read
    def replacing(directory, name):
        result = original(directory, name)
        if name == "test-request.json":
            replacement = root / "replacement"
            replacement.write_bytes(result[0])
            replacement.replace(root / name)
        return result
    monkeypatch.setattr(module, "_read", replacing)
    with pytest.raises(TestSnapshotError):
        load_test_snapshot(tmp_path, JOB, snapshot.snapshot_hashes)


def test_snapshot_detects_symlinked_parent(tmp_path, prepared):
    snapshot = write_test_snapshot(tmp_path, JOB, prepared)
    root = tmp_path / snapshot.input_root
    replacement = root.parent / "moved"
    root.rename(replacement)
    root.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(TestSnapshotError):
        load_test_snapshot(tmp_path, JOB, snapshot.snapshot_hashes)


def test_dispatch_gate_rechecks_identity_and_then_snapshot_again(tmp_path, prepared, monkeypatch):
    snapshot = write_test_snapshot(tmp_path, JOB, prepared)
    assert recheck_test_snapshot(tmp_path, JOB, snapshot.snapshot_hashes) == (snapshot, prepared)
    def changed(repository, item):
        raise ValueError("new test added")
    monkeypatch.setattr(module, "recheck_prepared", changed)
    with pytest.raises(TestSnapshotError):
        recheck_test_snapshot(tmp_path, JOB, snapshot.snapshot_hashes)
    def tamper(repository, item):
        root = tmp_path / snapshot.input_root
        unlock(root)
        (root / "test-request.json").write_bytes(b"{}")
        return item
    monkeypatch.setattr(module, "recheck_prepared", tamper)
    with pytest.raises(TestSnapshotError):
        recheck_test_snapshot(tmp_path, JOB, snapshot.snapshot_hashes)
