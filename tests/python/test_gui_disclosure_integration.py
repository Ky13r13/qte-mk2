"""A failed journal publication must not report a successful GUI disclosure."""

from __future__ import annotations

import hashlib
import json

import pytest

from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from test_gui_catalog import _unknown


def _conflict(journal, identity, event, damage):
    journal.root.mkdir(parents=True, exist_ok=True)
    record = journal.root / f"{event}-{journal.key(identity)}.json"
    if damage == "symlink":
        record.symlink_to(journal.root / "missing-target")
    elif damage == "invalid_json":
        record.write_bytes(b"{")
    elif damage == "nested_json":
        record.write_bytes(b"[" * 1500 + b"0" + b"]" * 1500)
    else:
        record.write_text(json.dumps({"schema_version": 1, "event": event,
                                     "identity": "a different identity",
                                     "recorded_at": "2026-10-27T00:00:00+00:00"}))
    return record


def _assert_preserved(record, damage):
    if damage == "symlink":
        assert record.is_symlink()
        assert not record.exists()
    elif damage == "invalid_json":
        assert record.read_bytes() == b"{"
    elif damage == "nested_json":
        assert record.read_bytes() == b"[" * 1500 + b"0" + b"]" * 1500
    else:
        assert json.loads(record.read_text())["identity"] == "a different identity"
    assert not list(record.parent.glob(".*.tmp"))


@pytest.mark.parametrize("damage", ["invalid_json", "nested_json", "wrong_identity", "symlink"])
def test_corrupt_protection_collision_prevents_registration(tmp_path, damage):
    content = b"protected input evidence"
    catalog = Catalog(tmp_path)
    relative = _unknown(tmp_path, "protected", content)
    identity = "file-sha256:" + hashlib.sha256(content).hexdigest()
    record = _conflict(catalog.disclosures, identity, "protected_content", damage)

    with pytest.raises(ArtifactError) as error:
        catalog.register(relative)

    assert (error.value.code, error.value.status) == ("disclosure_unavailable", 503)
    assert not catalog.database.exists()
    _assert_preserved(record, damage)


@pytest.mark.parametrize("damage", ["invalid_json", "nested_json", "wrong_identity", "symlink"])
def test_corrupt_reveal_collision_returns_error_and_keeps_content_locked(tmp_path, damage):
    catalog = Catalog(tmp_path)
    detail = catalog.register(_unknown(tmp_path, "protected"))
    identity, = catalog.inspect(detail["id"]).protected_identities()
    record = _conflict(catalog.disclosures, identity, "revealed_outside_protocol", damage)

    with pytest.raises(ArtifactError) as error:
        catalog.reveal(detail["id"], "reveal outside protocol")

    assert (error.value.code, error.value.status) == ("disclosure_unavailable", 503)
    _assert_preserved(record, damage)
    # Restarting the catalog cannot convert a failed write into authorization.
    restarted = Catalog(tmp_path)
    refreshed = restarted.get(detail["id"])
    assert refreshed["needs_disclosure"] is True
    assert refreshed["files"][0]["available"] is False
    with pytest.raises(ArtifactError) as blocked:
        restarted.read_file(detail["id"], detail["files"][0]["id"])
    assert (blocked.value.code, blocked.value.status) == ("holdout_protected", 403)
