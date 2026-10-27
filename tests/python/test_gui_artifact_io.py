"""Linux artifact-storage facade contracts, including bounded descriptor reads."""
from __future__ import annotations

from dataclasses import replace
import builtins
from pathlib import Path
import os
import sys
from types import SimpleNamespace

import pytest

from qte.gui import _artifact_io_posix as posix
from qte.gui import artifact_io
from qte.gui.artifact_io import (
    ArtifactIOError, Snapshot, inventory, inventory_fingerprint, read_stable,
    validate_root,
)


POSIX = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux descriptor backend")


def make_root(tmp_path: Path, relative: str = "build/artifacts/fixture") -> Path:
    root = tmp_path / relative
    root.mkdir(parents=True)
    return root


@POSIX
def test_validate_root_accepts_nested_declared_root_and_rejects_aliases(tmp_path):
    root = make_root(tmp_path)
    assert validate_root(tmp_path, "build/artifacts/fixture") == root
    gui = make_root(tmp_path, "build/gui/jobs/job-1/artifacts")
    assert validate_root(tmp_path, "build/gui/jobs/job-1/artifacts") == gui
    for relative in ("", ".", "../build/x", "build", "build/.hidden/x", "build/local-dev/x",
                     "build/python-package/x", "build/gui/jobs/job-1/output"):
        with pytest.raises(ArtifactIOError):
            validate_root(tmp_path, relative)


@POSIX
def test_validate_root_rejects_symlinked_ancestor_or_leaf(tmp_path):
    outside = tmp_path / "outside"; outside.mkdir()
    build = tmp_path / "build"; build.mkdir()
    (build / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactIOError):
        validate_root(tmp_path, "build/linked")
    root = make_root(tmp_path, "build/artifacts/real")
    (root / "leaf").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactIOError):
        validate_root(tmp_path, "build/artifacts/real/leaf")


@POSIX
def test_inventory_is_nested_deterministic_and_fingerprints_identity(tmp_path):
    root = make_root(tmp_path)
    (root / "z.txt").write_bytes(b"z")
    (root / "nested").mkdir(); (root / "nested/a.txt").write_bytes(b"aa")
    snaps = inventory(root)
    assert list(snaps) == ["nested/a.txt", "z.txt"]
    assert snaps["z.txt"].identity() == (
        snaps["z.txt"].device, snaps["z.txt"].inode, 1,
        snaps["z.txt"].mtime_ns, snaps["z.txt"].ctime_ns)
    assert inventory_fingerprint(dict(reversed(list(snaps.items())))) == tuple(
        (name, *snap.identity()) for name, snap in snaps.items())


@POSIX
@pytest.mark.parametrize("name", [".env", "secret.pem", "state.sqlite3"])
def test_inventory_rejects_forbidden_names_and_symlinks(tmp_path, name):
    root = make_root(tmp_path); (root / name).write_bytes(b"x")
    with pytest.raises(ArtifactIOError): inventory(root)
    (root / name).unlink(); (root / "link").symlink_to(tmp_path)
    with pytest.raises(ArtifactIOError): inventory(root)


