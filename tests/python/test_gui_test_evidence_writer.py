"""G8a.2a completion-last writer tests; no runner is launched here."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import stat

import pytest

import qte.gui.test_evidence_writer as writer
from qte.gui.artifact_io import FILE_LIMIT, METADATA_LIMIT
from qte.gui.test_evidence import fixed_test_command, read_test_evidence


JOB_ID = "a" * 32


def _xml(*, failed: bool = False) -> bytes:
    status = '<failure message="fixture" />' if failed else ""
    return (f'<testsuites><testsuite name="fixture" tests="1" failures="{int(failed)}" '
            f'errors="0" skipped="0" disabled="0"><testcase name="case" classname="fixture" '
            f'time="0">{status}</testcase></testsuite></testsuites>').encode()


def _manifest(repository: Path, *, runner: str = "pytest", code: int = 0) -> dict:
    identity = {
        "source": {"python/qte/engine.py": "a" * 64},
        "tests": {"tests/python/test_engine.py": "b" * 64},
        "binaries": {"build/local-dev/tests/qte_test": "c" * 64},
        "build": {"pyproject.toml": "d" * 64},
    }
    root = str(repository)
    return {
        "schema_version": 1, "artifact_kind": "test_evidence_v1", "runner": runner,
        "job_id": JOB_ID, "repository_path": root, "cwd": root,
        "argv": list(fixed_test_command(root, runner, JOB_ID)),
        "started_ns": "1", "finished_ns": "2", "exit_code": code, "termination": "exited",
        "versions": {"python": "3.14", "pytest": "9" if runner == "pytest" else None,
                     "ctest": "4" if runner == "ctest" else None, "platform": "fixture"},
        "identity_scope": "explicit_files_v1", "start_identity": identity,
        "end_identity": deepcopy(identity), "source_to_binary": "unknown", "log_truncated": False,
    }


def _artifacts(tmp_path: Path, junit: bytes | None = None) -> Path:
    root = tmp_path / "build/gui/jobs" / JOB_ID / "artifacts"
    root.mkdir(parents=True)
    if junit is not None:
        (root / "junit.xml").write_bytes(junit)
    return root


def test_writer_persists_exact_complete_evidence_last_with_private_modes(tmp_path):
    root = _artifacts(tmp_path, _xml())
    result = writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"runner output\n")

    assert result.outcome == "passed"
    assert set(path.name for path in root.iterdir()) == {"manifest.json", "junit.xml", "log.txt", "complete.json"}
    assert read_test_evidence(tmp_path, f"build/gui/jobs/{JOB_ID}/artifacts").outcome == "passed"
    assert all(stat.S_IMODE((root / name).stat().st_mode) == 0o600
               for name in ("manifest.json", "log.txt", "complete.json"))


def test_failed_tests_are_completed_evidence_not_a_writer_failure(tmp_path):
    root = _artifacts(tmp_path, _xml(failed=True))
    result = writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path, code=1), b"failure")
    assert result.outcome == "failed"
    assert (root / "complete.json").is_file()


@pytest.mark.parametrize("name", ["manifest.json", "log.txt", "complete.json", "unexpected.txt"])
def test_writer_rejects_any_preexisting_output_without_overwrite(tmp_path, name):
    root = _artifacts(tmp_path, _xml())
    original = b"keep"
    (root / name).write_bytes(original)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"log")
    assert (root / name).read_bytes() == original
    if name != "complete.json":
        assert not (root / "complete.json").exists()


@pytest.mark.parametrize("unsafe", ["file_symlink", "parent_symlink", "fifo"])
def test_writer_rejects_symlinks_and_special_files(tmp_path, unsafe):
    root = _artifacts(tmp_path, _xml())
    if unsafe == "file_symlink":
        (root / "junit.xml").unlink()
        (root / "junit.xml").symlink_to(tmp_path / "outside")
    elif unsafe == "parent_symlink":
        root.rename(tmp_path / "real-artifacts")
        root.symlink_to(tmp_path / "real-artifacts", target_is_directory=True)
    else:
        (root / "junit.xml").unlink()
        os.mkfifo(root / "junit.xml")
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"log")


def test_writer_detects_junit_replacement_after_initial_stable_read(tmp_path, monkeypatch):
    root = _artifacts(tmp_path, _xml())
    original = writer._read_regular
    changed = False

    def mutate(directory_fd, name, limit):
        nonlocal changed
        value = original(directory_fd, name, limit)
        if name == "junit.xml" and not changed:
            changed = True
            target = root / name
            target.unlink()
            target.write_bytes(_xml(failed=True))
        return value

    monkeypatch.setattr(writer, "_read_regular", mutate)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"log")
    assert not (root / "complete.json").exists()
    assert (root / "log.txt").exists() and (root / "manifest.json").exists()


def test_writer_preserves_partial_files_if_a_pre_marker_write_fails(tmp_path, monkeypatch):
    root = _artifacts(tmp_path, _xml())
    original = writer._write_exclusive

    def fail_manifest(directory_fd, name, data):
        if name == "manifest.json":
            raise writer.TestEvidenceWriterError("injected")
        original(directory_fd, name, data)

    monkeypatch.setattr(writer, "_write_exclusive", fail_manifest)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"partial log")
    assert (root / "log.txt").read_bytes() == b"partial log"
    assert not (root / "manifest.json").exists()
    assert not (root / "complete.json").exists()


def test_writer_never_overwrites_completed_evidence(tmp_path):
    root = _artifacts(tmp_path, _xml())
    writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"first")
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"second")
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


@pytest.mark.parametrize("change", ["wrong_job", "wrong_repository", "relocated"])
def test_writer_rejects_wrong_or_relocated_manifest_scope(tmp_path, change):
    root = _artifacts(tmp_path, _xml())
    manifest = _manifest(tmp_path)
    repository = tmp_path
    if change == "wrong_job":
        manifest["job_id"] = "b" * 32
    elif change == "wrong_repository":
        manifest["repository_path"] = str(tmp_path / "other")
    else:
        relocated = tmp_path / "relocated"; relocated.mkdir()
        repository = relocated
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(repository, JOB_ID, manifest, b"log")
    assert not (root / "complete.json").exists()


@pytest.mark.parametrize("kind", ["missing_xml", "invalid_xml", "oversized_xml", "oversized_log", "oversized_metadata"])
def test_writer_rejects_missing_invalid_or_oversized_inputs(tmp_path, kind):
    if kind == "missing_xml":
        root = _artifacts(tmp_path)
    elif kind == "invalid_xml":
        root = _artifacts(tmp_path, b"<broken>")
    elif kind == "oversized_xml":
        root = _artifacts(tmp_path, b"")
        os.truncate(root / "junit.xml", FILE_LIMIT + 1)
    else:
        root = _artifacts(tmp_path, _xml())
    manifest = _manifest(tmp_path)
    log = b"log"
    if kind == "oversized_log":
        log = b"x" * (FILE_LIMIT + 1)
    if kind == "oversized_metadata":
        manifest["extra"] = "x" * METADATA_LIMIT
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, manifest, log)
    assert not (root / "complete.json").exists()


def test_writer_checks_earlier_file_again_after_later_file_read(tmp_path, monkeypatch):
    root = _artifacts(tmp_path, _xml())
    original = writer._read_regular
    def mutate(directory, name, limit):
        result = original(directory, name, limit)
        if name == "log.txt":
            (root / "junit.xml").write_bytes(_xml(failed=True))
        return result
    monkeypatch.setattr(writer, "_read_regular", mutate)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"log")
    assert not (root / "complete.json").exists()


@pytest.mark.parametrize("target", ["junit.xml", "log.txt", "manifest.json"])
def test_writer_rereads_all_producer_bytes_after_marker_write(tmp_path, monkeypatch, target):
    root = _artifacts(tmp_path, _xml())
    original = writer._write_exclusive
    def mutate(directory, name, data):
        original(directory, name, data)
        if name == "complete.json":
            (root / target).write_bytes(b"changed")
    monkeypatch.setattr(writer, "_write_exclusive", mutate)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"log")
    # Preserve evidence of failure; mere marker presence is never enough.
    assert (root / "complete.json").exists()
    with pytest.raises(ValueError):
        read_test_evidence(tmp_path, f"build/gui/jobs/{JOB_ID}/artifacts")


@pytest.mark.parametrize("phase", ["before_marker", "after_marker"])
def test_writer_rejects_directory_detached_from_job_path(tmp_path, monkeypatch, phase):
    root = _artifacts(tmp_path, _xml())
    moved = root.parent / "moved"
    original = writer._write_exclusive
    def relocate(directory, name, data):
        original(directory, name, data)
        if name == ("manifest.json" if phase == "before_marker" else "complete.json"):
            root.rename(moved)
            root.mkdir()
    monkeypatch.setattr(writer, "_write_exclusive", relocate)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"log")
    assert not list(root.iterdir())
    assert (moved / "complete.json").exists() == (phase == "after_marker")


def test_writer_detects_same_bytes_replacement_after_marker(tmp_path, monkeypatch):
    root = _artifacts(tmp_path, _xml())
    original = writer._write_exclusive
    def replace(directory, name, data):
        original(directory, name, data)
        if name == "complete.json":
            path = root / "log.txt"
            value = path.read_bytes()
            path.unlink()
            path.write_bytes(value)
    monkeypatch.setattr(writer, "_write_exclusive", replace)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, _manifest(tmp_path), b"log")


def test_writer_preserves_all_skipped_and_identity_changes(tmp_path):
    xml = b'<testsuites><testsuite name="s" tests="1" failures="0" errors="0" skipped="1"><testcase name="s" classname="s"><skipped /></testcase></testsuite></testsuites>'
    _artifacts(tmp_path, xml)
    manifest = _manifest(tmp_path)
    manifest["end_identity"]["source"]["python/qte/engine.py"] = "f" * 64
    result = writer.finalize_test_evidence(tmp_path, JOB_ID, manifest, b"skipped")
    assert result.outcome == "skipped" and not result.supports_passing_run
    assert dict(result.identity_stability)["source"] == "different"


def test_writer_binds_job_to_frozen_bytes_not_mutable_caller_manifest(tmp_path, monkeypatch):
    root = _artifacts(tmp_path, _xml())
    caller = _manifest(tmp_path)
    caller["job_id"] = "b" * 32
    caller["argv"] = list(fixed_test_command(str(tmp_path), "pytest", "b" * 32))
    original = writer._artifacts_fd
    def mutate(repository, job_id):
        caller["job_id"] = JOB_ID
        caller["argv"] = list(fixed_test_command(str(tmp_path), "pytest", JOB_ID))
        return original(repository, job_id)
    monkeypatch.setattr(writer, "_artifacts_fd", mutate)
    with pytest.raises(writer.TestEvidenceWriterError):
        writer.finalize_test_evidence(tmp_path, JOB_ID, caller, b"log")
    assert set(path.name for path in root.iterdir()) == {"junit.xml"}
