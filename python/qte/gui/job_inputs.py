"""Bounded, immutable input snapshots for the fixed local research job."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .artifacts import strict_json


CONFIG_LIMIT = 1024 * 1024
DATA_LIMIT = 16 * 1024 * 1024
_SENSITIVE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key"}
_ALLOWED_ROOTS = {"examples", "configs", "data", "build"}
_RESERVED_BUILD = {"gui", "local-dev", "python-package"}
_JOB_ID = re.compile(r"[0-9a-f]{32}")
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)


class JobInputError(Exception):
    """A deliberately non-diagnostic error suitable for the local API."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code, self.status, self.message = code, status, message


@dataclass(frozen=True)
class PreparedInputs:
    original_config_path: str
    original_data_path: str
    adapter: str
    config_bytes: bytes
    data_bytes: bytes
    config_sha256: str
    data_sha256: str
    canonical_config_sha256: str
    effective_config_bytes: bytes
    fingerprint: str


@dataclass(frozen=True)
class SnapshotInputs:
    job_id: str
    input_root: str
    effective_config: str
    original_config_path: str
    original_data_path: str
    effective_config_path: str
    data_path: str
    hashes: dict[str, str]
    snapshot_hashes: dict[str, str]
    fingerprint: str


def _error(code: str) -> JobInputError:
    values = {
        "invalid": ("invalid_job_input", 400, "Research job input is invalid."),
        "forbidden": ("input_forbidden", 403, "Research job input is unavailable."),
        "changed": ("input_changed", 409, "Research job input changed while it was read."),
        "snapshot_exists": ("snapshot_exists", 409, "Job input snapshot already exists."),
        "snapshot": ("snapshot_invalid", 409, "Job input snapshot is unavailable."),
    }
    return JobInputError(*values[code])


