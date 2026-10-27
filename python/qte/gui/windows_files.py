"""Bounded Windows document reads with pinned, non-reparse ancestors.

Only local drive paths are supported. Every handle omits share-delete and
share-write. No check-then-open path fallback is allowed.
Native Windows acceptance remains separate from the injected-API unit tests.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .safeio import MAX_DOCUMENT_BYTES, SafeReadError

_DIRECTORY = 0x10
_REPARSE = 0x400
_DISK = 1
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"
}


def _component(value: str) -> bool:
    return (bool(value) and value not in {".", ".."} and not value.endswith((".", " "))
            and not any(ord(char) < 32 or char in '<>:"/\\|?*' for char in value)
            and value.split(".", 1)[0].upper() not in _RESERVED)


def _paths(root: str, relative: str) -> tuple[tuple[str, ...], str]:
    """Pure lexical validation, before any file handle is opened."""
    if type(root) is not str or type(relative) is not str:
        raise SafeReadError("document_forbidden", 403, "Document unavailable.")
    base = PureWindowsPath(root)
    rel = PurePosixPath(relative)
    if (len(base.drive) != 2 or not base.drive[0].isascii() or not base.drive[0].isalpha()
            or base.drive[1] != ":" or base.root != "\\"
            or not relative or not rel.parts or rel.is_absolute() or rel.as_posix() != relative
            or any(not _component(part) for part in rel.parts)
            or any(not _component(part) for part in base.parts[1:])
            or ".." in root.replace("/", "\\").split("\\")):
        raise SafeReadError("document_forbidden", 403, "Document unavailable.")
    current = PureWindowsPath(base.anchor)
    ancestors = [str(current)]
    for part in (*base.parts[1:], *rel.parts[:-1]):
        current /= part
        ancestors.append(str(current))
    return tuple(ancestors), str(current / rel.parts[-1])


@dataclass(frozen=True)
class _Info:
    attributes: int
    volume: int
    index: int
    size: int
    modified: int
    links: int
    file_type: int


class _FileInfo(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                ("accessed", wintypes.FILETIME), ("modified", wintypes.FILETIME),
                ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]


class _Win32:
    def __init__(self):
        # Lazy loading lets non-Windows test code exercise policy with fake handles.
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._create = api.CreateFileW
        self._create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self._create.restype = wintypes.HANDLE
        self._info = api.GetFileInformationByHandle
        self._info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
        self._info.restype = wintypes.BOOL
        self._type = api.GetFileType
        self._type.argtypes = [wintypes.HANDLE]
        self._type.restype = wintypes.DWORD
        self._path = api.GetFinalPathNameByHandleW
        self._path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        self._path.restype = wintypes.DWORD
        self._read = api.ReadFile
        self._read.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        self._read.restype = wintypes.BOOL
        self._close = api.CloseHandle
        self._close.argtypes = [wintypes.HANDLE]
        self._close.restype = wintypes.BOOL

    def open(self, path: str, *, directory: bool):
        # OPEN_EXISTING; OPEN_REPARSE_POINT prevents following the final component.
        # Parent components are already pinned and checked when this is called.
        access = 0x80 if directory else 0x80000000  # READ_ATTRIBUTES / GENERIC_READ
        # Also deny ancestor writers: an in-place reparse change must not race
        # the check and later descendant traversal. Sharing conflicts fail closed.
        share = 0x1  # READ only; never WRITE or DELETE
        handle = self._create(path, access, share, None, 3, 0x00200000 | 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def info(self, handle) -> _Info:
        value = _FileInfo()
        if not self._info(handle, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _Info(value.attributes, value.volume,
                     (value.index_high << 32) | value.index_low,
                     (value.size_high << 32) | value.size_low,
                     (value.modified.dwHighDateTime << 32) | value.modified.dwLowDateTime,
                     value.links, self._type(handle))

    def read(self, handle, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        received = wintypes.DWORD()
        if not self._read(handle, buffer, size, ctypes.byref(received), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if received.value > size:
            raise OSError("invalid read size")
        return buffer.raw[:received.value]

    def path(self, handle) -> str:
        # Anchor descendants to the opened volume, not a mutable DOS drive mapping.
        buffer = ctypes.create_unicode_buffer(32768)
        size = self._path(handle, buffer, len(buffer), 0x1)  # VOLUME_NAME_GUID
        if not 0 < size < len(buffer):
            raise OSError("canonical file path unavailable")
        value = buffer.value
        if not value.startswith("\\\\?\\Volume{"):
            raise OSError("local volume path required")
        return value

    def close(self, handle) -> None:
        if not self._close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _read_pinned(root: str, relative: str, api) -> bytes:
    ancestors, leaf = _paths(root, relative)
    handles = []
    try:
        parent = None
        for directory in ancestors:
            path = directory if parent is None else parent.rstrip("\\") + "\\" + PureWindowsPath(directory).name
            handle = api.open(path, directory=True)
            handles.append(handle)
            info = api.info(handle)
            if info.file_type != _DISK or info.attributes & _REPARSE or not info.attributes & _DIRECTORY:
                raise SafeReadError("document_forbidden", 403, "Document unavailable.")
            parent = api.path(handle)
        handle = api.open(parent.rstrip("\\") + "\\" + PureWindowsPath(leaf).name, directory=False)
        handles.append(handle)
        before = api.info(handle)
        if before.file_type != _DISK or before.attributes & (_REPARSE | _DIRECTORY):
            raise SafeReadError("document_forbidden", 403, "Document unavailable.")
        if before.size > MAX_DOCUMENT_BYTES:
            raise SafeReadError("document_too_large", 413, "Document exceeds the size limit.")
        chunks = []
        remaining = MAX_DOCUMENT_BYTES + 1
        while remaining:
            chunk = api.read(handle, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise SafeReadError("document_too_large", 413, "Document exceeds the size limit.")
        after = api.info(handle)
        if before != after or len(data) != after.size:
            raise SafeReadError("document_changed", 409, "Document changed while it was being read.")
        return data
    except SafeReadError:
        raise
    except OSError as error:
        if getattr(error, "winerror", None) in {2, 3} or isinstance(error, FileNotFoundError):
            raise SafeReadError("document_not_found", 404, "Document unavailable.") from None
        raise SafeReadError("document_forbidden", 403, "Document unavailable.") from None
    finally:
        for handle in reversed(handles):
            try:
                api.close(handle)
            except OSError:
                pass


def read_windows_file(root: Path, relative: str) -> bytes:
    return _read_pinned(str(root.absolute()), relative, _Win32())
