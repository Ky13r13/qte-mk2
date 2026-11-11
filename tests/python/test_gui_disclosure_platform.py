import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from qte.gui import disclosures
from qte.gui.artifact_io import ArtifactIOError
from qte.gui.disclosures import DisclosureJournal


def _payload(identity: str, event: str = "revealed_outside_protocol") -> bytes:
    return json.dumps({
        "schema_version": 1,
        "event": event,
        "identity": identity,
        "recorded_at": "2026-10-27T00:00:00+00:00",
    }).encode()


def _forbid_posix(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("POSIX artifact access must not run")

    monkeypatch.setattr(disclosures, "read_stable", forbidden)
    monkeypatch.setattr(disclosures.os, "open", forbidden)
    monkeypatch.setattr(Path, "lstat", forbidden)


def _windows_backend(monkeypatch, read_stable):
    backend = ModuleType("qte.gui._artifact_io_windows")
    backend.read_stable = read_stable
    monkeypatch.setitem(sys.modules, backend.__name__, backend)


def test_windows_read_uses_bounded_native_backend_only(tmp_path, monkeypatch):
    journal, identity, calls = DisclosureJournal(tmp_path), "sha256:fixture", []
    monkeypatch.setattr(disclosures, "sys", SimpleNamespace(platform="win32"))
    _forbid_posix(monkeypatch)

    def read_stable(root, relative, limit, expected):
        calls.append((root, relative, limit, expected))
        return _payload(identity)

    _windows_backend(monkeypatch, read_stable)
    assert journal.revealed([identity])
    assert calls == [(journal.root,
                      f"revealed_outside_protocol-{journal.key(identity)}.json",
                      4096, None)]


@pytest.mark.parametrize("result", [
    ArtifactIOError("unreadable"),
    b"{",
    _payload("sha256:different"),
    _payload("sha256:fixture", "protected_content"),
])
def test_windows_read_failures_and_mismatches_are_closed(tmp_path, monkeypatch, result):
    journal, identity = DisclosureJournal(tmp_path), "sha256:fixture"
    monkeypatch.setattr(disclosures, "sys", SimpleNamespace(platform="win32"))
    _forbid_posix(monkeypatch)

    def read_stable(*_args):
        if isinstance(result, BaseException):
            raise result
        return result

    _windows_backend(monkeypatch, read_stable)
    assert not journal.revealed([identity])


def test_unsupported_read_is_false_without_filesystem_access(tmp_path, monkeypatch):
    journal = DisclosureJournal(tmp_path)
    monkeypatch.setattr(disclosures, "sys", SimpleNamespace(platform="darwin"))
    _forbid_posix(monkeypatch)
    _windows_backend(monkeypatch, lambda *_args: pytest.fail("Windows backend must not load"))
    assert not journal.revealed(["sha256:fixture"])
    assert not journal.protected("sha256:fixture")


@pytest.mark.parametrize("platform", ["win32", "darwin"])
@pytest.mark.parametrize("identities", [[], ["sha256:fixture"]])
def test_unsupported_writes_reject_empty_and_nonempty_before_io(
        tmp_path, monkeypatch, platform, identities):
    journal = DisclosureJournal(tmp_path)
    monkeypatch.setattr(disclosures, "sys", SimpleNamespace(platform=platform))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("write guard must run before path or record access")

    monkeypatch.setattr(journal, "_ensure_root", forbidden)
    monkeypatch.setattr(journal, "protected", forbidden)
    monkeypatch.setattr(disclosures.os, "open", forbidden)
    monkeypatch.setattr(disclosures.os, "fdopen", forbidden)
    with pytest.raises(OSError, match="unsupported"):
        journal.reveal(identities)
    with pytest.raises(OSError, match="unsupported"):
        journal.mark_protected(identities)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires native Linux filesystem")
def test_linux_reader_and_writer_behavior_remains_available(tmp_path, monkeypatch):
    journal, identity = DisclosureJournal(tmp_path), "sha256:fixture"
    _windows_backend(monkeypatch, lambda *_args: pytest.fail("Windows backend must not load"))
    journal.reveal([identity])
    assert journal.revealed([identity])


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows handles")
def test_native_windows_reads_hand_created_record_but_writes_remain_blocked(tmp_path):
    journal, identity = DisclosureJournal(tmp_path), "sha256:fixture"
    journal.root.mkdir(parents=True)
    record = journal.root / f"revealed_outside_protocol-{journal.key(identity)}.json"
    record.write_bytes(_payload(identity))
    assert journal.revealed([identity])
    record.write_bytes(_payload(identity) + b" " * (4097 - len(_payload(identity))))
    assert record.stat().st_size == 4097
    assert not journal.revealed([identity])
    record.write_bytes(_payload("sha256:different"))
    assert not journal.revealed([identity])
    record.write_bytes(b"corrupt")
    assert not journal.revealed([identity])
    with pytest.raises(OSError, match="unsupported"):
        journal.reveal([identity])