def _safe_parts(value: str, *, allow_parent: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, str) or not value or "\x00" in value
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise _error("invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise _error("invalid")
    pending: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not allow_parent or not pending:
                raise _error("forbidden")
            pending.pop()
        elif part.startswith("."):
            raise _error("forbidden")
        else:
            pending.append(part)
    if not pending:
        raise _error("invalid")
    return tuple(pending)


def _allowed(parts: tuple[str, ...]) -> None:
    if parts[0] not in _ALLOWED_ROOTS:
        raise _error("forbidden")
    if parts[0] == "build" and (len(parts) < 2 or parts[1] in _RESERVED_BUILD):
        raise _error("forbidden")
    if any(part.startswith(".") for part in parts) or PurePosixPath(parts[-1]).suffix.casefold() in _SENSITIVE_SUFFIXES:
        raise _error("forbidden")


def _relative(parts: tuple[str, ...]) -> str:
    return PurePosixPath(*parts).as_posix()


def _data_parts(config_parts: tuple[str, ...], value: str) -> tuple[str, ...]:
    if (not isinstance(value, str) or not value or "\x00" in value
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise _error("invalid")
    candidate = PurePosixPath(*config_parts[:-1]) / PurePosixPath(value)
    if candidate.is_absolute():
        raise _error("forbidden")
    normalized: list[str] = []
    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not normalized:
                raise _error("forbidden")
            normalized.pop()
        elif part.startswith("."):
            raise _error("forbidden")
        else:
            normalized.append(part)
    if not normalized:
        raise _error("invalid")
    parts = tuple(normalized)
    _allowed(parts)
    return parts


def _read_regular(repository: Path, relative: str, limit: int) -> bytes:
    parts = _safe_parts(relative)
    descriptors: list[int] = []
    try:
        current = os.open(repository, _DIRECTORY_FLAGS)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            descriptors.append(current)
        fd = os.open(parts[-1], _READ_FLAGS | getattr(os, "O_NONBLOCK", 0), dir_fd=current)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise _error("forbidden")
        key = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        after = os.fstat(fd)
        after_key = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if len(result) > limit or len(result) != after.st_size or key != after_key:
            raise _error("changed")
        return result
    except JobInputError:
        raise
    except OSError:
        raise _error("forbidden") from None
    finally:
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                pass


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _canonical(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise _error("invalid") from None


def collect_inputs(repository: Path, config_path: str) -> PreparedInputs:
    """Read the approved JSON config and its CSV/Alpaca fixture once, by FD."""
    repository = Path(repository)
    config_parts = _safe_parts(config_path)
    _allowed(config_parts)
    if PurePosixPath(config_parts[-1]).suffix.casefold() != ".json":
        raise _error("invalid")
    original_config_path = _relative(config_parts)
    config_bytes = _read_regular(repository, original_config_path, CONFIG_LIMIT)
    try:
        config = strict_json(config_bytes)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise _error("invalid") from None
    if not isinstance(config, dict) or not isinstance(config.get("data"), dict):
        raise _error("invalid")
    data = config["data"]
    adapter, data_value = data.get("adapter"), data.get("path")
    if not isinstance(adapter, str) or adapter not in {"csv", "alpaca"} or not isinstance(data_value, str):
        raise _error("invalid")
    data_parts = _data_parts(config_parts, data_value)
    data_suffix = PurePosixPath(data_parts[-1]).suffix.casefold()
    if (adapter == "csv" and data_suffix != ".csv") or (adapter == "alpaca" and data_suffix != ".json"):
        raise _error("invalid")
    original_data_path = _relative(data_parts)
    data_bytes = _read_regular(repository, original_data_path, DATA_LIMIT)
    canonical = copy.deepcopy(config)
    canonical["data"]["path"] = original_data_path
    canonical_config = _canonical(canonical)
    copied_name = "input.csv" if adapter == "csv" else "input.json"
    effective = copy.deepcopy(canonical)
    effective["data"]["path"] = copied_name
    effective_bytes = _canonical(effective)
    config_hash, data_hash = _digest(config_bytes), _digest(data_bytes)
    canonical_hash = _digest(canonical_config)
    fingerprint = _digest(_canonical({"schema_version": 1, "adapter": adapter,
                                     "canonical_config_sha256": canonical_hash, "data_sha256": data_hash}))
    return PreparedInputs(original_config_path, original_data_path, adapter, config_bytes, data_bytes,
                          config_hash, data_hash, canonical_hash, effective_bytes, fingerprint)


def _mkdir_open(parent_fd: int, name: str, *, exclusive: bool = False) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        if exclusive:
            raise _error("snapshot_exists") from None
    except OSError:
        raise _error("snapshot") from None
    try:
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            os.close(fd)
            raise _error("snapshot")
        return fd
    except JobInputError:
        raise
    except OSError:
        raise _error("snapshot") from None


def _job_inputs_fd(repository: Path, job_id: str, *, create: bool) -> tuple[list[int], int]:
    if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
        raise _error("invalid")
    descriptors: list[int] = []
    try:
        current = os.open(repository, _DIRECTORY_FLAGS)
        descriptors.append(current)
        for name in ("build", "gui", "jobs"):
            if create:
                current = _mkdir_open(current, name)
            else:
                current = os.open(name, _DIRECTORY_FLAGS, dir_fd=current)
            descriptors.append(current)
        if create:
            current = _mkdir_open(current, job_id, exclusive=True)
            descriptors.append(current)
            current = _mkdir_open(current, "inputs", exclusive=True)
            descriptors.append(current)
            return descriptors, current
        current = os.open(job_id, _DIRECTORY_FLAGS, dir_fd=current)
        descriptors.append(current)
        current = os.open("inputs", _DIRECTORY_FLAGS, dir_fd=current)
        descriptors.append(current)
        return descriptors, current
    except JobInputError:
        for fd in reversed(descriptors):
            try: os.close(fd)
            except OSError: pass
        raise
    except OSError:
        for fd in reversed(descriptors):
            try: os.close(fd)
            except OSError: pass
        raise _error("snapshot") from None


def _write_exclusive(directory_fd: int, name: str, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, 0o400, dir_fd=directory_fd)
        try:
            offset = 0
            while offset < len(value):
                offset += os.write(fd, value[offset:])
            os.fsync(fd)
            os.fchmod(fd, 0o400)
        finally:
            os.close(fd)
    except OSError:
        raise _error("snapshot") from None


def _manifest(prepared: PreparedInputs, job_id: str, hashes: dict[str, str]) -> bytes:
    return _canonical({"schema_version": 1, "job_id": job_id, "adapter": prepared.adapter,
                       "original_config_path": prepared.original_config_path,
                       "original_data_path": prepared.original_data_path,
                       "canonical_config_sha256": prepared.canonical_config_sha256,
                       "fingerprint": prepared.fingerprint, "hashes": hashes}) + b"\n"


def write_snapshot(repository: Path, job_id: str, prepared: PreparedInputs) -> SnapshotInputs:
    """Create a fresh, read-only input directory without creating artifacts."""
    if not isinstance(prepared, PreparedInputs):
        # Late import: form capture already imports this module for its bounded
        # descriptor helpers, while inline snapshots deliberately share them.
        from .inline_inputs import PreparedInlineInputs, write_inline_snapshot
        if isinstance(prepared, PreparedInlineInputs):
            return write_inline_snapshot(repository, job_id, prepared)
        raise _error("invalid")
    data_name = "input.csv" if prepared.adapter == "csv" else "input.json" if prepared.adapter == "alpaca" else None
    if data_name is None or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                               for value in (prepared.config_sha256, prepared.data_sha256,
                                             prepared.canonical_config_sha256, prepared.fingerprint)):
        raise _error("invalid")
    descriptors, inputs_fd = _job_inputs_fd(Path(repository), job_id, create=True)
    try:
        content_hashes = {"original-config.json": _digest(prepared.config_bytes),
                          "effective-config.json": _digest(prepared.effective_config_bytes),
                          data_name: _digest(prepared.data_bytes)}
        if content_hashes["original-config.json"] != prepared.config_sha256 or content_hashes[data_name] != prepared.data_sha256:
            raise _error("invalid")
        _write_exclusive(inputs_fd, "original-config.json", prepared.config_bytes)
        _write_exclusive(inputs_fd, "effective-config.json", prepared.effective_config_bytes)
        _write_exclusive(inputs_fd, data_name, prepared.data_bytes)
        manifest = _manifest(prepared, job_id, content_hashes)
        _write_exclusive(inputs_fd, "input-manifest.json", manifest)
        snapshot_hashes = {**content_hashes, "input-manifest.json": _digest(manifest)}
        os.fsync(inputs_fd)
        os.fchmod(inputs_fd, 0o500)
    finally:
        for fd in reversed(descriptors):
            try: os.close(fd)
            except OSError: pass
    root = f"build/gui/jobs/{job_id}/inputs"
    hashes = {"config_sha256": prepared.config_sha256, "data_sha256": prepared.data_sha256,
              "canonical_config_sha256": prepared.canonical_config_sha256,
              "effective_config_sha256": content_hashes["effective-config.json"],
              "input_manifest_sha256": snapshot_hashes["input-manifest.json"]}
    return SnapshotInputs(job_id, root, f"{root}/effective-config.json", prepared.original_config_path,
                          prepared.original_data_path, f"{root}/effective-config.json", f"{root}/{data_name}",
                          hashes, snapshot_hashes, prepared.fingerprint)


def verify_snapshot(repository: Path, job_id: str, expected_snapshot_hashes: dict[str, str]) -> SnapshotInputs:
    """Verify persisted hashes, including the manifest, rather than trusting it."""
    if isinstance(expected_snapshot_hashes, dict) and "submitted-form.json" in expected_snapshot_hashes:
        # The v2 inventory is recognized by its required submitted envelope;
        # its verifier then checks the complete exact inventory and schema.
        from .inline_inputs import verify_inline_snapshot
        return verify_inline_snapshot(repository, job_id, expected_snapshot_hashes)
    if (not isinstance(expected_snapshot_hashes, dict) or not expected_snapshot_hashes
            or any(not isinstance(name, str) or not isinstance(digest, str)
                   or not re.fullmatch(r"[0-9a-f]{64}", digest)
                   for name, digest in expected_snapshot_hashes.items())):
        raise _error("snapshot")
    descriptors, inputs_fd = _job_inputs_fd(Path(repository), job_id, create=False)
    try:
        names = {"original-config.json", "effective-config.json", "input-manifest.json"}
        data_names = {"input.csv", "input.json"}
        if set(expected_snapshot_hashes) - names - data_names or len(set(expected_snapshot_hashes) & data_names) != 1:
            raise _error("snapshot")
        expected_names = names | (set(expected_snapshot_hashes) & data_names)
        if set(expected_snapshot_hashes) != expected_names:
            raise _error("snapshot")
        try:
            inventory = set(os.listdir(inputs_fd))
        except OSError:
            raise _error("snapshot") from None
        if inventory != expected_names:
            raise _error("snapshot")
        values = {name: _read_regular(Path(repository), f"build/gui/jobs/{job_id}/inputs/{name}", DATA_LIMIT)
                  for name in expected_names}
        actual = {name: _digest(value) for name, value in values.items()}
        if actual != expected_snapshot_hashes:
            raise _error("snapshot")
        try:
            manifest = strict_json(values["input-manifest.json"])
        except (UnicodeDecodeError, ValueError, TypeError):
            raise _error("snapshot") from None
        data_name = next(iter(set(expected_snapshot_hashes) & data_names))
        if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "job_id", "adapter",
                "original_config_path", "original_data_path", "canonical_config_sha256", "fingerprint", "hashes"}
                or manifest.get("schema_version") != 1 or manifest.get("job_id") != job_id
                or manifest.get("adapter") not in {"csv", "alpaca"} or not isinstance(manifest.get("hashes"), dict)):
            raise _error("snapshot")
        content_hashes = {name: actual[name] for name in expected_names - {"input-manifest.json"}}
        if manifest["hashes"] != content_hashes:
            raise _error("snapshot")
        paths = (manifest.get("original_config_path"), manifest.get("original_data_path"))
        if any(not isinstance(path, str) for path in paths) or not all(isinstance(manifest.get(name), str)
                and re.fullmatch(r"[0-9a-f]{64}", manifest[name]) for name in ("canonical_config_sha256", "fingerprint")):
            raise _error("snapshot")
        root = f"build/gui/jobs/{job_id}/inputs"
        hashes = {"config_sha256": actual["original-config.json"], "data_sha256": actual[data_name],
                  "canonical_config_sha256": manifest["canonical_config_sha256"],
                  "effective_config_sha256": actual["effective-config.json"],
                  "input_manifest_sha256": actual["input-manifest.json"]}
        return SnapshotInputs(job_id, root, f"{root}/effective-config.json", paths[0], paths[1],
                              f"{root}/effective-config.json", f"{root}/{data_name}", hashes,
                              dict(expected_snapshot_hashes), manifest["fingerprint"])
    except JobInputError:
        raise
    finally:
        for fd in reversed(descriptors):
            try: os.close(fd)
            except OSError: pass
