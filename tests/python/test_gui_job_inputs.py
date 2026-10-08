"""Input acceptance snapshots are bounded and independent of later source edits."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qte.gui import job_inputs
from qte.gui.job_inputs import JobInputError, collect_inputs, verify_snapshot, write_snapshot


def _write_config(root: Path, *, adapter="csv", data_path="../data/bars.csv") -> Path:
    (root / "examples").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    if adapter == "csv":
        (root / "data/bars.csv").write_text("timestamp,symbol,close\n1,ABC,10\n", encoding="utf-8")
    else:
        (root / "data/bars.json").write_text('{"bars":[]}', encoding="utf-8")
    config = {"data": {"adapter": adapter, "path": data_path, "interval_ns": 1},
              "strategy": {"type": "moving_average_regime", "symbol": "ABC", "fast_period": 1,
                           "slow_period": 2, "quantity": 1}, "engine": {"initial_cash": 100}}
    path = root / "examples/run.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_collect_snapshot_and_verify_preserve_copied_bytes(tmp_path):
    _write_config(tmp_path)
    prepared = collect_inputs(tmp_path, "examples/run.json")
    assert prepared.original_data_path == "data/bars.csv"
    assert prepared.config_sha256 != prepared.canonical_config_sha256
    snapshot = write_snapshot(tmp_path, "a" * 32, prepared)
    assert snapshot.input_root == "build/gui/jobs/" + "a" * 32 + "/inputs"
    assert snapshot.effective_config == snapshot.effective_config_path
    assert set(snapshot.snapshot_hashes) == {"original-config.json", "effective-config.json", "input.csv",
                                             "input-manifest.json"}
    assert not (tmp_path / "build/gui/jobs" / ("a" * 32) / "artifacts").exists()
    effective = json.loads((tmp_path / snapshot.effective_config_path).read_text(encoding="utf-8"))
    assert effective["data"]["path"] == "input.csv"
    assert (tmp_path / snapshot.data_path).read_bytes() == prepared.data_bytes
    assert (tmp_path / snapshot.effective_config_path).stat().st_mode & 0o777 == 0o400
    (tmp_path / "data/bars.csv").write_text("changed after acceptance", encoding="utf-8")
    verified = verify_snapshot(tmp_path, snapshot.job_id, snapshot.snapshot_hashes)
    assert verified.hashes == snapshot.hashes
    assert verified.fingerprint == prepared.fingerprint


@pytest.mark.parametrize("target", ["input.csv", "input-manifest.json"])
def test_verify_uses_persisted_hashes_including_manifest(tmp_path, target):
    _write_config(tmp_path)
    snapshot = write_snapshot(tmp_path, "b" * 32, collect_inputs(tmp_path, "examples/run.json"))
    path = tmp_path / snapshot.input_root / target
    os.chmod(path, 0o600)
    path.write_bytes(b"tampered")
    with pytest.raises(JobInputError) as error:
        verify_snapshot(tmp_path, snapshot.job_id, snapshot.snapshot_hashes)
    assert (error.value.code, error.value.status) == ("snapshot_invalid", 409)


@pytest.mark.parametrize("path,adapter,data_name,code", [
    ("examples/run.txt", "csv", "../data/bars.csv", "invalid_job_input"),
    (".private/run.json", "csv", "../data/bars.csv", "input_forbidden"),
    ("examples/run.json", "csv", "../../secret.csv", "input_forbidden"),
    ("examples/run.json", "alpaca", "../data/bars.csv", "invalid_job_input"),
])
def test_collect_rejects_paths_and_adapter_filetype(tmp_path, path, adapter, data_name, code):
    _write_config(tmp_path, adapter=adapter, data_path=data_name)
    if path.endswith(".txt"):
        (tmp_path / "examples/run.txt").write_text("{}", encoding="utf-8")
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, path)
    assert error.value.code == code


def test_collect_rejects_symlink_and_duplicate_json_keys(tmp_path):
    _write_config(tmp_path)
    (tmp_path / "data/link.csv").symlink_to(tmp_path / "data/bars.csv")
    config = json.loads((tmp_path / "examples/run.json").read_text(encoding="utf-8"))
    config["data"]["path"] = "../data/link.csv"
    (tmp_path / "examples/run.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run.json")
    assert error.value.code == "input_forbidden"
    (tmp_path / "examples/run.json").write_text('{"data":{},"data":{}}', encoding="utf-8")
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run.json")
    assert error.value.code == "invalid_job_input"


def test_collect_rejects_fifo_without_blocking(tmp_path):
    _write_config(tmp_path)
    data = tmp_path / "data/bars.csv"
    data.unlink()
    os.mkfifo(data)
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run.json")
    assert (error.value.code, error.value.status) == ("input_forbidden", 403)


@pytest.mark.parametrize(("target", "limit"), [("config", job_inputs.CONFIG_LIMIT),
                                                   ("data", job_inputs.DATA_LIMIT)])
def test_collect_rejects_oversize_regular_input_before_parsing(tmp_path, target, limit):
    _write_config(tmp_path)
    path = tmp_path / ("examples/run.json" if target == "config" else "data/bars.csv")
    path.write_bytes(b"x" * (limit + 1))
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run.json")
    assert (error.value.code, error.value.status) == ("input_forbidden", 403)


def test_collect_rejects_same_descriptor_mutation_during_read(tmp_path, monkeypatch):
    _write_config(tmp_path)
    path = tmp_path / "examples/run.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["padding"] = "x" * 200_000
    path.write_text(json.dumps(config), encoding="utf-8")
    original_read, changed = job_inputs.os.read, False

    def read_then_mutate(fd, length):
        nonlocal changed
        result = original_read(fd, length)
        if result and not changed:
            changed = True
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return result

    monkeypatch.setattr(job_inputs.os, "read", read_then_mutate)
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run.json")
    assert (error.value.code, error.value.status) == ("input_changed", 409)


def test_snapshot_uuid_is_strict_and_never_overwrites(tmp_path):
    _write_config(tmp_path)
    prepared = collect_inputs(tmp_path, "examples/run.json")
    with pytest.raises(JobInputError) as error:
        write_snapshot(tmp_path, "A" * 32, prepared)
    assert error.value.code == "invalid_job_input"
    snapshot = write_snapshot(tmp_path, "c" * 32, prepared)
    with pytest.raises(JobInputError) as error:
        write_snapshot(tmp_path, snapshot.job_id, prepared)
    assert error.value.code == "snapshot_exists"
    with pytest.raises(JobInputError) as error:
        verify_snapshot(tmp_path, "../" + snapshot.job_id, snapshot.snapshot_hashes)
    assert error.value.code == "invalid_job_input"


def test_canonical_hash_normalizes_relative_data_path_and_json_formatting(tmp_path):
    _write_config(tmp_path)
    first = collect_inputs(tmp_path, "examples/run.json")
    config = json.loads((tmp_path / "examples/run.json").read_text(encoding="utf-8"))
    config["data"]["path"] = "../data/./bars.csv"
    (tmp_path / "examples/run.json").write_text(json.dumps(config, indent=4), encoding="utf-8")
    second = collect_inputs(tmp_path, "examples/run.json")
    assert first.config_sha256 != second.config_sha256
    assert first.canonical_config_sha256 == second.canonical_config_sha256
    assert first.fingerprint == second.fingerprint


def test_verify_rejects_extra_snapshot_entries_and_non_string_expected_digest(tmp_path):
    _write_config(tmp_path)
    snapshot = write_snapshot(tmp_path, "d" * 32, collect_inputs(tmp_path, "examples/run.json"))
    invalid = dict(snapshot.snapshot_hashes)
    invalid["input.csv"] = 1
    with pytest.raises(JobInputError) as error:
        verify_snapshot(tmp_path, snapshot.job_id, invalid)
    assert error.value.code == "snapshot_invalid"
    directory = tmp_path / snapshot.input_root
    os.chmod(directory, 0o700)
    (directory / "unexpected.txt").write_text("not part of accepted inputs", encoding="utf-8")
    os.chmod(directory, 0o500)
    with pytest.raises(JobInputError) as error:
        verify_snapshot(tmp_path, snapshot.job_id, snapshot.snapshot_hashes)
    assert error.value.code == "snapshot_invalid"


def test_collect_rejects_control_path_and_unhashable_adapter_value(tmp_path):
    _write_config(tmp_path)
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run\n.json")
    assert error.value.code == "invalid_job_input"
    config = json.loads((tmp_path / "examples/run.json").read_text(encoding="utf-8"))
    config["data"]["adapter"] = {"unexpected": "object"}
    (tmp_path / "examples/run.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run.json")
    assert error.value.code == "invalid_job_input"


def test_collect_sanitizes_recursively_malformed_json(tmp_path):
    _write_config(tmp_path)
    (tmp_path / "examples/run.json").write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    with pytest.raises(JobInputError) as error:
        collect_inputs(tmp_path, "examples/run.json")
    assert (error.value.code, error.value.status, error.value.message) == (
        "invalid_job_input", 400, "Research job input is invalid.")
