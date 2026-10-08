"""Offline execution must not launder known protected inputs into safe exports."""
from __future__ import annotations

import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from test_gui_catalog import _single, _unknown


def prepared(data: bytes, *, config=b"{}", data_path="data/copied.csv"):
    return SimpleNamespace(original_config_path="examples/run.json", original_data_path=data_path,
        config_sha256=hashlib.sha256(config).hexdigest(), data_sha256=hashlib.sha256(data).hexdigest())


def job_state(repository, output, state):
    path = repository / ".cache/qte-gui/catalog.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS gui_jobs (id TEXT PRIMARY KEY,state TEXT,output_path TEXT)")
        db.execute("INSERT OR REPLACE INTO gui_jobs VALUES (?,?,?)", (output.split('/')[3], state, output))


def test_unknown_inputs_have_no_untouched_claim_and_no_catalog_side_effect(tmp_path):
    catalog = Catalog(tmp_path)
    lineage = catalog.authorize_job_inputs(prepared(b"new data"))
    assert lineage["input_disclosure"] == "unknown"
    assert lineage["protected_input_identities"] == []
    assert not catalog.database.exists() and not catalog.disclosures.root.exists()


@pytest.mark.parametrize("protected_field", ["config", "data"])
def test_both_config_and_copied_data_hashes_require_prior_disclosure(tmp_path, protected_field):
    catalog = Catalog(tmp_path)
    value = b"known protected bytes"
    item = catalog.register(_unknown(tmp_path, "protected", value))
    source = prepared(value) if protected_field == "data" else prepared(b"data", config=value)
    with pytest.raises(ArtifactError) as error:
        catalog.authorize_job_inputs(source)
    assert error.value.code == "holdout_protected"
    catalog.reveal(item["id"], "reveal outside protocol")
    assert catalog.authorize_job_inputs(source)["input_disclosure"] == "revealed_outside_protocol"


def test_registered_ancestor_is_reverified_and_queued_inputs_can_become_protected(tmp_path):
    catalog = Catalog(tmp_path)
    old = catalog.register(_unknown(tmp_path, "changing", b"old"))
    source = prepared(b"new", data_path="build/changing/attachment.bin")
    (tmp_path / source.original_data_path).write_bytes(b"new")
    with pytest.raises(ArtifactError) as error: catalog.authorize_job_inputs(source)
    assert error.value.code == "holdout_protected"
    catalog.reveal(old["id"], "reveal outside protocol")
    assert catalog.authorize_job_inputs(source)["input_disclosure"] == "revealed_outside_protocol"

    queued = prepared(b"accepted before registration")
    assert catalog.authorize_job_inputs(queued)["protected_input_identities"] == []
    catalog.register(_unknown(tmp_path, "later-registration", b"accepted before registration"))
    with pytest.raises(ArtifactError): catalog.authorize_job_inputs(queued)


