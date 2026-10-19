"""Private immutable fixed-test requests; no scheduler or test execution.

Snapshot verification is inert. Dispatch must separately rediscover the trusted
runtime through recheck_test_snapshot, never execute a stored argv.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from .artifact_io import METADATA_LIMIT
from .artifacts import strict_json
from .job_inputs import JobInputError, _job_inputs_fd, _write_exclusive
from .test_evidence import fixed_test_command
from .test_preflight import PreparedTestRun, recheck_prepared, validate_prepared


_FILES = frozenset({"test-request.json", "input-manifest.json"})


class TestSnapshotError(ValueError):
    __test__ = False


def _error():
    return TestSnapshotError("Test request snapshot is unavailable or invalid.")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class TestSnapshotInputs:
    __test__ = False
    job_id: str
    runner: str
    request_sha256: str
    manifest_sha256: str

    @property
    def input_root(self) -> str:
        return f"build/gui/jobs/{self.job_id}/inputs"

    @property
    def snapshot_hashes(self) -> dict[str, str]:
        return {"test-request.json": self.request_sha256,
                "input-manifest.json": self.manifest_sha256}


def _manifest(job_id: str, prepared: PreparedTestRun) -> bytes:
    return _canonical({"schema_version": 4, "kind": "test_run", "job_id": job_id,
                       "runner": prepared.value["runner"],
                       "request_sha256": sha256(prepared.payload).hexdigest()}) + b"\n"


def _result(job_id, prepared, manifest):
    return TestSnapshotInputs(job_id, prepared.value["runner"],
                              sha256(prepared.payload).hexdigest(), sha256(manifest).hexdigest())


def _bound(repository: Path, job_id: str, prepared: PreparedTestRun):
    value = prepared.value
    # Reuse the canonical path/job/runner contract; this describes, never runs.
    fixed_test_command(str(repository), value["runner"], job_id)
    if value["repository_path"] != str(repository) or len(prepared.payload) > METADATA_LIMIT:
        raise _error()


def write_test_snapshot(repository: Path, job_id: str, prepared: PreparedTestRun) -> TestSnapshotInputs:
    """Recheck trusted preflight, then exclusively persist its captured bytes.

    Rechecking includes bounded CTest show-only discovery for a CTest request,
    not running tests or building. A partial directory is retained, never reused.
    """
    descriptors = []
    try:
        repository = Path(repository)
        prepared = validate_prepared(prepared)
        _bound(repository, job_id, prepared)
        prepared = recheck_prepared(repository, prepared)
        manifest = _manifest(job_id, prepared)
        descriptors, directory = _job_inputs_fd(repository, job_id, create=True)
        _write_exclusive(directory, "test-request.json", prepared.payload)
        _write_exclusive(directory, "input-manifest.json", manifest)
        os.fsync(directory)
        os.fchmod(directory, 0o500)
        # Persist directory entries as well as the files. Retain them on errors.
        for descriptor in reversed(descriptors[:-1]):
            os.fsync(descriptor)
        return _result(job_id, prepared, manifest)
    except (JobInputError, OSError, ValueError, TypeError, KeyError, AttributeError):
        raise _error() from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _key(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(directory: int, name: str) -> tuple[bytes, tuple]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    descriptor = os.open(name, flags, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > METADATA_LIMIT:
            raise _error()
        chunks, size = [], 0
        while size <= METADATA_LIMIT:
            chunk = os.read(descriptor, min(65536, METADATA_LIMIT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size != before.st_size or _key(os.fstat(descriptor)) != _key(before):
            raise _error()
        return b"".join(chunks), _key(before)
    finally:
        os.close(descriptor)


def load_test_snapshot(repository: Path, job_id: str, expected_hashes: dict[str, str]
                       ) -> tuple[TestSnapshotInputs, PreparedTestRun]:
    """Validate exact inventory against independently pinned hashes; no execution."""
    descriptors, reopened = [], []
    try:
        if (type(expected_hashes) is not dict or set(expected_hashes) != _FILES
                or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in expected_hashes.values())):
            raise _error()
        repository = Path(repository)
        descriptors, directory = _job_inputs_fd(repository, job_id, create=False)
        if set(os.listdir(directory)) != _FILES:
            raise _error()
        contents, snapshots = {}, {}
        for name in sorted(_FILES):
            contents[name], snapshots[name] = _read(directory, name)
            if sha256(contents[name]).hexdigest() != expected_hashes[name]:
                raise _error()
        prepared = validate_prepared(PreparedTestRun(contents["test-request.json"]))
        _bound(repository, job_id, prepared)
        # Exact canonical manifest also rejects duplicate/extra/schema fields.
        strict_json(contents["input-manifest.json"])
        if (contents["test-request.json"] != prepared.payload
                or contents["input-manifest.json"] != _manifest(job_id, prepared)):
            raise _error()
        if (set(os.listdir(directory)) != _FILES
                or any(_key(os.stat(name, dir_fd=directory, follow_symlinks=False)) != snapshots[name]
                       for name in _FILES)):
            raise _error()
        reopened, current = _job_inputs_fd(repository, job_id, create=False)
        if _key(os.fstat(current)) != _key(os.fstat(directory)):
            raise _error()
        return _result(job_id, prepared, contents["input-manifest.json"]), prepared
    except (JobInputError, OSError, ValueError, TypeError, KeyError, AttributeError):
        raise _error() from None
    finally:
        for descriptor in reversed(reopened + descriptors):
            os.close(descriptor)


def recheck_test_snapshot(repository: Path, job_id: str, expected_hashes: dict[str, str]
                          ) -> tuple[TestSnapshotInputs, PreparedTestRun]:
    """Dispatch gate: rediscover identities and reverify the pinned request."""
    snapshot, prepared = load_test_snapshot(repository, job_id, expected_hashes)
    try:
        recheck_prepared(repository, prepared)
    except (ValueError, OSError, TypeError):
        raise _error() from None
    if load_test_snapshot(repository, job_id, expected_hashes) != (snapshot, prepared):
        raise _error()
    return snapshot, prepared
