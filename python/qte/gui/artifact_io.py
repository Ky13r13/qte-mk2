"""Race-aware bounded reads for explicitly registered artifact roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import sys

METADATA_LIMIT = 1024 * 1024
FILE_LIMIT = 16 * 1024 * 1024
INVENTORY_LIMIT = 10_000
TOTAL_ARTIFACT_BYTES = 256 * 1024 * 1024
SENSITIVE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key"}


class ArtifactIOError(Exception):
    pass


@dataclass(frozen=True)
class Snapshot:
    name: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int

    def identity(self) -> tuple[int, int, int, int, int]:
        return self.device, self.inode, self.size, self.mtime_ns, self.ctime_ns


def checked_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in ("", ".", "..") for p in path.parts):
        raise ArtifactIOError("unsafe path")
    return path


def inventory_fingerprint(
    snapshots: dict[str, Snapshot],
) -> tuple[tuple[str, int, int, int, int, int], ...]:
    """Return a deterministic name-and-filesystem-identity inventory key."""
    return tuple(sorted(
        (name, *snapshot.identity()) for name, snapshot in snapshots.items()
    ))


def _posix_backend():
    if not sys.platform.startswith("linux"):
        raise ArtifactIOError("artifact storage is unsupported on this platform")
    from . import _artifact_io_posix
    return _artifact_io_posix


def validate_root(repository: Path, relative: str) -> Path:
    return _posix_backend().validate_root(repository, relative)


def inventory(root: Path) -> dict[str, Snapshot]:
    return _posix_backend().inventory(root)


def read_stable(root: Path, relative: str, limit: int = FILE_LIMIT,
                expected: Snapshot | None = None) -> bytes:
    return _posix_backend().read_stable(root, relative, limit, expected)
