"""Private validation-ID capture and holdout role authorization."""
from copy import deepcopy
from hashlib import sha256
import json
import shutil

import pytest

from qte.data_validation import write_dataset_output
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.comparison_inputs import authorize_comparison_inputs, prepare_comparison_inputs
from qte.gui.dataset_views import dataset_view
from qte.gui.job_inputs import JobInputError
from test_gui_comparison_artifacts import INTERVAL, _bars


def fixture(repository, workflow="historical_experiment"):
    (repository / "data").mkdir()
    catalog = Catalog(repository)
    roles = ("train", "validation", "test") if workflow == "historical_experiment" else ("train", "test")
    windows = []
    for day, role in enumerate(roles, 1):
        (repository / f"data/{role}.csv").write_bytes(_bars(day))
        config = repository / f"data/{role}.json"
        config.write_text(json.dumps({"data": {"adapter": "csv", "path": f"{role}.csv",
            "interval_ns": INTERVAL, "action_free": True}}))
        write_dataset_output(config, repository / f"build/validation-{role}")
        item = catalog.register(f"build/validation-{role}")
        windows.append({"name": role, "role": role, "regime": "fixture", "validation_id": item["id"],
            "evidence": {"kind": "synthetic", "source": "invented mechanics fixture",
                         "corporate_action_policy": "fabricated_action_free"}})
    return catalog, {"schema_version": 1, "workflow": workflow, "windows": windows,
        "candidates": [{"name": "sma", "type": "moving_average_regime", "symbol": "SPY",
                        "parameters": {"fast_period": "2", "slow_period": "3", "quantity": "1"}}],
        "experiment": {}}


def identity(source):
    return "file-sha256:" + source.data_sha256


@pytest.mark.parametrize("workflow", ["historical_experiment", "moving_average_comparison"])
def test_capture_is_frozen_ordered_and_runs_no_strategy(tmp_path, monkeypatch, workflow):
    import qte
    monkeypatch.setattr(qte.BacktestEngine, "run", lambda *a, **k: pytest.fail("capture ran strategy"))
    catalog, form = fixture(tmp_path, workflow)
    value = prepare_comparison_inputs(catalog, form)
    assert value.kind == "comparison_run"
    assert json.loads(value.submitted_form_bytes) == form
    effective = json.loads(value.effective_config_bytes)
    canonical = json.loads(value.configuration_bytes)
    for index, source in enumerate(value.sources):
        assert source.validation_id == form["windows"][index]["validation_id"]
        assert source.data_bytes == (tmp_path / source.source_path).read_bytes()
        assert sha256(source.data_bytes).hexdigest() == source.data_sha256
        assert effective["windows"][index]["data"]["path"] == f"window-{index:03d}.csv"
        assert canonical["windows"][index]["evidence"]["source_sha256"] == source.data_sha256
        assert canonical["windows"][index]["evidence"]["availability_policy"] == "bar_end_ns"
    assert canonical["candidates"][0]["parameters"]["quantity"] == 1
    form["windows"][0]["role"] = "test"
    assert json.loads(value.submitted_form_bytes)["windows"][0]["role"] == "train"
    assert not (tmp_path / "build/gui/jobs").exists()
    assert not list(catalog.disclosures.root.glob("revealed_outside_protocol-*"))


def test_reservation_preserves_shared_configuration_guard_without_cross_contamination(tmp_path):
    catalog, form = fixture(tmp_path)
    value = prepare_comparison_inputs(catalog, form)
    train, test = value.sources[0], value.sources[-1]
    assert (tmp_path / train.validation_path / "configuration.json").read_bytes() == (
        tmp_path / test.validation_path / "configuration.json").read_bytes()
    expected = authorize_comparison_inputs(catalog, value, reserve_test=True)
    assert expected["holdout_history"] == "unknown"
    assert not list(catalog.disclosures.root.glob("revealed_outside_protocol-*"))
    for catalog in (catalog, Catalog(tmp_path)):
        for _ in range(2):
            catalog.get(test.validation_id)
            item = catalog.get(train.validation_id)
            assert all(not row["available"] for row in item["files"])
            with pytest.raises(ArtifactError):
                dataset_view(catalog, train.validation_id)
            for row in item["files"]:
                with pytest.raises(ArtifactError):
                    catalog.read_file(train.validation_id, row["id"])
            assert authorize_comparison_inputs(catalog, value) == expected
        assert catalog.disclosures.protected(identity(test))
        assert not any(catalog.disclosures.protected(item) for item in train.identities)
    # Explicitly revealing shared caller options is not observing the test data.
    catalog.reveal(train.validation_id, "reveal outside protocol")
    assert all(row["available"] for row in catalog.get(train.validation_id)["files"])
    assert authorize_comparison_inputs(catalog, value) == expected
    assert not catalog.disclosures.revealed([identity(test)])
    catalog.reveal(test.validation_id, "reveal outside protocol")
    with pytest.raises(JobInputError):
        authorize_comparison_inputs(catalog, value)


