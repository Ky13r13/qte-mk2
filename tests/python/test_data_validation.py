import hashlib
import json
import os

import pytest

from qte.cli import main
from qte.data_validation import prepare_data, verify_dataset_files, verify_dataset_output, write_dataset_output


DATA = (b"symbol,timestamp,open,high,low,close,volume\n"
        b"SPY,2024-01-01T00:00:00Z,10,11,9,10.5,100\n"
        b"SPY,2024-01-01T00:01:00Z,10.5,12,10,11,101\n")
INTERVAL = 60_000_000_000


def config(path="bars.csv", expected=None):
    data = {"adapter": "csv", "path": path, "interval_ns": INTERVAL, "action_free": True}
    if expected is not None:
        data["expected_sha256"] = expected
    return {"data": data}


def test_prepare_data_is_pure_and_enforces_exact_captured_digest():
    digest = hashlib.sha256(DATA).hexdigest()
    prepared = prepare_data(config(expected=digest), DATA, build_identity="test-identity")
    assert prepared.data_sha256 == digest
    assert prepared.source_identity == "test-identity"
    assert prepared.loaded.dataset.bar_count == 2
    with pytest.raises(ValueError, match="expected_sha256"):
        prepare_data(config(expected="0" * 64), DATA)
    with pytest.raises(ValueError, match="only|unknown configuration"):
        prepare_data({**config(), "strategy": {}}, DATA)


def test_cli_artifact_is_exclusive_raw_and_canonically_replayable(tmp_path):
    (tmp_path / "bars.csv").write_bytes(DATA)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config(expected=hashlib.sha256(DATA).hexdigest())))
    output = tmp_path / "validation"
    assert write_dataset_output(config_path, output) == output
    assert {p.name for p in output.iterdir()} == {
        "dataset_validation.json", "configuration.json", "summary.json", "input.csv", "complete.json"
    }
    assert (output / "input.csv").read_bytes() == DATA
    verified = verify_dataset_output(output)
    assert verified["manifest"]["artifact_kind"] == "dataset_validation_v1"
    assert verified["manifest"]["adapter"] == "csv"
    assert verified["configuration"]["data"]["path"] == "input.csv"
    assert verified["summary"]["bar_count"] == 2
    assert verified["summary"]["availability"] == {
        "timestamp_basis": "UTC", "bar_interval": "fixed", "available_at": "bar_end_ns",
        "exchange_calendar_validation": False, "gap_repair": False, "bar_filling": False,
    }
    with pytest.raises(FileExistsError):
        write_dataset_output(config_path, output)


def test_validate_data_cli_dispatches(tmp_path):
    (tmp_path / "bars.csv").write_bytes(DATA)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config()))
    output = tmp_path / "validation"
    assert main(["validate-data", "--config", str(config_path), "--output", str(output)]) == 0
    assert verify_dataset_output(output)["summary"]["symbols"] == ["SPY"]


