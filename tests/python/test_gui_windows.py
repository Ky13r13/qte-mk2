"""Windows dispatch/policy checks; fake handles are NOT native Windows acceptance."""
import asyncio
import builtins
import subprocess
import sys

import httpx
import pytest

from qte.gui import app as app_module
from qte.gui.safeio import SafeReadError, MAX_DOCUMENT_BYTES
from qte.gui.windows_files import _Info, _paths, _read_pinned


class Files:
    def __init__(self, data=b"hello", *, bad_index=None, attributes=0x400, changed=False,
                 too_large=False, file_type=1, error_index=None):
        self.data, self.cursor = data, 0
        self.bad_index, self.attributes, self.changed = bad_index, attributes, changed
        self.too_large, self.file_type, self.error_index = too_large, file_type, error_index
        self.opened, self.closed, self.info_count = [], [], {}

    def open(self, path, *, directory):
        index = len(self.opened)
        if index == self.error_index:
            raise OSError("PRIVATE path/share error")
        self.opened.append((path, directory))
        return index

    def info(self, handle):
        count = self.info_count.get(handle, 0)
        self.info_count[handle] = count + 1
        directory = self.opened[handle][1]
        attrs = 0x10 if directory else 0
        if handle == self.bad_index:
            attrs = self.attributes
        size = MAX_DOCUMENT_BYTES + 1 if self.too_large else len(self.data)
        return _Info(attrs, 1, handle, 0 if directory else size,
                     2 if self.changed and count else 1, 1, self.file_type)

    def path(self, handle):
        return f"\\\\?\\Volume{{fixture}}\\pinned-{handle}"

    def read(self, handle, size):
        assert not self.opened[handle][1]
        value = self.data[self.cursor:self.cursor + size]
        self.cursor += len(value)
        return value

    def close(self, handle):
        self.closed.append(handle)


@pytest.mark.parametrize("relative", ["", ".", "../secret", "a/../secret", "./README.md",
    "a//b", "/README.md", "C:/README.md", "a\\b", "x:secret", "NUL", "con.txt",
    "COM1.log", "LPT¹", "CONOUT$", "tail.", "tail ", "a/PRN.md", "x\x00y", "a?b"])
def test_windows_logical_paths_reject_aliases_before_open(relative):
    api = Files()
    with pytest.raises(SafeReadError):
        _read_pinned("C:\\qte", relative, api)
    assert not api.opened


@pytest.mark.parametrize("root", ["qte", "C:qte", "\\\\host\\share\\qte", "\\\\?\\C:\\qte",
                                  "\\\\.\\C:\\qte", "C:\\qte\\..\\other", "C:\\NUL", "C:\\tail."])
def test_windows_root_requires_an_unambiguous_local_drive_path(root):
    with pytest.raises(SafeReadError):
        _paths(root, "README.md")


def test_pinned_windows_reader_owns_handles_and_uses_volume_anchored_children():
    api = Files("héllo".encode())
    assert _read_pinned("C:\\Users\\研究\\qte", "docs/guide.md", api) == "héllo".encode()
    assert api.opened[0] == ("C:\\", True)
    assert all(path.startswith("\\\\?\\Volume{fixture}\\pinned-") for path, _ in api.opened[1:])
    assert api.opened[-1][0].endswith("\\guide.md")
    assert api.closed == list(reversed(range(len(api.opened))))


def test_win32_open_contract_denies_write_delete_and_requests_no_reparse_follow(monkeypatch):
    from qte.gui import windows_files
    calls = []

    class Function:
        def __call__(self, *args):
            calls.append(args)
            return 123

    class Kernel:
        CreateFileW = Function()
        GetFileInformationByHandle = Function()
        GetFileType = Function()
        GetFinalPathNameByHandleW = Function()
        ReadFile = Function()
        CloseHandle = Function()

    monkeypatch.setattr(windows_files.ctypes, "WinDLL", lambda *a, **kw: Kernel(), raising=False)
    api = windows_files._Win32()
    for directory in (True, False):
        api.open("C:\\qte", directory=directory)
        args = calls[-1]
        assert args[1] == (0x80 if directory else 0x80000000)
        assert args[2] == 1  # Share read only for both files AND ancestors.
        assert args[4] == 3  # OPEN_EXISTING; no writes/creation.
        assert args[5] == 0x00200000 | 0x02000000


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_windows_reader_rejects_reparse_points_including_root_and_leaf(index):
    api = Files(bad_index=index)
    with pytest.raises(SafeReadError, match="unavailable"):
        _read_pinned("C:\\qte", "docs/guide.md", api)
    assert api.closed == list(reversed(range(index + 1)))
    assert api.cursor == 0