@pytest.mark.parametrize("role", ["train", "validation"])
@pytest.mark.parametrize("name", ["input.csv", "summary.json"])
@pytest.mark.parametrize("reveal", [False, True])
def test_safe_roles_reject_protected_or_revealed_data_before_preparation(tmp_path, monkeypatch, role, name, reveal):
    catalog, form = fixture(tmp_path)
    digest = "file-sha256:" + sha256((tmp_path / f"build/validation-{role}/{name}").read_bytes()).hexdigest()
    catalog.disclosures.mark_protected([digest])
    if reveal:
        catalog.disclosures.reveal([digest])
    import qte.gui.comparison_inputs as module
    monkeypatch.setattr(module, "prepare_comparison", lambda *a: pytest.fail("parsed unauthorized input"))
    with pytest.raises(JobInputError) as error:
        prepare_comparison_inputs(catalog, form)
    assert (error.value.code, error.value.status) == ("comparison_input_unavailable", 409)
    assert str(error.value) == "Comparison inputs are unavailable or inconsistent."


@pytest.mark.parametrize("name", ["input.csv", "summary.json", "dataset_validation.json", "complete.json"])
def test_final_test_accepts_protection_but_never_known_reveal(tmp_path, name):
    catalog, form = fixture(tmp_path)
    value = prepare_comparison_inputs(catalog, form)
    test = value.sources[-1]
    digest = "file-sha256:" + sha256((tmp_path / test.validation_path / name).read_bytes()).hexdigest()
    catalog.disclosures.mark_protected([digest])
    assert prepare_comparison_inputs(catalog, form) == value
    assert all(not row["available"] for row in catalog.get(test.validation_id)["files"])
    assert catalog.disclosures.protected(identity(test))
    catalog.disclosures.reveal([digest])
    with pytest.raises(JobInputError):
        authorize_comparison_inputs(catalog, value)
    with pytest.raises(JobInputError):
        prepare_comparison_inputs(catalog, form)


@pytest.mark.parametrize("kind", ["protected_content", "revealed_outside_protocol"])
@pytest.mark.parametrize("damage", ["corrupt", "symlink", "directory_symlink"])
def test_damaged_disclosure_history_fails_closed(tmp_path, kind, damage):
    catalog, form = fixture(tmp_path)
    value = prepare_comparison_inputs(catalog, form)
    root = catalog.disclosures.root
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{kind}-{catalog.disclosures.key(identity(value.sources[-1]))}.json"
    if damage == "directory_symlink":
        root.rename(root.with_name("old-disclosures"))
        root.symlink_to(root.with_name("old-disclosures"), target_is_directory=True)
    elif damage == "symlink":
        path.symlink_to(tmp_path / "missing-record")
    else:
        path.write_text("SECRET invalid history")
    with pytest.raises(JobInputError) as error:
        authorize_comparison_inputs(catalog, value)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("mutation", ["duplicate", "integer", "noncanonical", "path", "role", "command"])
