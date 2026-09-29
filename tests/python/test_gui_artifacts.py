from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from qte.gui.artifact_io import ArtifactIOError, inventory, read_stable
from qte.gui.artifacts import ArtifactError, inspect_artifact, strict_json


def _write_single(root: Path, *, complete: bool = True) -> Path:
    root.mkdir(parents=True)
    files = {
        "manifest.json": json.dumps({"configuration": {}, "dataset_end_ns": 2, "dataset_hash": "hash",
            "dataset_hash_algorithm": "fixture", "dataset_start_ns": 1, "execution_model": "fixture",
            "normalized_engine_config": "fixture", "research_label": "fixture", "sampling": None,
            "schema_version": 1, "source": {"provider": "csv"}, "source_id": "fixture",
            "source_identity": "sha256:fixture", "strategy": {}}).encode(),
        "report.json": json.dumps({"fill_count": 0, "open_trade_count": 0, "order_count": 0,
            "trade_count": 0, "metrics": {"total_return": {"value": 0.0, "undefined_reason": ""}},
            "returns": [0.0]}).encode(),
        "equity.csv": b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n",
        "fills.csv": b"fill_id,order_id,symbol,side,quantity,timestamp_ns,price,commission\n",
        "orders.csv": b"order_id,symbol,status,detail\n",
    }
    for name, data in files.items(): (root / name).write_bytes(data)
    if complete:
        checks = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checks}))
    return root


def test_single_reader_verifies_exact_inventory_and_unknown_evidence(tmp_path):
    artifact = inspect_artifact(_write_single(tmp_path / "single"))
    assert artifact.kind == "single_run_v1"
    assert artifact.metadata["integrity"] == "verified"
    assert artifact.metadata["evidence"] == "unknown"
    assert all(row["available"] for row in artifact.files(False))


def test_single_reader_supports_sampled_equity(tmp_path):
    root = _write_single(tmp_path / "sampled")
    manifest = json.loads((root / "manifest.json").read_text()); manifest["sampling"] = {"policy": "fixture"}
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "sampled_equity.csv").write_text("timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n")
    complete = json.loads((root / "complete.json").read_text())
    for name in ("manifest.json", "sampled_equity.csv"):
        complete["sha256"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / "complete.json").write_text(json.dumps(complete))
    assert "sampled_equity.csv" in inspect_artifact(root).known


def test_incomplete_and_unsupported_default_to_protected(tmp_path):
    incomplete = inspect_artifact(_write_single(tmp_path / "incomplete", complete=False))
    assert incomplete.metadata["export"] == "incomplete"
    assert not any(row["available"] for row in incomplete.files(False))
    unknown = tmp_path / "unknown"; unknown.mkdir(); (unknown / "attachment.bin").write_bytes(b"opaque")
    unsupported = inspect_artifact(unknown)
    assert unsupported.kind == "unsupported"
    assert unsupported.files(False)[0]["protection"] == "unclassified"
    assert not unsupported.files(False)[0]["available"]


def test_tamper_extra_file_duplicate_json_and_nonfinite_are_rejected(tmp_path):
    root = _write_single(tmp_path / "tampered")
    (root / "extra.txt").write_text("not declared")
    with pytest.raises(ArtifactError): inspect_artifact(root)
    assert strict_json(b'{"x":1}') == {"x": 1}
    for body in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e309}'):
        with pytest.raises(ValueError): strict_json(body)


@pytest.mark.parametrize(("name", "value"), [
    ("manifest.json", b'{"schema_version":true}'),
    ("manifest.json", b"[]"),
    ("report.json", b"[]"),
    ("equity.csv", b"anything,goes\n1,2\n"),
])
def test_declared_but_malformed_known_files_are_invalid(tmp_path, name, value):
    root = _write_single(tmp_path / "malformed")
    (root / name).write_bytes(value)
    complete = json.loads((root / "complete.json").read_text())
    complete["sha256"][name] = hashlib.sha256(value).hexdigest()
    (root / "complete.json").write_text(json.dumps(complete))
    with pytest.raises(ArtifactError): inspect_artifact(root)


def test_inventory_rejects_symlink_and_fifo_without_hanging(tmp_path):
    root = tmp_path / "unsafe"; root.mkdir()
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(ArtifactIOError): inventory(root)
    (root / "link").unlink(); os.mkfifo(root / "pipe")
    with pytest.raises(ArtifactIOError): inventory(root)


@pytest.mark.parametrize("name", [".env", "paper.sqlite3", "private.pem", ".git"])
def test_inventory_rejects_sensitive_entries(tmp_path, name):
    root = tmp_path / "sensitive"; root.mkdir()
    (root / name).write_text("secret")
    with pytest.raises(ArtifactIOError): inventory(root)


def test_stable_read_detects_post_inventory_mutation(tmp_path):
    root = tmp_path / "changed"; root.mkdir(); path = root / "x"; path.write_bytes(b"a")
    snap = inventory(root)["x"]
    path.write_bytes(b"b")
    with pytest.raises(ArtifactIOError): read_stable(root, "x", expected=snap)
