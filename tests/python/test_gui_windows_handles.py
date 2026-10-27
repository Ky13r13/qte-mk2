from __future__ import annotations

import ctypes
from dataclasses import FrozenInstanceError
import sys

import pytest

from qte.gui import windows_artifact_handles as handles
from qte.gui import windows_files


class Function:
    def __init__(self, callback=lambda *args: 1):
        self.callback = callback
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class Kernel:
    def __init__(self, *, extended_result=1):
        self.CreateFileW = Function(lambda *args: 123)
        self.GetFileInformationByHandle = Function(self._ordinary)
        self.GetFileType = Function(lambda handle: 1)
        self.GetFinalPathNameByHandleW = Function()
        self.ReadFile = Function()
        self.CloseHandle = Function()
        self.GetFileInformationByHandleEx = Function(
            lambda *args: self._extended(extended_result, *args))

    @staticmethod
    def _ordinary(handle, output):
        value = ctypes.cast(output, ctypes.POINTER(windows_files._FileInfo)).contents
        value.attributes = 0x20
        value.volume = 7
        value.size_high = 0x1
        value.size_low = 0x23456789
        value.links = 3
        value.index_high = 1
        value.index_low = 2
        return 1

    @staticmethod
    def _extended(result, handle, info_class, output, size):
        if not result:
            return 0
        if info_class == 0:
            assert size == ctypes.sizeof(handles._FileBasicInfo)
            value = ctypes.cast(output, ctypes.POINTER(handles._FileBasicInfo)).contents
            value.last_write_time = 0x123456789ABCDEF
            value.change_time = 0x23456789ABCDEF0
            value.file_attributes = 0x1020
        elif info_class == 18:
            assert size == ctypes.sizeof(handles._FileIdInfo)
            value = ctypes.cast(output, ctypes.POINTER(handles._FileIdInfo)).contents
            value.volume_serial_number = 0xFEDCBA9876543210
            for index in range(16):
                value.file_id[index] = 255 - index
        else:
            raise AssertionError("unexpected information class")
        return 1


def _api(monkeypatch, *, extended_result=1):
    kernels = []

    def library(*args, **kwargs):
        value = Kernel(extended_result=extended_result)
        kernels.append(value)
        return value

    monkeypatch.setattr(handles.ctypes, "WinDLL", library, raising=False)
    # windows_files and windows_artifact_handles share the ctypes module object.
    api = handles.WindowsArtifactAPI()
    return api, kernels


def test_fixed_width_native_layouts_and_extended_binding(monkeypatch):
    api, kernels = _api(monkeypatch)
    assert ctypes.sizeof(handles._FileBasicInfo) == 40
    assert handles._FileBasicInfo.file_attributes.offset == 32
    assert ctypes.sizeof(handles._FileIdInfo) == 24
    assert handles._FileIdInfo.file_id.offset == 8
    extended = kernels[-1].GetFileInformationByHandleEx
    assert extended.argtypes == [
        handles.wintypes.HANDLE, ctypes.c_int, handles.wintypes.LPVOID,
        handles.wintypes.DWORD,
    ]
    assert extended.restype is handles.wintypes.BOOL
    assert isinstance(api, windows_files._Win32)


def test_artifact_info_uses_full_width_volume_file_id_and_raw_times(monkeypatch):
    api, kernels = _api(monkeypatch)
    value = api.artifact_info(99)
    assert value == handles.WindowsFileInfo(
        attributes=0x1020,
        volume=0xFEDCBA9876543210,
        file_id=bytes(range(255, 239, -1)),
        size=(1 << 32) | 0x23456789,
        modified=0x123456789ABCDEF,
        changed=0x23456789ABCDEF0,
        links=3,
        file_type=1,
    )
    assert type(value.file_id) is bytes and len(value.file_id) == 16
    calls = kernels[-1].GetFileInformationByHandleEx.calls
    assert [(call[0], call[1], call[3]) for call in calls] == [(99, 0, 40), (99, 18, 24)]
    with pytest.raises(FrozenInstanceError):
        value.volume = 1


@pytest.mark.parametrize("failure_call", [1, 2])
def test_extended_information_failure_has_no_legacy_identity_fallback(
        monkeypatch, failure_call):
    calls = 0

    def extended(*args):
        nonlocal calls
        calls += 1
        return 0 if calls == failure_call else Kernel._extended(1, *args)

    kernels = []

    def library(*args, **kwargs):
        kernel = Kernel()
        kernel.GetFileInformationByHandleEx = Function(extended)
        kernels.append(kernel)
        return kernel

    monkeypatch.setattr(handles.ctypes, "WinDLL", library, raising=False)
    monkeypatch.setattr(handles.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(handles.ctypes, "WinError", lambda code: OSError("win32 failure"),
                        raising=False)
    api = handles.WindowsArtifactAPI()
    with pytest.raises(OSError, match="win32 failure"):
        api.artifact_info(101)
    assert calls == failure_call


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows artifact identity requires Windows")
def test_native_windows_artifact_identity_smoke(tmp_path):
    path = tmp_path / "identity.bin"
    path.write_bytes(b"native identity")
    api = handles.WindowsArtifactAPI()
    handle = api.open(str(path), directory=False)
    try:
        value = api.artifact_info(handle)
        assert value.size == len(b"native identity")
        assert value.file_type == 1
        assert 0 <= value.volume < 2**64
        assert len(value.file_id) == 16
        assert value.links >= 1
    finally:
        api.close(handle)
