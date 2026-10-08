"""G5b.2b snapshot compatibility and inline identity regressions."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil

import pytest

import qte
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.form_inputs import capture_form_inputs
from qte.gui.inline_inputs import prepare_inline_inputs
from qte.gui.job_inputs import JobInputError, collect_inputs, verify_snapshot, write_snapshot
from qte.gui.jobs import _fingerprint
from qte.gui.research_forms import config_origin, prepare_form


ROOT = Path(__file__).resolve().parents[2]
JOB_ID = "0123456789abcdef0123456789abcdef"

LEGACY_EFFECTIVE = (
    b'{"data":{"action_free":true,"adapter":"csv","interval_ns":3600000000000,'
    b'"path":"input.csv"},"engine":{"history_capacity":256,"initial_cash":100000,'
    b'"random_seed":0},"execution":{"commission_bps":1,"slippage_bps":2,'
    b'"spread_bps":2},"sampling":{"interval_ns":3600000000000,'
    b'"max_staleness_ns":0,"periods_per_year":8766},"strategy":{"fast_period":2,'
    b'"quantity":10,"slow_period":3,"symbol":"SPY","type":"moving_average_regime"}}'
)
LEGACY_MANIFEST = (
    b'{"adapter":"csv","canonical_config_sha256":'
    b'"2f0d0705e8964dc7a4521c679891b139ed07e527fcc361abe3acf526c681b549",'
    b'"fingerprint":"e93cb7d9c98c5d505fd2bc316fd18142c12bc0541e63a0431b3e4cd8e0b18f74",'
    b'"hashes":{"effective-config.json":'
    b'"8fb71fe535c0f5a2bec31b2c05f1727d7e52eddaa146743ed79988c97575a328",'
    b'"input.csv":"939e601058227310898b4405c71c3d6571b288df8c0244c41d05005ef9dba7b5",'
    b'"original-config.json":"6d8dc13ba72c54a9e86fe0e2c668053c0e580cfd80755716f8aa4e81592eea72"},'
    b'"job_id":"0123456789abcdef0123456789abcdef",'
    b'"original_config_path":"examples/research-run.json",'
    b'"original_data_path":"examples/research-bars.csv","schema_version":1}\n'
)


def _legacy_repository(tmp_path: Path) -> Path:
    examples = tmp_path / "examples"
    examples.mkdir()
    for name in ("research-run.json", "research-bars.csv"):
        (examples / name).write_bytes((ROOT / "examples" / name).read_bytes())
    return tmp_path


def _research_wire() -> dict:
    return {
        "data": {"adapter": "csv", "path": "data/bars.csv",
                 "interval_ns": "3600000000000", "action_free": True},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY",
                     "fast_period": "2", "slow_period": "3", "quantity": "10"},
        "engine": {"initial_cash": 100_000, "history_capacity": "256",
                   "random_seed": "18446744073709551615"},
        "sampling": {"interval_ns": "3600000000000", "max_staleness_ns": "0",
                     "periods_per_year": 8766},
    }


def _lab_wire() -> dict:
    return {"schema_version": 1, "seed": "4611686018427387903",
            "bars_per_window": "128", "timeframes": ["hourly"]}


def _inline_repository(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copyfile(ROOT / "examples/research-bars.csv", data / "bars.csv")
    return tmp_path


def _inline_prepared(repository: Path, kind: str):
    wire = _research_wire() if kind == "research_run" else _lab_wire()
    captured = capture_form_inputs(repository, kind, config_origin(), wire)
    preview = prepare_form(kind, json.loads(captured.configuration_bytes), captured.data_bytes)
    return wire, captured, preview, prepare_inline_inputs(captured, preview)


def test_legacy_snapshot_bytes_inventory_manifest_and_fingerprint_are_frozen(tmp_path):
    repository = _legacy_repository(tmp_path)
    prepared = collect_inputs(repository, "examples/research-run.json")
    assert prepared.config_sha256 == "6d8dc13ba72c54a9e86fe0e2c668053c0e580cfd80755716f8aa4e81592eea72"
    assert prepared.data_sha256 == "939e601058227310898b4405c71c3d6571b288df8c0244c41d05005ef9dba7b5"
    assert prepared.canonical_config_sha256 == "2f0d0705e8964dc7a4521c679891b139ed07e527fcc361abe3acf526c681b549"
    assert prepared.fingerprint == "e93cb7d9c98c5d505fd2bc316fd18142c12bc0541e63a0431b3e4cd8e0b18f74"
    assert prepared.effective_config_bytes == LEGACY_EFFECTIVE
    assert _fingerprint("research_run", prepared, 30) == (
        "edadf96e4f789444ec5be6a68eeca5a9cbd753b19da44cc0833a782c87020466"
    )

    snapshot = write_snapshot(repository, JOB_ID, prepared)
    folder = repository / snapshot.input_root
    assert {path.name for path in folder.iterdir()} == {
        "original-config.json", "effective-config.json", "input.csv", "input-manifest.json",
    }
    assert (folder / "original-config.json").read_bytes() == prepared.config_bytes
    assert (folder / "effective-config.json").read_bytes() == LEGACY_EFFECTIVE
    assert (folder / "input.csv").read_bytes() == prepared.data_bytes
    assert (folder / "input-manifest.json").read_bytes() == LEGACY_MANIFEST
    assert snapshot.snapshot_hashes == {
        "original-config.json": "6d8dc13ba72c54a9e86fe0e2c668053c0e580cfd80755716f8aa4e81592eea72",
        "effective-config.json": "8fb71fe535c0f5a2bec31b2c05f1727d7e52eddaa146743ed79988c97575a328",
        "input.csv": "939e601058227310898b4405c71c3d6571b288df8c0244c41d05005ef9dba7b5",
        "input-manifest.json": "673698af7bb885175b894a1534f242a42a04a02f0e6ac8e06cc5b56521a99f82",
    }

    verified = verify_snapshot(repository, JOB_ID, snapshot.snapshot_hashes)
    assert verified == snapshot
    assert json.loads((folder / "input-manifest.json").read_bytes())["schema_version"] == 1


def test_legacy_alpaca_dispatch_remains_v1_and_uses_input_json(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "bars.json").write_bytes(
        (ROOT / "tests/fixtures/alpaca/stock_bars_complete.json").read_bytes()
    )
    configuration = {
        "data": {"adapter": "alpaca", "path": "bars.json", "interval_ns": 3_600_000_000_000},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY",
                     "fast_period": 2, "slow_period": 3, "quantity": 10},
        "engine": {"initial_cash": 100_000},
    }
    (examples / "run.json").write_text(json.dumps(configuration), encoding="utf-8")
    prepared = collect_inputs(tmp_path, "examples/run.json")
    snapshot = write_snapshot(tmp_path, "1" * 32, prepared)
    folder = tmp_path / snapshot.input_root
    assert set(snapshot.snapshot_hashes) == {
        "original-config.json", "effective-config.json", "input.json", "input-manifest.json",
    }
    assert json.loads((folder / "effective-config.json").read_bytes())["data"]["path"] == "input.json"
    assert verify_snapshot(tmp_path, snapshot.job_id, snapshot.snapshot_hashes) == snapshot


def test_legacy_v1_inventory_cannot_be_reclassified_by_inline_filename(tmp_path):
    repository = _legacy_repository(tmp_path)
    snapshot = write_snapshot(repository, "2" * 32,
                              collect_inputs(repository, "examples/research-run.json"))
    folder = repository / snapshot.input_root
    os.chmod(folder, 0o700)
    (folder / "submitted-form.json").write_text("{}", encoding="utf-8")
    os.chmod(folder, 0o500)
    expected = dict(snapshot.snapshot_hashes)
    expected["submitted-form.json"] = "0" * 64
    with pytest.raises(JobInputError) as caught:
        verify_snapshot(repository, snapshot.job_id, expected)
    assert caught.value.code == "snapshot_invalid"


def test_inline_snapshot_owns_exact_wire_integers_and_captured_data_after_input_mutation(
        tmp_path, monkeypatch):
    repository = _inline_repository(tmp_path)
    monkeypatch.setattr(qte, "BacktestEngine", lambda *_args, **_kwargs: pytest.fail("snapshot ran engine"))
    wire, captured, preview, prepared = _inline_prepared(repository, "research_run")
    captured_data = captured.data_bytes
    assert captured_data is not None

    # Neither later caller mutation nor later source mutation may alter the
    # already-reviewed wire object or the exact captured bytes.
    wire["engine"]["random_seed"] = "7"
    wire["sampling"]["max_staleness_ns"] = "99"
    wire["data"]["path"] = "data/replacement.csv"
    (repository / "data/bars.csv").write_bytes(b"changed after stable capture")
    with monkeypatch.context() as context:
        context.setattr(Path, "read_bytes", lambda *_args, **_kwargs: pytest.fail("snapshot reread source path"))
        snapshot = write_snapshot(repository, "3" * 32, prepared)

    root = repository / snapshot.input_root
    submitted = json.loads((root / "submitted-form.json").read_bytes())
    assert submitted["configuration"]["engine"]["random_seed"] == "18446744073709551615"
    assert submitted["configuration"]["sampling"]["max_staleness_ns"] == "0"
    assert submitted["configuration"]["data"]["interval_ns"] == "3600000000000"
    assert submitted["configuration"]["data"]["path"] == "data/bars.csv"
    effective = json.loads((root / "effective-config.json").read_bytes())
    assert effective["engine"]["random_seed"] == 2**64 - 1
    assert effective["data"]["interval_ns"] == 3_600_000_000_000
    assert effective["data"]["path"] == "input.csv"
    assert (root / "input.csv").read_bytes() == captured_data
    assert prepared.config_sha256 == captured.config_sha256
    assert prepared.canonical_config_sha256 == preview["canonical_config_sha256"]
    assert verify_snapshot(repository, snapshot.job_id, snapshot.snapshot_hashes) == snapshot


def test_crossed_data_preview_cannot_reuse_the_same_canonical_configuration(tmp_path):
    repository = _inline_repository(tmp_path)
    _, captured, preview, prepared = _inline_prepared(repository, "research_run")
    assert captured.data_bytes is not None
    changed_data = captured.data_bytes.replace(b",1000\n", b",1001\n", 1)
    assert changed_data != captured.data_bytes
    changed_preview = prepare_form(
        "research_run", json.loads(captured.configuration_bytes), changed_data,
    )
    assert changed_preview["canonical_config_sha256"] == preview["canonical_config_sha256"]
    assert changed_preview["preview_fingerprint"] != prepared.preview_fingerprint
    with pytest.raises(JobInputError) as caught:
        prepare_inline_inputs(captured, changed_preview)
    assert caught.value.code == "invalid_job_input"


def test_lab_snapshot_has_null_data_identity_and_rejects_counterfeit_data(tmp_path):
    repository = _inline_repository(tmp_path)
    wire, captured, preview, prepared = _inline_prepared(repository, "strategy_lab")
    forged_capture = replace(
        captured,
        original_data_path="data/bars.csv",
        adapter="csv",
        data_bytes=b"",
        data_sha256=sha256(b"").hexdigest(),
    )
    with pytest.raises(JobInputError):
        prepare_inline_inputs(forged_capture, preview)
    forged_prepared = replace(
        prepared,
        original_data_path="data/bars.csv",
        adapter="csv",
        data_bytes=b"",
        data_sha256=sha256(b"").hexdigest(),
    )
    with pytest.raises(JobInputError):
        write_snapshot(repository, "4" * 32, forged_prepared)
    assert not (repository / "build/gui/jobs" / ("4" * 32)).exists()

    wire["seed"] = "1"
    snapshot = write_snapshot(repository, "5" * 32, prepared)
    assert (snapshot.original_config_path, snapshot.original_data_path, snapshot.adapter,
            snapshot.data_path, snapshot.data_sha256) == (None, None, None, None, None)
    assert snapshot.hashes["data_sha256"] is None
    assert set(snapshot.snapshot_hashes) == {
        "submitted-form.json", "effective-config.json", "input-manifest.json",
    }
    submitted = json.loads((repository / snapshot.input_root / "submitted-form.json").read_bytes())
    effective = json.loads((repository / snapshot.effective_config_path).read_bytes())
    assert submitted["configuration"]["seed"] == "4611686018427387903"
    assert effective["seed"] == 2**62 - 1
    assert verify_snapshot(repository, snapshot.job_id, snapshot.snapshot_hashes) == snapshot


@pytest.mark.parametrize("kind", ("research_run", "strategy_lab"))
def test_catalog_authorizes_captured_and_prepared_inline_shapes_with_identical_lineage(
        tmp_path, kind):
    repository = _inline_repository(tmp_path)
    _, captured, _, prepared = _inline_prepared(repository, kind)
    catalog = Catalog(repository)
    captured_lineage = catalog.authorize_job_inputs(captured)
    prepared_lineage = catalog.authorize_job_inputs(prepared)
    assert prepared_lineage == captured_lineage
    assert prepared_lineage["input_identities"] == (
        [f"file-sha256:{captured.config_sha256}"] if kind == "strategy_lab" else sorted([
            f"file-sha256:{captured.config_sha256}", f"file-sha256:{captured.data_sha256}",
        ])
    )
    assert prepared_lineage["input_disclosure"] == (
        "not_required" if kind == "strategy_lab" else "unknown"
    )

    identities = prepared_lineage["input_identities"]
    catalog.disclosures.mark_protected(identities)
    for value in (captured, prepared):
        with pytest.raises(ArtifactError) as caught:
            catalog.authorize_job_inputs(value)
        assert caught.value.code == "holdout_protected"
    catalog.disclosures.reveal(identities)
    revealed_captured = catalog.authorize_job_inputs(captured)
    revealed_prepared = catalog.authorize_job_inputs(prepared)
    assert revealed_prepared == revealed_captured
    assert revealed_prepared["protected_input_identities"] == identities
    assert revealed_prepared["input_disclosure"] == "revealed_outside_protocol"
