"""V3 snapshots preserve exact multi-input bytes without altering v1/v2."""
from dataclasses import replace
from hashlib import sha256
import json
import os

import pytest

from qte.comparison_export import run_comparison
from qte.gui.comparison_inputs import prepare_comparison_inputs
from qte.gui.job_inputs import JobInputError, verify_snapshot, write_snapshot
from test_gui_comparison_inputs import fixture


@pytest.mark.parametrize("workflow", ["historical_experiment", "moving_average_comparison"])
def test_snapshot_roundtrip_uses_captured_bytes_and_can_feed_existing_cli(tmp_path, monkeypatch, workflow):
    import qte
    original = qte.BacktestEngine.run
    monkeypatch.setattr(qte.BacktestEngine, "run", lambda *a, **k: pytest.fail("snapshot ran strategy"))
    catalog, form = fixture(tmp_path, workflow)
    value = prepare_comparison_inputs(catalog, form)
    for source in value.sources:
        (tmp_path / source.source_path).unlink()
    snapshot = write_snapshot(tmp_path, "a" * 32, value)
    assert verify_snapshot(tmp_path, snapshot.job_id, snapshot.snapshot_hashes) == snapshot
    assert snapshot.kind == "comparison_run"
    assert snapshot.source_identity == value.source_identity
    assert snapshot.fingerprint == value.fingerprint
    assert len(snapshot.snapshot_hashes) == len(value.sources) + 4
    for path, source in zip(snapshot.data_paths, value.sources, strict=True):
        assert (tmp_path / path).read_bytes() == source.data_bytes
    root = tmp_path / snapshot.input_root
    assert not root.stat().st_mode & 0o222
    assert all(not path.stat().st_mode & 0o222 for path in root.iterdir())
    assert not (root.parent / "artifacts").exists()
    with pytest.raises(JobInputError) as error:
        write_snapshot(tmp_path, snapshot.job_id, value)
    assert error.value.code == "snapshot_exists"
    monkeypatch.setattr(qte.BacktestEngine, "run", original)
    output = run_comparison(tmp_path / snapshot.effective_config_path, tmp_path / "build/cli-output")
    assert (output / "complete.json").is_file()
    assert json.loads((output / "configuration.json").read_text())["workflow"] == workflow


@pytest.mark.parametrize("field,changed", [("kind", "research_run"), ("fingerprint", "0" * 64),
    ("configuration_bytes", b"{}"), ("effective_config_bytes", b"{}"), ("source_identity", "sha256:" + "0" * 64)])
def test_forged_prepared_value_fails_before_mkdir(tmp_path, field, changed):
    catalog, form = fixture(tmp_path)
    prepared = prepare_comparison_inputs(catalog, form)
    with pytest.raises(JobInputError) as error:
        write_snapshot(tmp_path, "b" * 32, replace(prepared, **{field: changed}))
    assert error.value.code == "invalid_job_input"
    assert not (tmp_path / "build/gui/jobs" / ("b" * 32)).exists()


@pytest.mark.parametrize("mutation", ["schema", "role", "adapter", "path", "index", "identity",
                                     "extra", "symlink", "inventory_name", "unpinned"])
def test_rehashed_tampering_and_inventory_rejected(tmp_path, mutation):
    catalog, form = fixture(tmp_path)
    prepared = prepare_comparison_inputs(catalog, form)
    snapshot = write_snapshot(tmp_path, "c" * 32, prepared)
    root = tmp_path / snapshot.input_root
    os.chmod(root, 0o700)
    for path in root.iterdir():
        os.chmod(path, 0o600)
    manifest_path = root / "input-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    expected = dict(snapshot.snapshot_hashes)
    if mutation == "inventory_name":
        expected["../outside"] = "0" * 64
    elif mutation == "extra":
        (root / "extra.json").write_text("{}")
    elif mutation == "symlink":
        (root / "window-000.csv").unlink()
        (root / "window-000.csv").symlink_to(tmp_path / prepared.sources[0].source_path)
    else:
        if mutation == "schema": manifest["schema_version"] = 2
        elif mutation == "role":
            path = root / "effective-config.json"
            config = json.loads(path.read_text())
            config["windows"][0]["role"] = "test"
            path.write_text(json.dumps(config))
        elif mutation == "adapter": manifest["sources"][0]["adapter"] = "alpaca"
        elif mutation == "path": manifest["sources"][0]["source_path"] = "../SECRET"
        elif mutation == "index": manifest["sources"][0]["input_name"] = "window-001.csv"
        elif mutation == "identity": manifest["sources"][0]["validation_identity"] = "invalid"
        else: manifest["source_identity"] = "sha256:" + "0" * 64
        # Even independently repinned hashes cannot excuse inconsistent semantics.
        hashes = {name: sha256((root / name).read_bytes()).hexdigest()
                  for name in expected if name != "input-manifest.json"}
        manifest["hashes"] = hashes
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        if mutation != "unpinned":
            expected = {**hashes, "input-manifest.json": sha256(manifest_path.read_bytes()).hexdigest()}
    with pytest.raises(JobInputError) as error:
        verify_snapshot(tmp_path, snapshot.job_id, expected)
    assert (error.value.code, error.value.status) == ("snapshot_invalid", 409)
    assert "SECRET" not in str(error.value)


def test_partial_write_has_no_completion_and_cannot_be_reused(tmp_path, monkeypatch):
    catalog, form = fixture(tmp_path)
    prepared = prepare_comparison_inputs(catalog, form)
    import qte.gui.comparison_snapshots as module
    original = module._write_exclusive
    def fail(directory, name, raw):
        if name == "window-001.csv":
            raise OSError("disk full")
        original(directory, name, raw)
    monkeypatch.setattr(module, "_write_exclusive", fail)
    job_id = "d" * 32
    with pytest.raises(JobInputError):
        write_snapshot(tmp_path, job_id, prepared)
    root = tmp_path / "build/gui/jobs" / job_id / "inputs"
    assert (root / "window-000.csv").is_file()
    assert not (root / "input-manifest.json").exists()
    expected = {path.name: sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    with pytest.raises(JobInputError):
        verify_snapshot(tmp_path, job_id, expected)
    monkeypatch.setattr(module, "_write_exclusive", original)
    with pytest.raises(JobInputError) as error:
        write_snapshot(tmp_path, job_id, prepared)
    assert error.value.code == "snapshot_exists"
