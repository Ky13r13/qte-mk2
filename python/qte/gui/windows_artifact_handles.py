"""Native Windows artifact identity queried from already-open handles."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from .windows_files import _Win32

_FILE_BASIC_INFO_CLASS = 0
_FILE_ID_INFO_CLASS = 18


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("creation_time", ctypes.c_int64),
        ("last_access_time", ctypes.c_int64),
        ("last_write_time", ctypes.c_int64),
        ("change_time", ctypes.c_int64),
        ("file_attributes", ctypes.c_uint32),
    ]


class _FileIdInfo(ctypes.Structure):
    _fields_ = [
        ("volume_serial_number", ctypes.c_uint64),
        ("file_id", ctypes.c_uint8 * 16),
    ]


@dataclass(frozen=True)
class WindowsFileInfo:
    attributes: int
    volume: int
    file_id: bytes
    size: int
    modified: int
    changed: int
    links: int
    file_type: int


class WindowsArtifactAPI(_Win32):
    """Extend the pinned W1 handle API with full-width artifact identity."""

    def __init__(self):
        super().__init__()
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._extended_info = api.GetFileInformationByHandleEx
        self._extended_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._extended_info.restype = wintypes.BOOL

    def artifact_info(self, handle) -> WindowsFileInfo:
        ordinary = self.info(handle)
        basic = _FileBasicInfo()
        if not self._extended_info(
                handle, _FILE_BASIC_INFO_CLASS, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        identity = _FileIdInfo()
        if not self._extended_info(
                handle, _FILE_ID_INFO_CLASS, ctypes.byref(identity), ctypes.sizeof(identity)):
            raise ctypes.WinError(ctypes.get_last_error())
        return WindowsFileInfo(
            attributes=int(basic.file_attributes),
            volume=int(identity.volume_serial_number),
            file_id=bytes(identity.file_id),
            size=ordinary.size,
            modified=int(basic.last_write_time),
            changed=int(basic.change_time),
            links=ordinary.links,
            file_type=ordinary.file_type,
        )
