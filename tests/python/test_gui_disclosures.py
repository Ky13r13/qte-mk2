"""Fail-closed durability contracts for the independent disclosure journal."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qte.gui import disclosures
from qte.gui.disclosures import DisclosureJournal


def record_path(journal, event, identity):
    return journal.root / f"{event}-{journal.key(identity)}.json"


def test_success_is_immutable_and_exact_repeat_is_idempotent(tmp_path):
    journal, identity = DisclosureJournal(tmp_path), "sha256:fixture"
    journal.reveal([identity])
    path, first = record_path(journal, "revealed_outside_protocol", identity), None
    first = path.read_bytes()
    journal.reveal([identity])
    assert path.read_bytes() == first and journal.revealed([identity])
    value = json.loads(first)
    assert set(value) == {"schema_version", "event", "identity", "recorded_at"}


@pytest.mark.parametrize("mutate", [
    lambda value: b"not-json\n",
    lambda value: b"[" * 1500 + b"0" + b"]" * 1500,
    lambda value: json.dumps({**value, "schema_version": 2}).encode(),
    lambda value: json.dumps({**value, "event": "protected_content"}).encode(),
    lambda value: json.dumps({**value, "identity": "other"}).encode(),
])
def test_existing_invalid_collision_fails_closed_without_overwrite(tmp_path, mutate):
    journal, identity = DisclosureJournal(tmp_path), "sha256:fixture"
    journal.reveal([identity]); path = record_path(journal, "revealed_outside_protocol", identity)
    damaged = mutate(json.loads(path.read_bytes())); path.write_bytes(damaged)
    with pytest.raises(OSError): journal.reveal([identity])
    assert path.read_bytes() == damaged and not journal.revealed([identity])


def test_existing_symlink_or_nonregular_collision_fails_closed(tmp_path):
    journal, identity = DisclosureJournal(tmp_path), "sha256:fixture"
    journal._ensure_root(); path = record_path(journal, "revealed_outside_protocol", identity)
    target = tmp_path / "target"; target.write_text("x")
    path.symlink_to(target)
    with pytest.raises(OSError): journal.reveal([identity])
    path.unlink(); path.mkdir()
    with pytest.raises(OSError): journal.reveal([identity])


def test_oversized_multibyte_and_batch_prevalidation_create_no_storage(tmp_path):
    journal = DisclosureJournal(tmp_path)
    with pytest.raises(OSError, match="exceeds the limit"): journal.reveal(["é" * 3000])
    assert not journal.root.exists()
    with pytest.raises(OSError, match="exceeds the limit"): journal.reveal(["valid", "é" * 3000])
    assert not journal.root.exists()


@pytest.mark.parametrize("stage", ["fdopen", "write", "flush", "fsync", "link", "unlink"])
def test_prepublication_failures_close_and_remove_temporary_files(tmp_path, monkeypatch, stage):
    journal = DisclosureJournal(tmp_path)
    real_fdopen, real_open = disclosures.os.fdopen, disclosures.os.open
    opened = []
    def tracking_open(*args, **kwargs):
        fd = real_open(*args, **kwargs); opened.append(fd)
        return fd
    monkeypatch.setattr(disclosures.os, "open", tracking_open)
    def fail(*args, **kwargs): raise OSError(stage)
    if stage == "fdopen": monkeypatch.setattr(disclosures.os, "fdopen", fail)
    elif stage == "fsync": monkeypatch.setattr(disclosures.os, "fsync", fail)
    elif stage == "link": monkeypatch.setattr(disclosures.os, "link", fail)
    elif stage == "unlink": monkeypatch.setattr(disclosures.os, "unlink", fail)
    elif stage in {"write", "flush"}:
        class Stream:
            def __init__(self, raw): self.raw = raw
            def __enter__(self): return self
            def __exit__(self, *args): self.raw.close()
            def write(self, value):
                if stage == "write": raise OSError(stage)
                return self.raw.write(value)
            def flush(self):
                if stage == "flush": raise OSError(stage)
                return self.raw.flush()
            def fileno(self): return self.raw.fileno()
        monkeypatch.setattr(disclosures.os, "fdopen", lambda fd, *a, **k: Stream(real_fdopen(fd, *a, **k)))
    with pytest.raises(OSError): journal.reveal(["fixture"])
    for fd in opened:
        with pytest.raises(OSError): os.fstat(fd)
    if journal.root.exists() and stage != "unlink":
        assert not list(journal.root.glob(".*.tmp"))


def test_exclusive_temp_open_failure_never_unlinks_unowned_existing_temp(tmp_path, monkeypatch):
    journal = DisclosureJournal(tmp_path); journal._ensure_root()
    temporary = journal.root / ".fixed.tmp"; temporary.write_bytes(b"owned elsewhere")
    monkeypatch.setattr(disclosures, "uuid4", lambda: type("Id", (), {"hex": "fixed"})())
    with pytest.raises(FileExistsError): journal.reveal(["fixture"])
    assert temporary.read_bytes() == b"owned elsewhere"


def test_fdopen_primary_error_survives_close_failure(tmp_path, monkeypatch):
    journal = DisclosureJournal(tmp_path); real_close, real_open = disclosures.os.close, disclosures.os.open
    opened = []
    def tracking_open(*args, **kwargs):
        fd = real_open(*args, **kwargs); opened.append(fd)
        return fd
    def close_after_releasing(fd):
        real_close(fd)
        raise OSError("close")
    monkeypatch.setattr(disclosures.os, "open", tracking_open)
    monkeypatch.setattr(disclosures.os, "fdopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("fdopen")))
    monkeypatch.setattr(disclosures.os, "close", close_after_releasing)
    with pytest.raises(OSError, match="fdopen"):
        journal.reveal(["fixture"])
    for fd in opened:
        with pytest.raises(OSError): os.fstat(fd)


def test_postpublication_directory_fsync_failure_preserves_valid_evidence(tmp_path, monkeypatch):
    journal = DisclosureJournal(tmp_path); real_fsync, calls = disclosures.os.fsync, 0
    def fail_directory(fd):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError("directory")
        return real_fsync(fd)
    monkeypatch.setattr(disclosures.os, "fsync", fail_directory)
    with pytest.raises(OSError): journal.reveal(["fixture"])
    assert journal.revealed(["fixture"])


def test_cleanup_only_failure_is_reported_without_deleting_published_record(tmp_path, monkeypatch):
    journal = DisclosureJournal(tmp_path); real_unlink, calls = disclosures.os.unlink, 0
    def fail_temp(name, *args, **kwargs):
        nonlocal calls
        calls += 1
        if str(name).startswith("."): raise OSError("cleanup")
        return real_unlink(name, *args, **kwargs)
    monkeypatch.setattr(disclosures.os, "unlink", fail_temp)
    with pytest.raises(OSError, match="cleanup"): journal.reveal(["fixture"])
    assert journal.revealed(["fixture"])


def test_primary_write_error_survives_cleanup_error(tmp_path, monkeypatch):
    journal = DisclosureJournal(tmp_path)
    monkeypatch.setattr(disclosures.os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError("link")))
    monkeypatch.setattr(disclosures.os, "unlink", lambda *_a, **_k: (_ for _ in ()).throw(OSError("cleanup")))
    with pytest.raises(OSError, match="link"):
        journal.reveal(["fixture"])


def test_collision_validation_stays_in_the_pinned_directory(tmp_path, monkeypatch):
    journal, identity = DisclosureJournal(tmp_path), "fixture"
    journal.reveal([identity])
    path, expected = record_path(journal, "revealed_outside_protocol", identity), None
    expected = path.read_bytes()
    old_root, real_link = tmp_path / "pinned", disclosures.os.link
    def replace_root_then_link(*args, **kwargs):
        journal.root.rename(old_root)
        journal.root.mkdir(parents=True)
        record_path(journal, "revealed_outside_protocol", identity).write_text("not-json\n")
        return real_link(*args, **kwargs)
    monkeypatch.setattr(disclosures.os, "link", replace_root_then_link)
    journal.reveal([identity])
    assert (old_root / path.name).read_bytes() == expected
    assert not journal.revealed([identity])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_fifo_collision_fails_promptly_without_consuming_it(tmp_path, monkeypatch):
    journal, identity = DisclosureJournal(tmp_path), "fixture"
    journal._ensure_root(); path = record_path(journal, "revealed_outside_protocol", identity)
    os.mkfifo(path)
    real_open = disclosures.os.open
    def require_nonblocking(name, flags, *args, **kwargs):
        # Fail before entering a blocking open if the production flag regresses.
        if name == path.name:
            assert flags & os.O_NONBLOCK
        return real_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(disclosures.os, "open", require_nonblocking)
    with pytest.raises(OSError, match="collision"):
        journal.reveal([identity])
    assert path.exists() and not list(journal.root.glob(".*.tmp"))
