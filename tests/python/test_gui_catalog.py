from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog


def _unknown(repo: Path, name: str, data: bytes = b"secret") -> str:
    root = repo / "build" / name; root.mkdir(parents=True); (root / "attachment.bin").write_bytes(data)
    return f"build/{name}"


def _single(repo: Path, name: str, equity: bytes) -> str:
    root = repo / "build" / name; root.mkdir(parents=True)
    manifest = {"configuration": {}, "dataset_end_ns": 2, "dataset_hash": "x", "dataset_hash_algorithm": "x",
        "dataset_start_ns": 1, "execution_model": "x", "normalized_engine_config": "x", "research_label": "x",
        "sampling": None, "schema_version": 1, "source": {}, "source_id": "x", "source_identity": "x", "strategy": {}}
    report = {"fill_count": 0, "open_trade_count": 0, "order_count": 0, "trade_count": 0,
        "metrics": {"total_return": {"value": 0.0, "undefined_reason": ""}}, "returns": [0.0]}
    files = {"manifest.json": json.dumps(manifest).encode(), "report.json": json.dumps(report).encode(),
        "equity.csv": equity, "fills.csv": b"fill_id,order_id,symbol,side,quantity,timestamp_ns,price,commission\n",
        "orders.csv": b"order_id,symbol,status,detail\n"}
    for filename, data in files.items(): (root / filename).write_bytes(data)
    checks = {filename: hashlib.sha256(data).hexdigest() for filename, data in files.items()}
    (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checks}))
    return f"build/{name}"


def test_catalog_is_lazy_explicit_and_persistent(tmp_path):
    catalog = Catalog(tmp_path)
    assert catalog.list() == ([], 0)
    assert not catalog.database.exists()
    path = _unknown(tmp_path, "manual-20260926")
    detail = catalog.register(path)
    assert catalog.database.is_file()
    assert detail["artifact_date"] == "2026-09-26"
    assert detail["status"]["integrity"] == "unchecked"
    assert detail["has_protected"] is True
    assert catalog.get(detail["id"])["id"] == detail["id"]
    assert Catalog(tmp_path).list()[1] == 1
    assert "files" not in Catalog(tmp_path).list()[0][0]