@POSIX
def test_inventory_enforces_count_and_total_size_boundaries(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr(posix, "INVENTORY_LIMIT", 2)
    for name in ("a", "b"): (root / name).write_bytes(b"x")
    assert len(inventory(root)) == 2
    (root / "c").write_bytes(b"x")
    with pytest.raises(ArtifactIOError, match="inventory exceeds"): inventory(root)
    for path in root.iterdir(): path.unlink()
    monkeypatch.setattr(posix, "INVENTORY_LIMIT", 10)
    monkeypatch.setattr(posix, "TOTAL_ARTIFACT_BYTES", 2)
    (root / "a").write_bytes(b"a"); (root / "b").write_bytes(b"b")
    assert len(inventory(root)) == 2
    (root / "c").write_bytes(b"c")
    with pytest.raises(ArtifactIOError, match="total size"): inventory(root)


def test_inventory_fingerprint_changes_for_all_identity_fields_and_mapping_shape():
    base = Snapshot("a", 1, 2, 3, 4, 5)
    original = inventory_fingerprint({"a": base})
    for field in ("device", "inode", "size", "mtime_ns", "ctime_ns"):
        assert inventory_fingerprint({"a": replace(base, **{field: getattr(base, field) + 1})}) != original
    assert inventory_fingerprint({"renamed": replace(base, name="renamed")}) != original
    assert inventory_fingerprint({"a": base, "b": replace(base, name="b")}) != original
    assert inventory_fingerprint({}) != original


@POSIX
def test_read_stable_honors_exact_file_limit_and_bounded_chunks(tmp_path, monkeypatch):
    root = make_root(tmp_path); payload = b"x" * 130_000; (root / "data").write_bytes(payload)
    assert read_stable(root, "data", len(payload)) == payload
    with pytest.raises(ArtifactIOError): read_stable(root, "data", len(payload) - 1)
    calls, original = [], posix.os.read
    def bounded(fd, amount):
        calls.append(amount); return original(fd, amount)
    monkeypatch.setattr(posix.os, "read", bounded)
    assert read_stable(root, "data", len(payload)) == payload
    assert len(calls) >= 2 and max(calls) <= 65_536


@POSIX
@pytest.mark.parametrize("field", ["device", "inode", "size", "mtime_ns", "ctime_ns"])
def test_read_stable_rejects_every_expected_identity_mismatch(tmp_path, field):
    root = make_root(tmp_path); (root / "data").write_bytes(b"abc")
    snap = inventory(root)["data"]
    expected = replace(snap, **{field: getattr(snap, field) + 1})
    with pytest.raises(ArtifactIOError): read_stable(root, "data", expected=expected)


@POSIX
def test_read_stable_detects_replacement_and_mutation_during_read(tmp_path, monkeypatch):
    root = make_root(tmp_path); path = root / "data"; path.write_bytes(b"a" * 70_000)
    snapshot = inventory(root)["data"]
    replacement = root / "replacement"; replacement.write_bytes(b"b" * 70_000)
    os.replace(replacement, path)
    with pytest.raises(ArtifactIOError): read_stable(root, "data", expected=snapshot)
    path.write_bytes(b"c" * 70_000)
    original, changed = posix.os.read, False
    def mutate(fd, amount):
        nonlocal changed
        chunk = original(fd, amount)
        if not changed:
            changed = True; path.write_bytes(b"d" * 70_000)
        return chunk
    monkeypatch.setattr(posix.os, "read", mutate)
    with pytest.raises(ArtifactIOError): read_stable(root, "data")


@POSIX
@pytest.mark.parametrize("oversized", [False, True])
def test_read_stable_closes_every_descriptor_on_success_and_failure(tmp_path, monkeypatch, oversized):
    root = make_root(tmp_path); nested = root / "nested"; nested.mkdir()
    (nested / "data").write_bytes(b"abcdef")
    opened, closed = [], []
    open_original, close_original = posix.os.open, posix.os.close
    def tracked_open(*args, **kwargs):
        fd = open_original(*args, **kwargs); opened.append(fd); return fd
    def tracked_close(fd):
        closed.append(fd); return close_original(fd)
    monkeypatch.setattr(posix.os, "open", tracked_open)
    monkeypatch.setattr(posix.os, "close", tracked_close)
    if oversized:
        with pytest.raises(ArtifactIOError): read_stable(root, "nested/data", limit=5)
    else:
        assert read_stable(root, "nested/data", limit=6) == b"abcdef"
    assert closed == list(reversed(opened))


@POSIX
def test_inventory_closes_pending_child_descriptor_on_failure(tmp_path, monkeypatch):
    root = make_root(tmp_path); (root / "child").mkdir(); (root / ".bad").write_bytes(b"x")
    opened, closed, calls = [], [], 0
    open_original, close_original, list_original = posix.os.open, posix.os.close, posix.os.listdir
    def tracked_open(*args, **kwargs):
        fd = open_original(*args, **kwargs); opened.append(fd); return fd
    def tracked_close(fd): closed.append(fd); return close_original(fd)
    def ordered(fd):
        nonlocal calls
        calls += 1
        return ["child", ".bad"] if calls == 1 else list_original(fd)
    monkeypatch.setattr(posix.os, "open", tracked_open)
    monkeypatch.setattr(posix.os, "close", tracked_close)
    monkeypatch.setattr(posix.os, "listdir", ordered)
    with pytest.raises(ArtifactIOError, match="forbidden"): inventory(root)
    assert set(closed) == set(opened) and len(closed) == len(opened)


def test_unsupported_platform_fails_before_backend_or_filesystem(monkeypatch, tmp_path):
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        fromlist = kwargs.get("fromlist", args[2] if len(args) >= 3 else ()) or ()
        if "_artifact_io_posix" in name or "_artifact_io_posix" in fromlist:
            pytest.fail("backend imported")
        return original_import(name, *args, **kwargs)
    def forbidden(*_args, **_kwargs): pytest.fail("filesystem accessed")
    with monkeypatch.context() as scoped:
        scoped.setattr(artifact_io, "sys", SimpleNamespace(platform="win32"))
        scoped.setattr(builtins, "__import__", guarded_import)
        scoped.setattr(Path, "lstat", forbidden)
        scoped.setattr(posix.os, "open", forbidden)
        for call in (
            lambda: validate_root(tmp_path, "build/artifacts/x"),
            lambda: inventory(tmp_path),
            lambda: read_stable(tmp_path, "file"),
        ):
            with pytest.raises(ArtifactIOError, match="unsupported"):
                call()
