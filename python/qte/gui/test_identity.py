"""Capture and compare explicit, bounded test-job identity manifests.

This module deliberately does not discover repository files.  A trusted fixed
test-job specification supplies the complete list for each identity group.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
import unicodedata

from .artifact_io import ArtifactIOError, Snapshot, read_stable


IDENTITY_GROUPS = ("source", "tests", "binaries", "build")
MAX_FILES = 10_000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_REGULAR_FILE_BYTES = 16 * 1024 * 1024
MAX_BINARY_BYTES = 128 * 1024 * 1024


class TestIdentityError(ValueError):
    """A test identity could not be safely captured or validated."""


def _error() -> TestIdentityError:
    # Do not disclose caller-selected repository paths through evidence errors.
    return TestIdentityError("Test identity is unavailable.")


def _checked_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _error()
    if "\\" in value or value.startswith("/") or value.endswith("/") or "//" in value:
        raise _error()
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise _error()
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise _error()
    return value


def _checked_groups(groups: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(groups, dict) or set(groups) != set(IDENTITY_GROUPS):
        raise _error()
    result: dict[str, tuple[str, ...]] = {}
    names: set[str] = set()
    for group in IDENTITY_GROUPS:
        members = groups[group]
        if not isinstance(members, tuple):
            raise _error()
        checked = tuple(_checked_path(member) for member in members)
        if len(set(checked)) != len(checked) or any(name in names for name in checked):
            raise _error()
        names.update(checked)
        result[group] = checked
    if not result["source"] or not result["tests"] or not result["binaries"]:
        raise _error()
    if len(names) > MAX_FILES:
        raise _error()
    return result


def _snapshot(root: Path, relative: str, limit: int) -> Snapshot:
    """Obtain a no-follow snapshot of one regular file below *root*."""
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
             getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for component in relative.split("/")[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(relative.rsplit("/", 1)[-1], flags, dir_fd=current)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise _error()
        return Snapshot(relative, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                        info.st_dev, info.st_ino)
    except TestIdentityError:
        raise
    except (OSError, ValueError, TypeError):
        raise _error() from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _limit(group: str) -> int:
    return MAX_BINARY_BYTES if group == "binaries" else MAX_REGULAR_FILE_BYTES


def _validate_maps(value: object, *, require_nonempty: bool) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or set(value) != set(IDENTITY_GROUPS):
        raise _error()
    names: set[str] = set()
    result: dict[str, dict[str, str]] = {}
    for group in IDENTITY_GROUPS:
        entries = value[group]
        if not isinstance(entries, dict):
            raise _error()
        checked: dict[str, str] = {}
        for name, digest in entries.items():
            name = _checked_path(name)
            if name in names or not isinstance(digest, str) or len(digest) != 64:
                raise _error()
            if any(character not in "0123456789abcdef" for character in digest):
                raise _error()
            names.add(name)
            checked[name] = digest
        result[group] = dict(sorted(checked.items()))
    if len(names) > MAX_FILES:
        raise _error()
    if require_nonempty and any(not result[group] for group in ("source", "tests", "binaries")):
        raise _error()
    return result


def validate_identity(value: object) -> dict[str, dict[str, str]]:
    """Validate and canonicalize a stored identity manifest.

    Empty maps are accepted here so a format reader can validate historical or
    unavailable groups.  :func:`capture_identity` is stricter for new records.
    """
    return _validate_maps(value, require_nonempty=False)


def capture_identity(repository: Path, groups: dict[str, tuple[str, ...]]) -> dict[str, dict[str, str]]:
    """Hash only the explicit files in *groups*, rejecting unsafe races/paths."""
    checked_groups = _checked_groups(groups)
    root = Path(repository)
    snapshots: dict[str, tuple[str, Snapshot]] = {}
    total_size = 0
    try:
        for group in IDENTITY_GROUPS:
            for name in checked_groups[group]:
                snapshot = _snapshot(root, name, _limit(group))
                total_size += snapshot.size
                if total_size > MAX_TOTAL_BYTES:
                    raise _error()
                snapshots[name] = (group, snapshot)

        captured: dict[str, dict[str, str]] = {group: {} for group in IDENTITY_GROUPS}
        for group in IDENTITY_GROUPS:
            for name in checked_groups[group]:
                _, snapshot = snapshots[name]
                captured[group][name] = sha256(read_stable(root, name, _limit(group), snapshot)).hexdigest()

        # A stable per-file read prevents read-time mutation; this second pass
        # prevents a different listed file from changing before or after its turn.
        for name, (group, before) in snapshots.items():
            if _snapshot(root, name, _limit(group)) != before:
                raise _error()
        return _validate_maps(captured, require_nonempty=True)
    except TestIdentityError:
        raise
    except (ArtifactIOError, OSError, ValueError, TypeError):
        raise _error() from None


def compare_identity(recorded: object, current: object | None) -> dict[str, str]:
    """Compare two validated manifests without inferring provenance or freshness."""
    old = validate_identity(recorded)
    if current is None:
        return {group: "unknown" for group in IDENTITY_GROUPS}
    new = validate_identity(current)
    return {group: "unknown" if not old[group] or not new[group]
            else "same" if old[group] == new[group] else "different"
            for group in IDENTITY_GROUPS}