@pytest.mark.parametrize("protected_late", [False, True])
def test_derived_output_keeps_input_protection_without_implicit_reveal(tmp_path, protected_late):
    catalog = Catalog(tmp_path)
    source = prepared(b"input evidence")
    lineage = catalog.authorize_job_inputs(source)
    original = catalog.register(_unknown(tmp_path, "source", b"input evidence"))
    if not protected_late:
        catalog.reveal(original["id"], "reveal outside protocol")
        lineage = catalog.authorize_job_inputs(source)
    before_reveals = set(catalog.disclosures.root.glob("revealed_outside_protocol-*.json"))
    output = _single(tmp_path, "gui/jobs/" + "a" * 32 + "/artifacts",
                     b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n")
    job_state(tmp_path, output, "running")
    catalog.protect_job_output(output, lineage)
    with pytest.raises(ArtifactError) as blocked: catalog.register(output)
    assert blocked.value.code == "job_output_unavailable"
    job_state(tmp_path, output, "succeeded")
    item = Catalog(tmp_path).register(output)
    assert item["needs_disclosure"] is True
    assert all(not row["available"] for row in item["files"])
    assert set(catalog.disclosures.root.glob("revealed_outside_protocol-*.json")) == before_reveals
    assert not any(row["name"] == "lineage.json" for row in item["files"])


@pytest.mark.parametrize("path", ["build/original", "build/gui/jobs/not-uuid/artifacts",
                                "build/gui/jobs/" + "a" * 32 + "/artifacts/../private"])
def test_output_protection_is_limited_to_exact_owned_job_roots(tmp_path, path):
    with pytest.raises(ArtifactError) as error:
        Catalog(tmp_path).protect_job_output(path, {"input_identities": [], "protected_input_identities": []})
    assert error.value.code == "artifact_forbidden"


@pytest.mark.parametrize("damage", ["corrupt_record", "symlink_record", "symlink_directory"])
def test_invalid_protection_history_cannot_turn_known_inputs_into_unknown(tmp_path, damage):
    catalog = Catalog(tmp_path)
    source = prepared(b"protected bytes")
    catalog.register(_unknown(tmp_path, "original", b"protected bytes"))
    identity = f"file-sha256:{source.data_sha256}"
    journal = catalog.disclosures
    record = journal.root / f"protected_content-{journal.key(identity)}.json"
    if damage == "corrupt_record": record.write_text("{}")
    elif damage == "symlink_record":
        record.unlink(); record.symlink_to(tmp_path / "missing")
    else:
        original = journal.root.with_name("saved-disclosures")
        journal.root.rename(original)
        journal.root.symlink_to(original)
    with pytest.raises(ArtifactError) as error: catalog.authorize_job_inputs(source)
    assert error.value.code == "disclosure_unavailable"


def test_corrupt_output_protection_never_finalizes_successfully(tmp_path):
    catalog = Catalog(tmp_path)
    source = prepared(b"source bytes")
    original = catalog.register(_unknown(tmp_path, "source", b"source bytes"))
    catalog.reveal(original["id"], "reveal outside protocol")
    lineage = catalog.authorize_job_inputs(source)
    output = _single(tmp_path, "gui/jobs/" + "a" * 32 + "/artifacts",
                     b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n")
    identity = "file-sha256:" + hashlib.sha256((tmp_path / output / "report.json").read_bytes()).hexdigest()
    record = catalog.disclosures.root / f"protected_content-{catalog.disclosures.key(identity)}.json"
    record.write_text("{}")
    job_state(tmp_path, output, "running")
    with pytest.raises(ArtifactError) as error: catalog.protect_job_output(output, lineage)
    assert error.value.code == "disclosure_unavailable"
    with pytest.raises(ArtifactError) as error: catalog.register(output)
    assert error.value.code == "job_output_unavailable"


@pytest.mark.parametrize("state", [None, "queued", "running", "failed", "canceled", "interrupted"])
def test_complete_job_output_cannot_publish_before_verified_terminal_success(tmp_path, state):
    output = _single(tmp_path, "gui/jobs/" + "b" * 32 + "/artifacts",
                     b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n")
    if state is not None: job_state(tmp_path, output, state)
    with pytest.raises(ArtifactError) as error: Catalog(tmp_path).register(output)
    assert error.value.code == "job_output_unavailable"


def test_existing_job_registration_is_rechecked_on_every_read(tmp_path):
    catalog = Catalog(tmp_path)
    output = _single(tmp_path, "gui/jobs/" + "b" * 32 + "/artifacts",
                     b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n")
    job_state(tmp_path, output, "succeeded")
    item = catalog.register(output)
    job_state(tmp_path, output, "interrupted")
    with pytest.raises(ArtifactError) as error: catalog.get(item["id"])
    assert error.value.code == "job_output_unavailable"


@pytest.mark.parametrize("prefix", ["build//gui/jobs/", "build/./gui/jobs/", "./build/gui/jobs/"])
def test_noncanonical_paths_cannot_bypass_job_finalization(tmp_path, prefix):
    output = _single(tmp_path, "gui/jobs/" + "b" * 32 + "/artifacts",
                     b"timestamp_ns,equity,gross_exposure\n1,100.0,0.0\n")
    alternate = prefix + "b" * 32 + "/artifacts"
    catalog = Catalog(tmp_path)
    job_state(tmp_path, output, "running")
    with pytest.raises(ArtifactError) as error: catalog.register(alternate)
    assert error.value.code == "job_output_unavailable"
    job_state(tmp_path, output, "succeeded")
    item = catalog.register(alternate)
    # Simulate an old registry row saved before path normalization was added.
    with sqlite3.connect(catalog.database) as db:
        db.execute("UPDATE artifacts SET path=? WHERE id=?", (alternate, item["id"]))
    job_state(tmp_path, output, "running")
    with pytest.raises(ArtifactError) as error: catalog.get(item["id"])
    assert error.value.code == "job_output_unavailable"