def test_unprotected_single_does_not_create_disclosure_storage(tmp_path):
    catalog = Catalog(tmp_path)
    catalog.register(_single(tmp_path, "plain", b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n"))
    assert not catalog.disclosures.root.exists()


def test_catalog_keeps_invalid_and_incomplete_entries_visible(tmp_path):
    root = tmp_path / "build/bad"; root.mkdir(parents=True)
    (root / "manifest.json").write_text('{"schema_version":1}')
    (root / "complete.json").write_text('{"schema_version":1,"sha256":{}}')
    detail = Catalog(tmp_path).register("build/bad")
    assert detail["status"]["integrity"] == "invalid"
    assert detail["errors"] == ["artifact_invalid"]


def test_holdout_guard_reveal_and_copy_identity_survive_catalog_rebuild(tmp_path):
    first = _unknown(tmp_path, "first", b"same protected bytes")
    catalog = Catalog(tmp_path); one = catalog.register(first)
    file_id = one["files"][0]["id"]
    with pytest.raises(ArtifactError) as protected: catalog.read_file(one["id"], file_id)
    assert protected.value.code == "holdout_protected"
    with pytest.raises(ArtifactError): catalog.reveal(one["id"], "yes")
    catalog.reveal(one["id"], "reveal outside protocol")
    assert catalog.read_file(one["id"], file_id)[0] == b"same protected bytes"

    catalog.database.unlink()
    second = _unknown(tmp_path, "renamed-copy", b"same protected bytes")
    two = Catalog(tmp_path).register(second)
    assert two["status"]["disclosure"] == "revealed_outside_protocol"
    assert Catalog(tmp_path).read_file(two["id"], two["files"][0]["id"])[0] == b"same protected bytes"


def test_protected_bytes_cannot_be_downgraded_by_a_known_filename(tmp_path):
    equity = b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n"
    first = Catalog(tmp_path).register(_unknown(tmp_path, "unknown", equity))
    single = Catalog(tmp_path).register(_single(tmp_path, "single", equity))
    row = next(item for item in single["files"] if item["name"] == "equity.csv")
    assert row["protection"] == "protected"
    assert row["available"] is False
    assert single["has_protected"] is True


def test_refreshed_protected_bytes_are_classified_before_copy_registration(tmp_path):
    equity = b"timestamp_ns,equity,gross_exposure\n2,101.0,0.0\n"
    catalog = Catalog(tmp_path)
    original = catalog.register(_unknown(tmp_path, "changing", b"first"))
    (tmp_path / "build/changing/attachment.bin").write_bytes(equity)
    assert catalog.get(original["id"])["needs_disclosure"] is True
    copied = catalog.register(_single(tmp_path, "known-copy", equity))
    row = next(item for item in copied["files"] if item["name"] == "equity.csv")
    assert row["protection"] == "protected"
    assert row["available"] is False


def test_previous_reveal_does_not_unlock_new_protected_file(tmp_path):
    catalog = Catalog(tmp_path); detail = catalog.register(_unknown(tmp_path, "growing", b"first"))
    disclosed = catalog.reveal(detail["id"], "reveal outside protocol")
    assert disclosed["needs_disclosure"] is False
    (tmp_path / "build/growing/new.bin").write_bytes(b"second")
    refreshed = catalog.get(detail["id"])
    assert refreshed["status"]["disclosure"] == "revealed_outside_protocol"
    assert refreshed["needs_disclosure"] is True
    assert refreshed["blocked_protected_count"] == 1
    assert sum(not row["available"] for row in refreshed["files"]) == 1


def test_missing_registered_root_remains_visible(tmp_path):
    catalog = Catalog(tmp_path); detail = catalog.register(_unknown(tmp_path, "later-missing"))
    (tmp_path / "build/later-missing/attachment.bin").unlink()
    (tmp_path / "build/later-missing").rmdir()
    assert catalog.get(detail["id"])["errors"] == ["artifact_unavailable"]
    assert catalog.get(detail["id"])["files"] == []
    assert catalog.list()[0][0]["kind"] == "unavailable"


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_added_unsafe_entry_becomes_sanitized_unavailable(tmp_path, unsafe_kind):
    catalog = Catalog(tmp_path); detail = catalog.register(_unknown(tmp_path, "unsafe-later"))
    path = tmp_path / "build/unsafe-later/new-entry"
    if unsafe_kind == "symlink": path.symlink_to(tmp_path)
    else: os.mkfifo(path)
    refreshed = catalog.get(detail["id"])
    assert refreshed["kind"] == "unavailable"
    assert refreshed["files"] == []
    assert catalog.list()[0][0]["errors"] == ["artifact_unavailable"]


def test_corrupt_or_symlinked_disclosure_never_authorizes(tmp_path):
    path = _unknown(tmp_path, "artifact")
    catalog = Catalog(tmp_path); detail = catalog.register(path)
    identity = catalog.inspect(detail["id"]).protected_identities()[0]
    journal = catalog.disclosures
    journal.root.mkdir(parents=True, exist_ok=True)
    record = journal.root / f"revealed_outside_protocol-{journal.key(identity)}.json"
    record.write_text("")
    assert catalog.get(detail["id"])["status"]["disclosure"] == "unknown"
    record.unlink(); record.symlink_to(tmp_path / "missing")
    assert catalog.get(detail["id"])["status"]["disclosure"] == "unknown"
    record.unlink(); os.mkfifo(record)
    assert catalog.get(detail["id"])["status"]["disclosure"] == "unknown"


def test_catalog_rejects_outside_and_symlink_roots_and_storage(tmp_path):
    catalog = Catalog(tmp_path)
    with pytest.raises(ArtifactError): catalog.register("../outside")
    (tmp_path / "build").mkdir(); (tmp_path / "build/link").symlink_to(tmp_path)
    with pytest.raises(ArtifactError): catalog.register("build/link")
    cache_target = tmp_path / "cache-target"; cache_target.mkdir()
    (tmp_path / ".cache").symlink_to(cache_target)
    _unknown(tmp_path, "safe")
    with pytest.raises(ArtifactError): Catalog(tmp_path).register("build/safe")


@pytest.mark.parametrize("path", ["build", "build/gui", "build/gui/disclosures", "build/local-dev", "build/python-package"])
def test_catalog_rejects_broad_and_reserved_roots(tmp_path, path):
    (tmp_path / path).mkdir(parents=True, exist_ok=True)
    with pytest.raises(ArtifactError): Catalog(tmp_path).register(path)