@pytest.mark.parametrize("mutation", ["extra", "checksum", "summary", "identity"])
def test_verifier_rejects_inventory_checksum_shape_and_replay_tampering(tmp_path, mutation):
    (tmp_path / "bars.csv").write_bytes(DATA)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config()))
    output = write_dataset_output(config_path, tmp_path / "validation")
    files = {path.name: path.read_bytes() for path in output.iterdir()}
    if mutation == "extra":
        files["other.txt"] = b"x"
    elif mutation == "checksum":
        files["input.csv"] += b"\n"
    else:
        name = "summary.json" if mutation == "summary" else "dataset_validation.json"
        value = json.loads(files[name])
        if mutation == "summary":
            value["bar_count"] = 99
        else:
            value["source_identity"] = "forged"
        files[name] = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        complete = json.loads(files["complete.json"])
        complete["sha256"][name] = hashlib.sha256(files[name]).hexdigest()
        files["complete.json"] = (json.dumps(complete, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(ValueError):
        verify_dataset_files(files)


def test_verifier_replays_historical_identity_but_rejects_permissive_json(tmp_path):
    (tmp_path / "bars.csv").write_bytes(DATA)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config()))
    output = write_dataset_output(config_path, tmp_path / "validation")
    files = {path.name: path.read_bytes() for path in output.iterdir()}
    manifest = json.loads(files["dataset_validation.json"])
    manifest["source_identity"] = "sha256:" + "1" * 64
    files["dataset_validation.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    complete = json.loads(files["complete.json"])
    complete["sha256"]["dataset_validation.json"] = hashlib.sha256(files["dataset_validation.json"]).hexdigest()
    files["complete.json"] = (json.dumps(complete, indent=2, sort_keys=True) + "\n").encode()
    assert verify_dataset_files(files)["manifest"]["source_identity"] == manifest["source_identity"]

    duplicate = dict(files)
    duplicate["dataset_validation.json"] = files["dataset_validation.json"].replace(
        b'{\n  "adapter"', b'{\n  "adapter": "csv",\n  "adapter"', 1)
    duplicate_complete = json.loads(duplicate["complete.json"])
    duplicate_complete["sha256"]["dataset_validation.json"] = hashlib.sha256(
        duplicate["dataset_validation.json"]
    ).hexdigest()
    duplicate["complete.json"] = (json.dumps(duplicate_complete, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(ValueError, match="duplicate"):
        verify_dataset_files(duplicate)

    boolean_schema = dict(files)
    completion = json.loads(boolean_schema["complete.json"])
    completion["schema_version"] = True
    boolean_schema["complete.json"] = (json.dumps(completion, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(ValueError, match="inventory"):
        verify_dataset_files(boolean_schema)


@pytest.mark.parametrize("invalid_data", [None, []])
def test_verifier_normalizes_malformed_configuration_data(tmp_path, invalid_data):
    (tmp_path / "bars.csv").write_bytes(DATA)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config()))
    output = write_dataset_output(config_path, tmp_path / "validation")
    files = {path.name: path.read_bytes() for path in output.iterdir()}
    configuration = json.loads(files["configuration.json"])
    configuration["data"] = invalid_data
    files["configuration.json"] = (json.dumps(configuration, indent=2, sort_keys=True) + "\n").encode()
    complete = json.loads(files["complete.json"])
    complete["sha256"]["configuration.json"] = hashlib.sha256(files["configuration.json"]).hexdigest()
    files["complete.json"] = (json.dumps(complete, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(ValueError, match="configuration"):
        verify_dataset_files(files)


def test_verifier_normalizes_excessively_nested_json(tmp_path):
    (tmp_path / "bars.csv").write_bytes(DATA)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config()))
    output = write_dataset_output(config_path, tmp_path / "validation")
    files = {path.name: path.read_bytes() for path in output.iterdir()}
    files["summary.json"] = (b'{"nested":' * 65) + b"null" + (b"}" * 65)
    complete = json.loads(files["complete.json"])
    complete["sha256"]["summary.json"] = hashlib.sha256(files["summary.json"]).hexdigest()
    files["complete.json"] = (json.dumps(complete, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(ValueError, match="nesting|invalid"):
        verify_dataset_files(files)


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_output_capture_rejects_nonregular_entries_without_blocking(tmp_path, kind):
    output = tmp_path / "validation"
    output.mkdir()
    target = output / "complete.json"
    if kind == "symlink":
        target.symlink_to(tmp_path / "missing")
    else:
        os.mkfifo(target)
    with pytest.raises(ValueError, match="invalid file"):
        verify_dataset_output(output)


def test_output_capture_rejects_regular_to_fifo_swap_without_blocking(tmp_path, monkeypatch):
    output = tmp_path / "validation"
    output.mkdir()
    target = output / "complete.json"
    target.write_bytes(b"{}")
    real_open = os.open

    def swap_then_open(path, flags):
        if os.fspath(path) == os.fspath(target):
            target.unlink()
            os.mkfifo(target)
        return real_open(path, flags)

    monkeypatch.setattr("qte.data_validation.os.open", swap_then_open)
    with pytest.raises(ValueError, match="changed during capture"):
        verify_dataset_output(output)
