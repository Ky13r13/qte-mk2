"""Inline form snapshots retain captured identity without path rereads."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil

import pytest

from qte.gui.form_inputs import capture_form_inputs
from qte.gui.inline_inputs import prepare_inline_inputs
from qte.gui.job_inputs import JobInputError, verify_snapshot, write_snapshot
from qte.gui.research_forms import config_origin, prepare_form


ROOT = Path(__file__).resolve().parents[2]


def research_wire(path: str = "data/bars.csv") -> dict:
    return {
        "data": {"adapter": "csv", "path": path, "interval_ns": "3600000000000", "action_free": True},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY", "fast_period": "2",
                     "slow_period": "3", "quantity": "10"},
        "engine": {"initial_cash": 100_000.0, "random_seed": "0"},
        "sampling": {"interval_ns": "3600000000000", "max_staleness_ns": "0", "periods_per_year": 8766},
    }


def lab_wire() -> dict:
    return {"schema_version": 1, "seed": "817", "bars_per_window": "128", "timeframes": ["hourly"]}


@pytest.fixture
def repository(tmp_path):
    (tmp_path / "data").mkdir()
    shutil.copyfile(ROOT / "examples/research-bars.csv", tmp_path / "data/bars.csv")
    return tmp_path


def prepared(repository: Path, kind: str = "research_run"):
    wire = research_wire() if kind == "research_run" else lab_wire()
    captured = capture_form_inputs(repository, kind, config_origin(), wire)
    preview = prepare_form(kind, json.loads(captured.configuration_bytes), captured.data_bytes)
    return prepare_inline_inputs(captured, preview), captured, wire


def test_research_snapshot_has_honest_v2_inventory_and_captured_bytes(repository):
    value, captured, wire = prepared(repository)
    source = repository / "data/bars.csv"
    raw = source.read_bytes()
    source.write_bytes(b"source changed after capture")
    snapshot = write_snapshot(repository, "a" * 32, value)
    assert snapshot.kind == "research_run"
    assert snapshot.original_config_path is None
    assert snapshot.original_data_path == "data/bars.csv"
    assert snapshot.adapter == "csv"
    assert snapshot.data_sha256 == sha256(raw).hexdigest()
    assert set(snapshot.snapshot_hashes) == {
        "submitted-form.json", "effective-config.json", "input.csv", "input-manifest.json",
    }
    assert snapshot.hashes["data_sha256"] == sha256(raw).hexdigest()
    submitted = json.loads((repository / snapshot.input_root / "submitted-form.json").read_text())
    assert submitted == {"schema_version": 1, "kind": "research_run", "config_origin": config_origin(),
                         "configuration": wire}
    effective = json.loads((repository / snapshot.effective_config_path).read_text())
    assert effective["data"]["path"] == "input.csv"
    assert (repository / snapshot.data_path).read_bytes() == raw
    verified = verify_snapshot(repository, snapshot.job_id, snapshot.snapshot_hashes)
    assert verified == snapshot
    assert captured.original_config_path is None


def test_lab_snapshot_has_no_counterfeit_dataset_identity(repository):
    value, _, _ = prepared(repository, "strategy_lab")
    snapshot = write_snapshot(repository, "b" * 32, value)
    assert snapshot.kind == "strategy_lab"
    assert (snapshot.original_config_path, snapshot.original_data_path, snapshot.adapter,
            snapshot.data_path, snapshot.data_sha256) == (None, None, None, None, None)
    assert snapshot.hashes["data_sha256"] is None
    assert set(snapshot.snapshot_hashes) == {
        "submitted-form.json", "effective-config.json", "input-manifest.json",
    }
    assert verify_snapshot(repository, snapshot.job_id, snapshot.snapshot_hashes) == snapshot


def test_prepare_rejects_crossed_preview_and_writer_validates_before_directory(repository):
    value, captured, _ = prepared(repository)
    other_wire = research_wire(); other_wire["engine"]["initial_cash"] = 99_000.0
    other = capture_form_inputs(repository, "research_run", config_origin(), other_wire)
    other_preview = prepare_form("research_run", json.loads(other.configuration_bytes), other.data_bytes)
    with pytest.raises(JobInputError) as error:
        prepare_inline_inputs(captured, other_preview)
    assert error.value.code == "invalid_job_input"

    forged = replace(value, data_sha256="0" * 64)
    with pytest.raises(JobInputError) as error:
        write_snapshot(repository, "c" * 32, forged)
    assert error.value.code == "invalid_job_input"
    assert not (repository / "build/gui/jobs" / ("c" * 32)).exists()


def test_rehashed_tampering_and_downgrade_are_semantically_rejected(repository):
    value, _, _ = prepared(repository)
    snapshot = write_snapshot(repository, "d" * 32, value)
    root = repository / snapshot.input_root
    manifest_path = root / "input-manifest.json"
    os.chmod(root, 0o700); os.chmod(manifest_path, 0o600)
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 1
    content_hashes = dict(manifest["hashes"])
    changed = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    manifest_path.write_bytes(changed)
    os.chmod(manifest_path, 0o400); os.chmod(root, 0o500)
    expected = dict(snapshot.snapshot_hashes)
    expected["input-manifest.json"] = sha256(changed).hexdigest()
    with pytest.raises(JobInputError) as error:
        verify_snapshot(repository, snapshot.job_id, expected)
    assert (error.value.code, error.value.status) == ("snapshot_invalid", 409)
    assert content_hashes == manifest["hashes"]


def test_partial_write_is_not_verifiable_or_reusable(repository, monkeypatch):
    value, _, _ = prepared(repository)
    import qte.gui.inline_inputs as inline
    original, writes = inline._write_exclusive, 0

    def fail_after_one(*args):
        nonlocal writes
        writes += 1
        if writes > 1:
            raise JobInputError("snapshot_invalid", 409, "failed")
        return original(*args)

    monkeypatch.setattr(inline, "_write_exclusive", fail_after_one)
    job_id = "e" * 32
    with pytest.raises(JobInputError):
        write_snapshot(repository, job_id, value)
    monkeypatch.setattr(inline, "_write_exclusive", original)
    with pytest.raises(JobInputError) as error:
        write_snapshot(repository, job_id, value)
    assert error.value.code == "snapshot_exists"
    with pytest.raises(JobInputError) as error:
        verify_snapshot(repository, job_id, {"submitted-form.json": "0" * 64})
    assert error.value.code == "snapshot_invalid"
