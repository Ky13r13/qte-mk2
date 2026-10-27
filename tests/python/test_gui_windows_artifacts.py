"""Private Win32 artifact-storage contracts; fake handles are not native acceptance."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from qte.gui._artifact_io_windows import (
    ArtifactIOError, FILE_LIMIT, INVENTORY_LIMIT, MAX_DEPTH, TOTAL_ARTIFACT_BYTES,
    WindowsSnapshot, _inventory, _read_stable, _validate_root,
)
from qte.gui import _artifact_io_windows as windows_io
from qte.gui.windows_artifact_handles import WindowsFileInfo


DIRECTORY, REPARSE, FILE = 0x10, 0x400, 1


class Scan:
    def __init__(self, names, *, error=False): self.names, self.error, self.closed, self.index = names, error, False, 0
    def __iter__(self): return self
    def __next__(self):
        if self.error: raise OSError("private scan failure")
        if self.index == len(self.names): raise StopIteration
        name = self.names[self.index]; self.index += 1
        return type("Entry", (), {"name": name})()
    def close(self): self.closed = True


class Handles:
    def __init__(self, *, files=None, directories=(), fail_open=(), fail_read=False, changed=False):
        self.files = files or {}; self.directories = set(directories); self.fail_open = set(fail_open)
        self.fail_read, self.changed, self.opened, self.closed, self.reads, self.info_counts, self.cursors = fail_read, changed, [], [], [], {}, {}
    def open(self, path, *, directory):
        if path in self.fail_open or path.rsplit("\\", 1)[-1] in self.fail_open:
            raise OSError("private open failure")
        handle = len(self.opened); self.opened.append((path, directory)); return handle
    def path(self, handle): return "\\\\?\\Volume{fixture}\\" + str(handle)
    def artifact_info(self, handle):
        self.info_counts[handle] = self.info_counts.get(handle, 0) + 1
        path, requested_directory = self.opened[handle]
        name = path.rsplit("\\", 1)[-1]
        if name in self.files:
            data = self.files[name]
            changed = 9 if self.changed and self.info_counts[handle] > 1 else 8
            return WindowsFileInfo(0, 7, b"f" * 16, len(data), 8, changed, 1, FILE)
        changed = 9 if self.changed and self.info_counts[handle] > 2 else 8
        return WindowsFileInfo(DIRECTORY, 7, b"d" * 16, 0, 8, changed, 1, FILE)
    def read(self, handle, size):
        if self.fail_read: raise OSError("private read failure")
        self.reads.append(size)
        name = self.opened[handle][0].rsplit("\\", 1)[-1]
        offset = self.cursors.get(handle, 0); self.cursors[handle] = offset + size
        return self.files.get(name, b"")[offset:offset + size]
    def close(self, handle): self.closed.append(handle)


def scan_map(mapping, seen):
    batches = iter(mapping.values())
    def scan(path):
        del path
        names = next(batches, [])
        item = names if isinstance(names, Scan) else Scan(names)
        seen.append(item); return item
    return scan


def snapshot(name="data", **changes):
    return replace(WindowsSnapshot(name, 3, 7, b"f" * 16, 8, 8, 0, 1), **changes)


def test_snapshot_identity_is_tagged_and_sensitive_to_every_field():
    item = snapshot()
    assert item.identity() == ("windows_file_id_v1", 7, b"f" * 16, 3, 8, 8, 0, 1)
    for field, value in (("volume", 9), ("file_id", b"g" * 16), ("size", 4),
                         ("modified", 9), ("changed", 9), ("attributes", 2), ("links", 2)):
        assert replace(item, **{field: value}).identity() != item.identity()


def test_inventory_nested_names_are_sorted_and_scanners_handles_close():
    api = Handles(files={"a.txt": b"a", "z.txt": b"zz"})
    seen = []
    values = _inventory("C:\\root", api, scan_map({"C:\\root": ["nested", "z.txt"],
        "C:\\root\\nested": ["a.txt"]}, seen))
    assert list(values) == ["nested/a.txt", "z.txt"]
    assert all(item.closed for item in seen)
    assert set(api.closed) == set(range(len(api.opened))) and len(api.closed) == len(api.opened)


@pytest.mark.parametrize("name", [".env", "secret.pem", "state.SQLITE3", "..", "a/b", "NUL"])
def test_inventory_rejects_sensitive_and_alias_entries_without_leaking_names(name):
    api = Handles(); seen = []
    with pytest.raises(ArtifactIOError) as error:
        _inventory("C:\\root", api, scan_map({"C:\\root": [name]}, seen))
    assert name not in str(error.value)
    assert all(item.closed for item in seen)
    assert set(api.closed) == set(range(len(api.opened))) and len(api.closed) == len(api.opened)


def test_inventory_rejects_reparse_and_directory_change():
    class Reparse(Handles):
        def artifact_info(self, handle):
            value = super().artifact_info(handle)
            return replace(value, attributes=DIRECTORY | REPARSE)
    with pytest.raises(ArtifactIOError): _inventory("C:\\root", Reparse(), scan_map({"C:\\root": ["dir"]}, []))
    with pytest.raises(ArtifactIOError): _inventory("C:\\root", Handles(changed=True), scan_map({"C:\\root": []}, []))


def test_inventory_rejects_casefold_collision_and_nested_depth_bound():
    with pytest.raises(ArtifactIOError):
        _inventory("C:\\root", Handles(files={"A.txt": b"a", "a.TXT": b"a"}),
                   scan_map({"C:\\root": ["A.txt", "a.TXT"]}, []))
    seen, batches = [], [[f"d{index}"] for index in range(MAX_DEPTH + 2)]
    def scan(_path):
        item = Scan(batches.pop(0) if batches else []); seen.append(item); return item
    with pytest.raises(ArtifactIOError, match="depth"):
        _inventory("C:\\root", Handles(), scan)
    assert all(item.closed for item in seen)


def test_inventory_count_and_total_bounds_are_independent(monkeypatch):
    api = Handles(); names = [f"f{index}" for index in range(INVENTORY_LIMIT + 1)]
    with pytest.raises(ArtifactIOError, match="inventory exceeds"):
        _inventory("C:\\root", api, scan_map({"C:\\root": names}, []))
    monkeypatch.setattr(windows_io, "TOTAL_ARTIFACT_BYTES", 2)
    exact = Handles(files={"a": b"a", "b": b"b"})
    assert len(_inventory("C:\\root", exact, scan_map({"C:\\root": ["a", "b"]}, []))) == 2
    too_large = Handles(files={"a": b"a", "b": b"b", "c": b"c"})
    with pytest.raises(ArtifactIOError, match="total size"):
        _inventory("C:\\root", too_large, scan_map({"C:\\root": ["a", "b", "c"]}, []))


@pytest.mark.parametrize("field", ["volume", "file_id", "size", "modified", "changed", "attributes", "links"])
def test_read_rejects_all_expected_identity_mismatches_and_cross_backend_snapshot(field):
    api = Handles(files={"data": b"abc"})
    expected = replace(snapshot(), **{field: (b"g" * 16 if field == "file_id" else getattr(snapshot(), field) + 1)})
    with pytest.raises(ArtifactIOError): _read_stable("C:\\root", "data", 3, expected, api)
    with pytest.raises(ArtifactIOError): _read_stable("C:\\root", "data", 3, object(), api)


def test_read_rejects_bad_path_and_expected_name_and_obeys_limits():
    api = Handles(files={"data": b"abc"})
    for relative in ("", "../data", "a/../data", "C:\\data", "a//data"):
        with pytest.raises(ArtifactIOError): _read_stable("C:\\root", relative, 3, None, api)
    with pytest.raises(ArtifactIOError): _read_stable("C:\\root", "data", 3, snapshot("other"), api)
    assert _read_stable("C:\\root", "data", 3, None, api) == b"abc"
    with pytest.raises(ArtifactIOError): _read_stable("C:\\root", "data", 2, None, api)


def test_read_uses_bounded_chunks_and_rejects_oversized_api_chunk():
    payload = b"x" * 130_000
    api = Handles(files={"data": payload})
    assert _read_stable("C:\\root", "data", len(payload), None, api) == payload
    assert len(api.reads) >= 2 and max(api.reads) <= 65_536
    class OversizedChunk(Handles):
        def read(self, handle, size): return b"x" * (size + 1)
    with pytest.raises(ArtifactIOError, match="read is invalid"):
        _read_stable("C:\\root", "data", 3, None, OversizedChunk(files={"data": b"abc"}))


def test_read_detects_changed_file_and_closes_on_open_or_read_failure():
    with pytest.raises(ArtifactIOError): _read_stable("C:\\root", "data", 3, None, Handles(files={"data": b"abc"}, changed=True))
    for api in (Handles(files={"data": b"abc"}, fail_open={"data"}), Handles(files={"data": b"abc"}, fail_read=True)):
        with pytest.raises(ArtifactIOError) as error: _read_stable("C:\\root", "data", 3, None, api)
        assert "private" not in str(error.value)
        assert set(api.closed) == set(range(len(api.opened))) and len(api.closed) == len(api.opened)


def test_missing_handle_metadata_is_not_replaced_by_path_or_directory_metadata():
    class MissingInfo(Handles):
        def artifact_info(self, _handle): raise OSError("private metadata failure")
    for call in (lambda: _inventory("C:\\root", MissingInfo(), scan_map({"C:\\root": []}, [])),
                 lambda: _read_stable("C:\\root", "data", 3, None, MissingInfo(files={"data": b"abc"}))):
        with pytest.raises(ArtifactIOError) as error: call()
        assert "private" not in str(error.value)


def test_inventory_scanner_creation_and_iteration_failures_close_ownership():
    api = Handles()
    def creation_failure(_path): raise OSError("private creation failure")
    with pytest.raises(ArtifactIOError) as error: _inventory("C:\\root", api, creation_failure)
    assert "private" not in str(error.value)
    assert set(api.closed) == set(range(len(api.opened)))
    api, seen = Handles(), []
    with pytest.raises(ArtifactIOError) as error:
        _inventory("C:\\root", api, scan_map({"C:\\root": Scan([], error=True)}, seen))
    assert "private" not in str(error.value)
    assert all(item.closed for item in seen) and set(api.closed) == set(range(len(api.opened)))


def test_validate_root_rejects_aliases_and_reparse_without_disclosing_paths():
    api = Handles()
    for relative in ("", ".", "../x", "build", "build/.private/x", "build/local-dev/x", "build/gui/jobs/id/out"):
        with pytest.raises(ArtifactIOError): _validate_root("C:\\repo", relative, api)
    class Reparse(Handles):
        def artifact_info(self, handle): return replace(super().artifact_info(handle), attributes=DIRECTORY | REPARSE)
    with pytest.raises(ArtifactIOError): _validate_root("C:\\repo", "build/artifacts/x", Reparse())


@pytest.mark.parametrize("kind", ["root", "ancestor", "child_directory", "file"])
def test_reparse_is_rejected_at_each_pinned_location(kind):
    class ReparseAt(Handles):
        def artifact_info(self, handle):
            value = super().artifact_info(handle)
            path = self.opened[handle][0]
            matches = ((kind == "root" and handle == 0)
                       or (kind == "ancestor" and handle == 1)
                       or (kind == "child_directory" and path.endswith("\\dir"))
                       or (kind == "file" and path.endswith("\\data")))
            return replace(value, attributes=value.attributes | REPARSE) if matches else value
    api = ReparseAt(files={"data": b"x"})
    if kind in {"root", "ancestor"}:
        with pytest.raises(ArtifactIOError): _validate_root("C:\\repo", "build/artifacts/x", api)
    elif kind == "child_directory":
        with pytest.raises(ArtifactIOError): _inventory("C:\\root", api, scan_map({"C:\\root": ["dir"]}, []))
    else:
        with pytest.raises(ArtifactIOError): _inventory("C:\\root", api, scan_map({"C:\\root": ["data"]}, []))


def test_validate_root_enforces_pinned_ancestor_depth_bound():
    relative = "build/" + "/".join("part" for _ in range(MAX_DEPTH + 2))
    with pytest.raises(ArtifactIOError, match="depth"):
        _validate_root("C:\\repo", relative, Handles())


def test_native_wrappers_fail_closed_off_windows_and_sanitize_missing_win32_api(monkeypatch, tmp_path):
    with monkeypatch.context() as scoped:
        scoped.setattr(windows_io, "sys", type("Platform", (), {"platform": "linux"})())
        scoped.setattr(windows_io, "WindowsArtifactAPI", lambda: pytest.fail("Windows API constructed"))
        for call in (lambda: windows_io.validate_root(tmp_path, "build/artifacts/x"),
                     lambda: windows_io.inventory(tmp_path), lambda: windows_io.read_stable(tmp_path, "x")):
            with pytest.raises(ArtifactIOError, match="native Windows"): call()
    with monkeypatch.context() as scoped:
        scoped.setattr(windows_io, "sys", type("Platform", (), {"platform": "win32"})())
        scoped.setattr(windows_io, "WindowsArtifactAPI", lambda: (_ for _ in ()).throw(AttributeError("private")))
        with pytest.raises(ArtifactIOError, match="native Windows") as error:
            windows_io.inventory(tmp_path)
        assert "private" not in str(error.value)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows artifact acceptance requires Windows")
def test_native_windows_inventory_and_read(tmp_path):
    root = tmp_path / "artifact"; root.mkdir(); (root / "data.txt").write_bytes(b"fixture")
    snaps = windows_io.inventory(root)
    assert windows_io.read_stable(root, "data.txt", 7, snaps["data.txt"]) == b"fixture"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction acceptance requires Windows")
def test_native_windows_rejects_junction(tmp_path):
    target, root = tmp_path / "target", tmp_path / "root"; target.mkdir(); root.mkdir()
    junction = root / "junction"
    subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)], check=True, capture_output=True)
    with pytest.raises(ArtifactIOError): windows_io.inventory(root)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows replacement acceptance requires Windows")
def test_native_windows_replacement_invalidates_expected_snapshot(tmp_path):
    root = tmp_path / "root"; root.mkdir(); path = root / "data"; path.write_bytes(b"old")
    expected = windows_io.inventory(root)["data"]
    replacement = root / "replacement"
    replacement.write_bytes(b"new")
    replacement_id = windows_io.inventory(root)["replacement"]
    assert (replacement_id.volume, replacement_id.file_id) != (expected.volume, expected.file_id)
    replacement.replace(path)
    with pytest.raises(ArtifactIOError, match="changed before read"):
        windows_io.read_stable(root, "data", 3, expected)
