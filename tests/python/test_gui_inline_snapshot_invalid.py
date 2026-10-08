"""Even rehashed inline snapshots must retain their independently derived meaning."""
from dataclasses import replace
from hashlib import sha256
import json
import os

import pytest

from qte.gui.job_inputs import JobInputError, verify_snapshot, write_snapshot
from test_gui_inline_inputs import prepared, repository


@pytest.mark.parametrize("changes", [
    {"kind": []}, {"kind": "unexpected"}, {"adapter": []}, {"adapter": "alpaca"},
    {"original_config_path": "examples/fake.json"}, {"original_data_path": "data/other.csv"},
    {"original_data_path": "/data/bars.csv"}, {"config_origin": None},
    {"configuration_bytes": None}, {"submitted_form_bytes": None}, {"effective_config_bytes": None},
    {"configuration_bytes": b"{}"}, {"submitted_form_bytes": b"{}"}, {"effective_config_bytes": b"{}"},
    {"config_sha256": "0" * 64}, {"canonical_config_sha256": "0" * 64},
    {"preview_fingerprint": "0" * 64}, {"data_bytes": None}, {"data_bytes": b"changed"},
    {"data_sha256": None},
])
def test_forged_prepared_metadata_fails_before_any_job_directory(repository, changes):
    accepted, _, _ = prepared(repository)
    with pytest.raises(JobInputError) as error:
        write_snapshot(repository, "f" * 32, replace(accepted, **changes))
    assert error.value.code == "invalid_job_input"
    assert not (repository / "build/gui/jobs").exists()


def rehash(repository, snapshot, filename, mutation):
    folder = repository / snapshot.input_root
    manifest_path = folder / "input-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    value = manifest if filename == "input-manifest.json" else json.loads((folder / filename).read_bytes())
    mutation(value)
    expected = dict(snapshot.snapshot_hashes)
    if filename != "input-manifest.json":
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        path = folder / filename
        os.chmod(path, 0o600)
        path.write_bytes(raw)
        os.chmod(path, 0o400)
        manifest["hashes"][filename] = expected[filename] = sha256(raw).hexdigest()
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    os.chmod(manifest_path, 0o600)
    manifest_path.write_bytes(raw)
    os.chmod(manifest_path, 0o400)
    expected["input-manifest.json"] = sha256(raw).hexdigest()
    return expected


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1), ("schema_version", 3),
    ("job_id", "0" * 32), ("kind", []), ("kind", "strategy_lab"),
    ("adapter", []), ("adapter", "alpaca"), ("original_data_path", "data/other.csv"),
    ("config_origin", None), ("submitted_configuration_sha256", "0" * 64),
    ("canonical_config_sha256", "0" * 64), ("preview_fingerprint", "0" * 64),
])
def test_rehashed_manifest_cannot_redefine_captured_meaning(repository, field, value):
    accepted, _, _ = prepared(repository)
    saved = write_snapshot(repository, "f" * 32, accepted)
    expected = rehash(repository, saved, "input-manifest.json", lambda item: item.update({field: value}))
    with pytest.raises(JobInputError) as error:
        verify_snapshot(repository, saved.job_id, expected)
    assert (error.value.code, error.value.status) == ("snapshot_invalid", 409)


@pytest.mark.parametrize("filename,mutation", [
    ("submitted-form.json", lambda item: item.update(schema_version=True)),
    ("submitted-form.json", lambda item: item.update(kind="strategy_lab")),
    ("submitted-form.json", lambda item: item["config_origin"].update(form_schema_version=True)),
    ("submitted-form.json", lambda item: item["configuration"]["strategy"].update(quantity=10)),
    ("effective-config.json", lambda item: item["data"].update(path="data/bars.csv")),
    ("effective-config.json", lambda item: item["engine"].update(initial_cash=99999.0)),
])
def test_rehashed_envelope_and_effective_config_still_require_exact_derivation(repository, filename, mutation):
    accepted, _, _ = prepared(repository)
    saved = write_snapshot(repository, "f" * 32, accepted)
    expected = rehash(repository, saved, filename, mutation)
    with pytest.raises(JobInputError) as error:
        verify_snapshot(repository, saved.job_id, expected)
    assert (error.value.code, error.value.status) == ("snapshot_invalid", 409)


@pytest.mark.parametrize("limit", ["CONFIG_LIMIT", "DATA_LIMIT"])
def test_writer_and_verifier_enforce_matching_capture_size_limits(repository, monkeypatch, limit):
    import qte.gui.inline_inputs as inline
    accepted, _, _ = prepared(repository)
    saved = write_snapshot(repository, "f" * 32, accepted)
    size = len(accepted.configuration_bytes if limit == "CONFIG_LIMIT" else accepted.data_bytes)
    monkeypatch.setattr(inline, limit, size - 1)
    with pytest.raises(JobInputError) as error:
        write_snapshot(repository, "e" * 32, accepted)
    assert error.value.code == "invalid_job_input"
    assert not (repository / "build/gui/jobs" / ("e" * 32)).exists()
    with pytest.raises(JobInputError) as error:
        verify_snapshot(repository, saved.job_id, saved.snapshot_hashes)
    assert (error.value.code, error.value.status) == ("snapshot_invalid", 409)


def test_lab_cannot_gain_a_counterfeit_empty_dataset_by_rehashing(repository):
    accepted, _, _ = prepared(repository, "strategy_lab")
    saved = write_snapshot(repository, "f" * 32, accepted)
    folder = repository / saved.input_root
    os.chmod(folder, 0o700)
    (folder / "input.csv").write_bytes(b"")
    os.chmod(folder, 0o500)
    digest = sha256(b"").hexdigest()
    expected = rehash(repository, saved, "input-manifest.json", lambda item: item["hashes"].update({"input.csv": digest}))
    expected["input.csv"] = digest
    with pytest.raises(JobInputError) as error:
        verify_snapshot(repository, saved.job_id, expected)
    assert (error.value.code, error.value.status) == ("snapshot_invalid", 409)
