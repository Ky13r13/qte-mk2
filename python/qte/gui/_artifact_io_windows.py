"""Private Windows artifact reader; not enabled by the GUI storage facade yet.

Names are enumerated only beneath pinned, volume-anchored directories. Type and
identity always come from opened handles, never cached directory-entry metadata.
This detects concurrent changes; it is not an atomic filesystem snapshot.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from .artifact_io import (
    ArtifactIOError, FILE_LIMIT, INVENTORY_LIMIT, SENSITIVE_SUFFIXES,
    TOTAL_ARTIFACT_BYTES, checked_relative,
)
from .safeio import SafeReadError
from .windows_artifact_handles import WindowsArtifactAPI, WindowsFileInfo
from .windows_files import _component, _paths

MAX_DEPTH = 128  # Maximum simultaneously pinned handles, including ancestors.
_DIRECTORY, _REPARSE, _DISK = 0x10, 0x400, 1


@dataclass(frozen=True)
class WindowsSnapshot:
    name: str
    size: int
    volume: int
    file_id: bytes
    modified: int
    changed: int
    attributes: int
    links: int

    def identity(self) -> tuple:
        # Raw Windows times and IDs: deliberately not POSIX inode/ctime aliases.
        return ("windows_file_id_v1", self.volume, self.file_id, self.size,
                self.modified, self.changed, self.attributes, self.links)


def _snapshot(name: str, info: WindowsFileInfo) -> WindowsSnapshot:
    return WindowsSnapshot(name, info.size, info.volume, info.file_id,
                           info.modified, info.changed, info.attributes, info.links)


def _info(api, handle) -> WindowsFileInfo:
    value = api.artifact_info(handle)
    if (value.file_type != _DISK or value.attributes & _REPARSE
            or type(value.file_id) is not bytes or len(value.file_id) != 16
            or not 0 <= value.volume < 2**64 or value.size < 0 or value.links < 1):
        raise ArtifactIOError("artifact handle is unavailable")
    return value


def _directory(api, handle) -> WindowsFileInfo:
    value = _info(api, handle)
    if not value.attributes & _DIRECTORY:
        raise ArtifactIOError("artifact directory is unavailable")
    return value


def _canonical(api, handle) -> str:
    value = api.path(handle)
    if not value.startswith("\\\\?\\Volume{") or len(value) >= 32767:
        raise ArtifactIOError("artifact directory is unavailable")
    return value.rstrip("\\")


def _close(api, handle) -> None:
    try:
        api.close(handle)
    except OSError:
        pass


def _close_entries(entries) -> None:
    try:
        entries.close()
    except OSError:
        pass


@contextmanager
def _pinned(root: str, relative: str, api):
    ancestors, leaf = _paths(root, relative)
    if len(ancestors) > MAX_DEPTH:
        raise ArtifactIOError("artifact depth exceeds the limit")
    handles = []
    try:
        parent = None
        for directory in ancestors:
            path = directory if parent is None else parent + "\\" + PureWindowsPath(directory).name
            handle = api.open(path, directory=True)
            handles.append(handle)
            _directory(api, handle)
            parent = _canonical(api, handle)
        yield handles, parent, PureWindowsPath(leaf).name
    finally:
        for handle in reversed(handles):
            _close(api, handle)


def _allowed_name(name: str) -> None:
    if (not _component(name) or name.startswith(".")
            or PurePosixPath(name).suffix.casefold() in SENSITIVE_SUFFIXES):
        raise ArtifactIOError("artifact contains a forbidden entry")


def _validate_root(repository: str, relative: str, api) -> str:
    rel = checked_relative(relative)
    if (rel.as_posix() != relative or rel.parts[0] != "build" or len(rel.parts) < 2
            or rel.parts[1] in {"local-dev", "python-package"}
            or (rel.parts[1] == "gui" and not (
                len(rel.parts) >= 5 and rel.parts[2] == "jobs" and rel.parts[4] == "artifacts"))):
        raise ArtifactIOError("artifact root is not allowed")
    for part in rel.parts:
        _allowed_name(part)
    try:
        with _pinned(repository, relative + "/qte-placeholder", api):
            return str(PureWindowsPath(repository).joinpath(*rel.parts))
    except (OSError, SafeReadError):
        raise ArtifactIOError("artifact root is unavailable") from None


@dataclass
class _Frame:
    handle: object
    path: str
    prefix: PurePosixPath
    before: WindowsSnapshot
    entries: object
    seen: set[str]
    owned: bool


def _inventory(root: str, api, scan) -> dict[str, WindowsSnapshot]:
    result = {}
    count = total = 0
    stack = []

    def frame(handle, path, prefix, info, owned):
        return _Frame(handle, path, prefix, _snapshot(prefix.as_posix(), info),
                      scan(path), set(), owned)

    try:
        with _pinned(root, "qte-placeholder", api) as (ancestors, canonical, _):
            try:
                stack.append(frame(ancestors[-1], canonical, PurePosixPath(),
                                   _directory(api, ancestors[-1]), False))
                while stack:
                    current = stack[-1]
                    try:
                        name = next(current.entries).name
                    except StopIteration:
                        after = _snapshot(current.prefix.as_posix(), _directory(api, current.handle))
                        if after.identity() != current.before.identity():
                            raise ArtifactIOError("artifact directory changed during inventory")
                        stack.pop()
                        _close_entries(current.entries)
                        if current.owned:
                            _close(api, current.handle)
                        continue
                    count += 1
                    if count > INVENTORY_LIMIT:
                        raise ArtifactIOError("artifact inventory exceeds the limit")
                    _allowed_name(name)
                    folded = name.casefold()
                    if folded in current.seen:
                        raise ArtifactIOError("artifact contains ambiguous names")
                    current.seen.add(folded)
                    if len(ancestors) + len(stack) > MAX_DEPTH:
                        raise ArtifactIOError("artifact depth exceeds the limit")
                    child = api.open(current.path + "\\" + name, directory=True)
                    retained = False
                    try:
                        info = _info(api, child)
                        relative = current.prefix / name
                        if info.attributes & _DIRECTORY:
                            stack.append(frame(child, _canonical(api, child), relative, info, True))
                            retained = True
                        else:
                            total += info.size
                            if total > TOTAL_ARTIFACT_BYTES:
                                raise ArtifactIOError("artifact exceeds the total size limit")
                            result[relative.as_posix()] = _snapshot(relative.as_posix(), info)
                    finally:
                        if not retained:
                            _close(api, child)
            finally:
                for current in reversed(stack):
                    _close_entries(current.entries)
                    if current.owned:
                        _close(api, current.handle)
    except (OSError, SafeReadError):
        raise ArtifactIOError("artifact inventory is unavailable") from None
    return dict(sorted(result.items()))


def _read_stable(root: str, relative: str, limit: int, expected, api) -> bytes:
    if type(limit) is not int or not 0 <= limit <= FILE_LIMIT:
        raise ArtifactIOError("artifact read limit is invalid")
    if expected is not None and (type(expected) is not WindowsSnapshot or expected.name != relative):
        raise ArtifactIOError("artifact snapshot identity is incompatible")
    for part in checked_relative(relative).parts:
        _allowed_name(part)
    try:
        with _pinned(root, relative, api) as (ancestors, canonical, leaf):
            if len(ancestors) >= MAX_DEPTH:
                raise ArtifactIOError("artifact depth exceeds the limit")
            handle = api.open(canonical + "\\" + leaf, directory=False)
            try:
                before = _info(api, handle)
                if before.attributes & _DIRECTORY or before.size > limit:
                    raise ArtifactIOError("artifact file is invalid or oversized")
                identity = _snapshot(relative, before).identity()
                if expected is not None and identity != expected.identity():
                    raise ArtifactIOError("artifact changed before read")
                chunks, remaining = [], limit + 1
                while remaining:
                    requested = min(65536, remaining)
                    chunk = api.read(handle, requested)
                    if type(chunk) is not bytes or len(chunk) > requested:
                        raise ArtifactIOError("artifact read is invalid")
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                data = b"".join(chunks)
                after = _info(api, handle)
                if (len(data) > limit or len(data) != after.size
                        or identity != _snapshot(relative, after).identity()):
                    raise ArtifactIOError("artifact changed during read")
                return data
            finally:
                _close(api, handle)
    except (OSError, SafeReadError):
        raise ArtifactIOError("artifact file is unavailable") from None


def _native_api():
    if sys.platform != "win32":
        raise ArtifactIOError("native Windows artifact storage is unavailable")
    try:
        return WindowsArtifactAPI()
    except (OSError, AttributeError):
        raise ArtifactIOError("native Windows artifact storage is unavailable") from None


def validate_root(repository: Path, relative: str) -> Path:
    api = _native_api()
    return Path(_validate_root(str(repository.absolute()), relative, api))


def inventory(root: Path) -> dict[str, WindowsSnapshot]:
    api = _native_api()
    return _inventory(str(root.absolute()), api, os.scandir)


def read_stable(root: Path, relative: str, limit: int = FILE_LIMIT,
                expected: WindowsSnapshot | None = None) -> bytes:
    api = _native_api()
    return _read_stable(str(root.absolute()), relative, limit, expected, api)
