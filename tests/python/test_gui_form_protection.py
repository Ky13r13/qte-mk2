"""Null identities are explicit, not truthiness shortcuts around protection."""
from dataclasses import replace

import pytest

from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.form_inputs import capture_form_inputs
from test_gui_job_protection import prepared

ORIGIN = {"kind": "inline_form", "path_base": "repository_root", "form_schema_version": 1}


@pytest.mark.parametrize("updates", [
    {"kind": "unknown"}, {"kind": []}, {"config_origin": None},
    {"config_origin": {**ORIGIN, "form_schema_version": True}},
    {"config_origin": {**ORIGIN, "extra": 1}}, {"original_config_path": "examples/fake.json"},
    {"original_data_path": "data/fake.csv"}, {"data_sha256": ""}, {"data_sha256": "a" * 64},
    {"data_bytes": b""}, {"adapter": "csv"}, {"config_sha256": None}, {"config_sha256": ""},
    {"config_sha256": "A" * 64}, {"config_sha256": []},
])
def test_inline_lab_malformed_origin_and_null_combinations_fail_closed(tmp_path, updates):
    captured = capture_form_inputs(tmp_path, "strategy_lab", ORIGIN, {})
    with pytest.raises(ArtifactError) as error:
        Catalog(tmp_path).authorize_job_inputs(replace(captured, **updates))
    assert error.value.code == "artifact_invalid"


@pytest.mark.parametrize("field,value", [
    ("config_sha256", None), ("data_sha256", None), ("config_sha256", ""),
    ("data_sha256", ""), ("original_config_path", None), ("original_data_path", None),
])
def test_legacy_inputs_keep_both_real_identities_required(tmp_path, field, value):
    captured = prepared(b"data")
    setattr(captured, field, value)
    with pytest.raises(ArtifactError) as error:
        Catalog(tmp_path).authorize_job_inputs(captured)
    assert error.value.code == "artifact_invalid"
