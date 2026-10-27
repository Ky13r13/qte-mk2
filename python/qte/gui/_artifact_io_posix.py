"""Private Linux descriptor-based artifact filesystem backend."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat

from .artifact_io import (
    ArtifactIOError,
    FILE_LIMIT,
    INVENTORY_LIMIT,
    SENSITIVE_SUFFIXES,
    Snapshot,
    TOTAL_ARTIFACT_BYTES,
    checked_relative,
)


def _snapshot(name: str, info: os.stat_result) -> Snapshot:
    return Snapshot(name, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                    info.st_dev, info.st_ino)


def validate_root(repository: Path, relative: str) -> Path:
    rel = checked_relative(relative)
    if rel.parts[0] != "build" or len(rel.parts) < 2 or any(p.startswith(".") for p in rel.parts):
        raise ArtifactIOError("artifact root is not allowed")
    if rel.parts[1] in {"local-dev", "python-package"}:
        raise ArtifactIOError("artifact root is reserved")
    if rel.parts[1] == "gui" and not (len(rel.parts) >= 5 and rel.parts[2] == "jobs" and rel.parts[4] == "artifacts"):
        raise ArtifactIOError("GUI state is not an artifact root")
    current = Path(repository)
    for part in rel.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            raise ArtifactIOError("artifact root is unavailable") from None
        if stat.S_ISLNK(mode):
            raise ArtifactIOError("symlinked artifact roots are forbidden")
    if not current.is_dir():
        raise ArtifactIOError("artifact root must be a directory")
    return current


def inventory(root: Path) -> dict[str, Snapshot]:
    result: dict[str, Snapshot] = {}
    entry_count = 0
    total_size = 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    root_fd = os.open(root, flags)
    pending = [(root_fd, PurePosixPath())]
    try:
        while pending:
            directory_fd, prefix = pending.pop()
            try:
                for name in os.listdir(directory_fd):
                    entry_count += 1
                    if entry_count > INVENTORY_LIMIT: raise ArtifactIOError("artifact inventory exceeds the limit")
                    if name.startswith(".") or PurePosixPath(name).suffix.casefold() in SENSITIVE_SUFFIXES:
                        raise ArtifactIOError("artifact contains a forbidden entry")
                    rel = prefix / name
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode): raise ArtifactIOError("artifact contains a symlink")
                    if stat.S_ISDIR(info.st_mode): pending.append((os.open(name, flags, dir_fd=directory_fd), rel))
                    elif stat.S_ISREG(info.st_mode):
                        total_size += info.st_size
                        if total_size > TOTAL_ARTIFACT_BYTES: raise ArtifactIOError("artifact exceeds the total size limit")
                        result[rel.as_posix()] = _snapshot(rel.as_posix(), info)
                    else: raise ArtifactIOError("artifact contains a non-regular entry")
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise ArtifactIOError("artifact inventory is unavailable") from exc
    finally:
        for directory_fd, _ in pending:
            try: os.close(directory_fd)
            except OSError: pass
    return dict(sorted(result.items()))


def read_stable(root: Path, relative: str, limit: int = FILE_LIMIT,
                expected: Snapshot | None = None) -> bytes:
    rel = checked_relative(relative)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    dir_flags = flags | getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, dir_flags)
        descriptors.append(current)
        for part in rel.parts[:-1]:
            current = os.open(part, dir_flags, dir_fd=current)
            descriptors.append(current)
        fd = os.open(rel.parts[-1], flags, dir_fd=current)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ArtifactIOError("artifact file is invalid or oversized")
        key = _snapshot(rel.as_posix(), before).identity()
        if expected and key != expected.identity():
            raise ArtifactIOError("artifact changed before read")
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(fd)
        after_key = _snapshot(rel.as_posix(), after).identity()
        if len(data) > limit or len(data) != after.st_size or key != after_key:
            raise ArtifactIOError("artifact changed during read")
        return data
    except (FileNotFoundError, OSError) as exc:
        raise ArtifactIOError("artifact file is unavailable") from exc
    finally:
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                pass