def test_invalid_forms_reject_without_job_directory_or_diagnostic_leaks(tmp_path, mutation):
    catalog, form = fixture(tmp_path)
    if mutation == "duplicate": form["windows"][1]["validation_id"] = form["windows"][0]["validation_id"]
    elif mutation == "integer": form["candidates"][0]["parameters"]["quantity"] = 1
    elif mutation == "noncanonical": form["candidates"][0]["parameters"]["quantity"] = "01"
    elif mutation == "path": form["windows"][0]["path"] = "SECRET.csv"
    elif mutation == "role": form["windows"][0]["role"] = "test"
    else: form["command"] = "SECRET"
    with pytest.raises(JobInputError) as error:
        prepare_comparison_inputs(catalog, form)
    assert "SECRET" not in str(error.value)
    assert not (tmp_path / "build/gui/jobs").exists()


def test_copied_validation_with_new_id_is_still_a_duplicate_source(tmp_path):
    catalog, form = fixture(tmp_path)
    shutil.copytree(tmp_path / "build/validation-train", tmp_path / "build/copied")
    form["windows"][1]["validation_id"] = catalog.register("build/copied")["id"]
    with pytest.raises(JobInputError):
        prepare_comparison_inputs(catalog, form)


def test_aggregate_limit_precedes_data_preparation(tmp_path, monkeypatch):
    catalog, form = fixture(tmp_path)
    import qte.gui.comparison_inputs as module
    monkeypatch.setattr(module, "TOTAL_DATA_LIMIT", 1)
    monkeypatch.setattr(module, "prepare_comparison", lambda *a: pytest.fail("parsed oversized input"))
    with pytest.raises(JobInputError):
        prepare_comparison_inputs(catalog, form)


def test_changed_validation_during_capture_fails_reinspection(tmp_path, monkeypatch):
    catalog, form = fixture(tmp_path)
    import qte.gui.comparison_inputs as module
    original = module._read_regular
    def changed(repository, path, limit):
        raw = original(repository, path, limit)
        if path == "build/validation-test/input.csv":
            (repository / "build/validation-test/summary.json").write_text("SECRET")
        return raw
    monkeypatch.setattr(module, "_read_regular", changed)
    with pytest.raises(JobInputError) as error:
        prepare_comparison_inputs(catalog, form)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("change", ["none", "changed_source", "reexport", "uppercase"])
def test_real_gui_validation_source_correspondence(tmp_path, change):
    from qte.gui.research_forms import config_origin
    from test_gui_job_api import finished, visit
    catalog, form = fixture(tmp_path)
    filename = "TRAIN.CSV" if change == "uppercase" else "train.csv"
    if change == "uppercase":
        (tmp_path / "data/train.csv").rename(tmp_path / "data/TRAIN.CSV")
    payload = {"schema_version": 1, "kind": "dataset_validate", "config_origin": config_origin(),
        "configuration": {"data": {"adapter": "csv", "path": f"data/{filename}",
                                    "interval_ns": str(INTERVAL), "action_free": True}}}
    async def check(client, headers):
        preview = await client.post("/api/v1/configs/validate", headers=headers, json=payload)
        assert preview.status_code == 200, preview.text
        submitted = await client.post("/api/v1/jobs", headers=headers, json={**payload,
            "preview_fingerprint": preview.json()["preview_fingerprint"], "idempotency_key": "dataset_" + change * 5,
            "timeout_seconds": 30})
        assert submitted.status_code == 200, submitted.text
        job = await finished(client, headers, submitted.json()["job"]["id"])
        assert job["state"] == "succeeded", job
        form["windows"][0]["validation_id"] = catalog.register(job["output_path"])["id"]
        value = prepare_comparison_inputs(catalog, form)
        assert value.sources[0].source_path == f"data/{filename}"
        if change in {"none", "uppercase"}:
            return
        if change == "changed_source":
            (tmp_path / f"data/{filename}").write_text("SECRET")
        else:
            alternate = deepcopy(payload["configuration"])
            alternate["data"].update(path=filename, interval_ns=INTERVAL, currency="EUR")
            config = tmp_path / "data/reexport.json"
            config.write_text(json.dumps(alternate))
            replacement = write_dataset_output(config, tmp_path / "build/reexport")
            for path in replacement.iterdir():
                shutil.copyfile(path, tmp_path / job["output_path"] / path.name)
        with pytest.raises(JobInputError) as error:
            prepare_comparison_inputs(catalog, form)
        assert "SECRET" not in str(error.value)
    visit(tmp_path, check)
