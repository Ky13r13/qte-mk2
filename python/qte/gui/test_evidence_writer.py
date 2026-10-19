"""Exclusive completion-last persistence for trusted G8 test-job output.

This writes consistency evidence only.  It does not execute a command, attest to
the runner, or establish build provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from .artifact_io import FILE_LIMIT, METADATA_LIMIT
from .artifacts import strict_json
from .test_evidence import VerifiedTestEvidence, verify_test_evidence_files


_JOB_ID = re.compile(r"[0-9a-f]{32}")
_READ_FLAGS = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
               getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
_DIRECTORY_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)


class TestEvidenceWriterError(ValueError):
    """Evidence could not be finalized safely; any partial output is retained."""

    __test__ = False


@dataclass(frozen=True)
class _FileState:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _error() -> TestEvidenceWriterError:
    return TestEvidenceWriterError("Test evidence could not be finalized.")


def _state(info: os.stat_result) -> _FileState:
    return _FileState(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_regular(directory_fd: int, name: str, limit: int) -> tuple[bytes, _FileState]:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=directory_fd)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise _error()
        remaining, chunks = limit + 1, []
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(data) > limit or len(data) != after.st_size or _state(before) != _state(after):
            raise _error()
        return data, _state(after)
    except TestEvidenceWriterError:
        raise
    except OSError:
        raise _error() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _names(directory_fd: int) -> set[str]:
    try:
        # listdir(fd) advances the directory stream on Linux; reset it before
        # each exact-inventory check rather than accidentally treating a prior
        # observation as an empty directory.
        os.lseek(directory_fd, 0, os.SEEK_SET)
        return set(os.listdir(directory_fd))
    except OSError:
        raise _error() from None


def _artifacts_fd(repository: Path, job_id: str) -> tuple[list[int], int]:
    if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
        raise _error()
    root = Path(repository)
    if not root.is_absolute():
        raise _error()
    descriptors: list[int] = []
    try:
        current = os.open(root, _DIRECTORY_FLAGS)
        descriptors.append(current)
        for name in ("build", "gui", "jobs", job_id, "artifacts"):
            current = os.open(name, _DIRECTORY_FLAGS, dir_fd=current)
            descriptors.append(current)
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise _error()
        return descriptors, current
    except TestEvidenceWriterError:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except OSError:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise _error() from None


def _canonical_manifest(manifest: object) -> bytes:
    if type(manifest) is not dict:
        raise _error()
    try:
        value = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                           allow_nan=False).encode("utf-8")
        if len(value) > METADATA_LIMIT or strict_json(value) != manifest:
            raise _error()
        return value
    except TestEvidenceWriterError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error() from None


def _completion(files: dict[str, bytes]) -> bytes:
    try:
        return json.dumps({"schema_version": 1, "sha256": {
            name: sha256(files[name]).hexdigest() for name in sorted(files)
        }}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise _error() from None


def _write_exclusive(directory_fd: int, name: str, data: bytes) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                             0o600, dir_fd=directory_fd)
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except OSError:
        raise _error() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _verified(files: dict[str, bytes]) -> VerifiedTestEvidence:
    try:
        return verify_test_evidence_files(files)
    except ValueError:
        raise _error() from None


def _check_states(directory: int, expected: dict[str, _FileState]) -> None:
    if _names(directory) != set(expected):
        raise _error()
    for name, before in expected.items():
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or _state(info) != before:
            raise _error()


def _check_path(repository: Path, job_id: str, directory: int) -> None:
    descriptors, current = _artifacts_fd(repository, job_id)
    try:
        before, now = os.fstat(directory), os.fstat(current)
        if (before.st_dev, before.st_ino) != (now.st_dev, now.st_ino):
            raise _error()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def finalize_test_evidence(repository: Path, job_id: str, manifest: dict, log: bytes) -> VerifiedTestEvidence:
    """Persist one fixed job's evidence, creating the completion marker last.

    The caller must already have created the job-local artifacts directory and
    produced ``junit.xml``.  Existing evidence is never replaced or repaired.
    """
    if type(log) is not bytes or len(log) > FILE_LIMIT:
        raise _error()
    manifest_bytes = _canonical_manifest(manifest)
    frozen_manifest = strict_json(manifest_bytes)
    descriptors, artifact_fd = _artifacts_fd(Path(repository), job_id)
    try:
        repository_path = str(Path(repository))
        if frozen_manifest.get("job_id") != job_id or frozen_manifest.get("repository_path") != repository_path:
            raise _error()
        if _names(artifact_fd) != {"junit.xml"}:
            raise _error()
        junit, junit_state = _read_regular(artifact_fd, "junit.xml", FILE_LIMIT)

        initial = {"manifest.json": manifest_bytes, "junit.xml": junit, "log.txt": log}
        marker = _completion(initial)
        _verified({**initial, "complete.json": marker})

        _write_exclusive(artifact_fd, "log.txt", log)
        _write_exclusive(artifact_fd, "manifest.json", manifest_bytes)

        if _names(artifact_fd) != {"junit.xml", "log.txt", "manifest.json"}:
            raise _error()
        states: dict[str, _FileState] = {}
        for name, limit in (("junit.xml", FILE_LIMIT), ("log.txt", FILE_LIMIT),
                            ("manifest.json", METADATA_LIMIT)):
            data, state = _read_regular(artifact_fd, name, limit)
            if data != initial[name] or name == "junit.xml" and state != junit_state:
                raise _error()
            states[name] = state
        # Catch changes to an earlier file while a later file was being read,
        # and refuse to finalize a directory detached from the canonical path.
        _check_states(artifact_fd, states)
        _check_path(Path(repository), job_id, artifact_fd)
        _write_exclusive(artifact_fd, "complete.json", marker)
        os.fsync(artifact_fd)

        if _names(artifact_fd) != {"junit.xml", "log.txt", "manifest.json", "complete.json"}:
            raise _error()
        final: dict[str, bytes] = {}
        for name, limit in (("junit.xml", FILE_LIMIT), ("log.txt", FILE_LIMIT),
                            ("manifest.json", METADATA_LIMIT), ("complete.json", METADATA_LIMIT)):
            data, state = _read_regular(artifact_fd, name, limit)
            expected = marker if name == "complete.json" else initial[name]
            if data != expected or name in states and state != states[name]:
                raise _error()
            final[name] = data
            states[name] = state
        _check_states(artifact_fd, states)
        _check_path(Path(repository), job_id, artifact_fd)
        verified = _verified(final)
        os.fsync(artifact_fd)
        return verified
    except TestEvidenceWriterError:
        raise
    except (OSError, TypeError, ValueError):
        raise _error() from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