@pytest.mark.parametrize("options,code", [
    ({"changed": True}, "document_changed"),
    ({"too_large": True}, "document_too_large"),
    ({"file_type": 3}, "document_forbidden"),
    ({"bad_index": 3, "attributes": 0x10}, "document_forbidden"),
    ({"error_index": 2}, "document_forbidden"),
])
def test_windows_reader_bounds_identity_type_and_cleanup(options, code):
    api = Files(**options)
    with pytest.raises(SafeReadError) as error:
        _read_pinned("C:\\qte", "docs/guide.md", api)
    assert error.value.code == code
    assert "PRIVATE" not in str(error.value)
    assert api.closed == list(reversed(range(len(api.opened))))


def test_limited_app_never_imports_linux_jobs_and_has_authenticated_fail_closed_routes(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_text("# QTE\nWindows fixture", encoding="utf-8")
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "fcntl" or name in {"jobs", "catalog", "artifact_routes", "job_routes",
                                        "form_routes", "dataset_routes", "comparison_routes",
                                        "test_routes", "benchmark_routes"}:
            pytest.fail(f"limited host imported {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(app_module, "full_gui_supported", lambda: False)
    monkeypatch.setattr(builtins, "__import__", guarded)

    async def scenario():
        app = app_module.create_app(tmp_path, code_sink=lambda _: None, alpaca_download_enabled=True)
        assert app.state.catalog is None and app.state.jobs is None
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://127.0.0.1:8765") as client:
                assert (await client.get("/api/v1/jobs")).status_code == 401
                login = await client.post("/api/v1/session", headers={"Origin": "http://127.0.0.1:8765"},
                    json={"schema_version": 1, "code": app.state.session_controller.access_code_for_testing})
                headers = {"X-QTE-Token": login.json()["request_token"]}
                capabilities = (await client.get("/api/v1/capabilities", headers=headers)).json()
                available = {item["id"] for item in capabilities["capabilities"] if item["state"] == "available"}
                assert available == {"library", "connection_inspection"}
                doc = await client.get("/api/v1/documents/readme", headers=headers)
                assert doc.status_code == 200 and "Windows fixture" in doc.text
                assert (await client.get("/api/v1/connections", headers=headers)).status_code == 200
                for path in ("jobs", "catalog/register", "artifacts/id/files", "runs/id",
                             "datasets/id", "comparisons/preview", "configs/validate",
                             "data/alpaca/prerequisites", "system/tests/jobs", "system/benchmarks/jobs"):
                    response = await client.get(f"/api/v1/{path}", headers=headers)
                    assert response.status_code == 503
                    assert response.json()["error"]["code"] == "platform_unavailable"
                    assert response.headers["cache-control"] == "no-store"
                assert (await client.post("/api/v1/jobs", headers=headers, json={})).status_code == 403
                response = await client.post("/api/v1/jobs", json={},
                    headers={**headers, "Origin": "http://127.0.0.1:8765"})
                assert response.status_code == 503
        assert not (tmp_path / ".cache").exists()
    asyncio.run(scenario())


def test_importing_gui_does_not_require_fcntl_in_a_fresh_process():
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'fcntl': raise ImportError('Windows has no fcntl')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import qte.gui
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows handle acceptance requires Windows")
def test_native_windows_document_reader(tmp_path):
    from qte.gui.windows_files import read_windows_file
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "example.md").write_text("# Native Windows", encoding="utf-8")
    assert read_windows_file(tmp_path, "docs/example.md") == b"# Native Windows"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction acceptance requires Windows")
def test_native_windows_rejects_junctions_at_root_and_below(tmp_path):
    from qte.gui.windows_files import read_windows_file
    target = tmp_path / "target"
    target.mkdir()
    (target / "example.md").write_text("outside logical root", encoding="utf-8")
    junction = tmp_path / "junction"
    command = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                             capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    for root, relative in ((tmp_path, "junction/example.md"), (junction, "example.md")):
        with pytest.raises(SafeReadError):
            read_windows_file(root, relative)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows sharing acceptance requires Windows")
def test_native_windows_pinned_ancestor_denies_reparse_write_handles(tmp_path):
    import ctypes
    from qte.gui.windows_files import _Win32
    api = _Win32()
    pinned = api.open(str(tmp_path), directory=True)
    try:
        other = api._create(str(tmp_path), 0x40000000, 0x7, None, 3, 0x02000000, None)
        if other != ctypes.c_void_p(-1).value:
            api.close(other)
            pytest.fail("pinned ancestor unexpectedly allowed a writer")
        assert ctypes.get_last_error() == 32  # ERROR_SHARING_VIOLATION
    finally:
        api.close(pinned)
