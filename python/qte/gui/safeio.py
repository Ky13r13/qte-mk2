"""Bounded, symlink-free reads beneath one explicitly configured checkout."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

MAX_DOCUMENT_BYTES = 1024 * 1024


class SafeReadError(Exception):
    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


def read_registered_file(root: Path, relative_path: str) -> bytes:
    """Read a regular file without following a symlink in any path component."""
    rel = PurePosixPath(relative_path)
    if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        raise SafeReadError("document_forbidden", 403, "Document unavailable.")

    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SafeReadError("library_unavailable", 503, "Library source unavailable.") from None
    if not root.is_dir():
        raise SafeReadError("library_unavailable", 503, "Library source unavailable.")

    # O_NONBLOCK prevents a registered FIFO from hanging the single local server
    # before fstat has a chance to reject it as a non-regular file.
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
             getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    dir_flags = flags | getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, dir_flags)
        descriptors.append(current)
        for component in rel.parts[:-1]:
            current = os.open(component, dir_flags, dir_fd=current)
            descriptors.append(current)
        fd = os.open(rel.parts[-1], flags, dir_fd=current)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise SafeReadError("document_forbidden", 403, "Document unavailable.")
        if before.st_size > MAX_DOCUMENT_BYTES:
            raise SafeReadError("document_too_large", 413, "Document exceeds the size limit.")
        chunks: list[bytes] = []
        remaining = MAX_DOCUMENT_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise SafeReadError("document_too_large", 413, "Document exceeds the size limit.")
        after = os.fstat(fd)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if identity(before) != identity(after) or len(data) != after.st_size:
            raise SafeReadError("document_changed", 409, "Document changed while it was being read.")
        return data
    except SafeReadError:
        raise
    except FileNotFoundError:
        raise SafeReadError("document_not_found", 404, "Document unavailable.") from None
    except (OSError, RuntimeError):
        raise SafeReadError("document_forbidden", 403, "Document unavailable.") from None
    finally:
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                pass
