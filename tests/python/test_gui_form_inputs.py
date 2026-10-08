"""Inline form capture stays bounded, path-safe, and pre-authorization only."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from qte.gui.form_inputs import capture_form_inputs
from qte.gui.job_inputs import CONFIG_LIMIT, DATA_LIMIT, JobInputError
from qte.gui.research_forms import config_origin


def origin():
    return config_origin()


def research(path: str = "data/bars.csv", *, adapter: str = "csv") -> dict:
    return {
        "data": {"adapter": adapter, "path": path},
        # Deliberately malformed financial sections remain out of scope here.
        "strategy": {"type": "not-yet-validated", "quantity": "not-a-number"},
        "engine": {"initial_cash": "also-not-validated"},
    }


def test_research_capture_canonicalizes_detached_config_and_reads_data_once(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    raw = b"timestamp,symbol,close\n1,ABC,10\n"
    (tmp_path / "data/bars.csv").write_bytes(raw)
    value = research("data/./bars.csv")
    import qte.gui.form_inputs as forms
    original, reads = forms._read_regular, []

    def read_once(repository, relative, limit):
        reads.append((repository, relative, limit))
        return original(repository, relative, limit)

    monkeypatch.setattr(forms, "_read_regular", read_once)
    captured = capture_form_inputs(tmp_path, "research_run", origin(), value)
    value["data"]["path"] = "data/changed.csv"
    (tmp_path / "data/bars.csv").write_bytes(b"mutated after capture")
    assert reads == [(tmp_path, "data/bars.csv", DATA_LIMIT)]
    assert captured.original_config_path is None
    assert captured.original_data_path == "data/bars.csv"
    assert captured.adapter == "csv" and captured.data_bytes == raw
    assert captured.data_sha256 == hashlib.sha256(raw).hexdigest()
    assert captured.config_bytes == captured.configuration_bytes
    assert captured.config_sha256 == hashlib.sha256(captured.configuration_bytes).hexdigest()
    assert json.loads(captured.configuration_bytes)["data"]["path"] == "data/./bars.csv"


def test_capture_does_not_parse_or_financially_validate_before_authorization(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/bars.csv").write_text("deliberately not bar data", encoding="utf-8")
    value = research()
    value["data"]["schema"] = {"symbol": ["not", "a", "schema"]}
    captured = capture_form_inputs(tmp_path, "research_run", origin(), value)
    assert captured.data_bytes == b"deliberately not bar data"


def test_capture_rejects_data_oversize_and_same_descriptor_mutation(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    data = tmp_path / "data/bars.csv"
    data.write_bytes(b"x" * (DATA_LIMIT + 1))
    with pytest.raises(JobInputError) as error:
        capture_form_inputs(tmp_path, "research_run", origin(), research())
    assert error.value.code == "input_forbidden"

    data.write_bytes(b"x" * 200_000)
    import qte.gui.job_inputs as inputs
    original_read, changed = inputs.os.read, False

    def read_then_mutate(fd, length):
        nonlocal changed
        result = original_read(fd, length)
        if result and not changed:
            changed = True
            data.write_bytes(data.read_bytes() + b" ")
        return result

    monkeypatch.setattr(inputs.os, "read", read_then_mutate)
    with pytest.raises(JobInputError) as error:
        capture_form_inputs(tmp_path, "research_run", origin(), research())
    assert error.value.code == "input_changed"


@pytest.mark.parametrize("path", ["../private.csv", ".hidden/bars.csv", "build/gui/private.csv", "data/key.pem"])
def test_research_capture_rejects_private_or_reserved_paths_before_read(tmp_path, path):
    with pytest.raises(JobInputError) as error:
        capture_form_inputs(tmp_path, "research_run", origin(), research(path))
    assert error.value.code == "input_forbidden"


def test_research_capture_rejects_symlink_fifo_and_adapter_suffix(tmp_path):
    (tmp_path / "data").mkdir()
    target = tmp_path / "target.csv"; target.write_text("x", encoding="utf-8")
    (tmp_path / "data/link.csv").symlink_to(target)
    with pytest.raises(JobInputError) as error:
        capture_form_inputs(tmp_path, "research_run", origin(), research("data/link.csv"))
    assert error.value.code == "input_forbidden"
    (tmp_path / "data/fifo.csv").unlink(missing_ok=True)
    os.mkfifo(tmp_path / "data/fifo.csv")
    with pytest.raises(JobInputError) as error:
        capture_form_inputs(tmp_path, "research_run", origin(), research("data/fifo.csv"))
    assert error.value.code == "input_forbidden"
    with pytest.raises(JobInputError) as error:
        capture_form_inputs(tmp_path, "research_run", origin(), research("data/link.json"))
    assert error.value.code == "invalid_job_input"


@pytest.mark.parametrize(("kind", "form_origin", "configuration"), [
    ("unknown", origin(), {}),
    ("research_run", {**origin(), "unexpected": True}, research()),
    ("research_run", {**origin(), "form_schema_version": True}, research()),
    ("research_run", origin(), []),
    ("research_run", origin(), {"data": {"adapter": "unknown", "path": "data/bars.csv"}}),
    ([], origin(), {}),
    ("research_run", origin(), {"data": {"adapter": [], "path": "data/bars.csv"}}),
    ("research_run", origin(), {"data": {"adapter": "csv", "path": "data/" + "x" * 510 + ".csv"}}),
])
def test_capture_rejects_invalid_envelope_before_data_read(tmp_path, kind, form_origin, configuration):
    with pytest.raises(JobInputError) as error:
        capture_form_inputs(tmp_path, kind, form_origin, configuration)
    assert error.value.code == "invalid_job_input"


def test_configuration_bounds_unicode_depth_and_nonfinite_are_rejected(tmp_path):
    too_large = {"data": {"adapter": "csv", "path": "data/bars.csv"}, "padding": "x" * CONFIG_LIMIT}
    for configuration in (
        too_large,
        {"text": "\ud800"},
        {"nested": json.loads("[" * 65 + "0" + "]" * 65)},
        {"number": float("nan")},
    ):
        with pytest.raises(JobInputError) as error:
            capture_form_inputs(tmp_path, "strategy_lab", origin(), configuration)
        assert error.value.code == "invalid_job_input"


def test_lab_capture_is_config_only_and_never_counterfeits_dataset_fields(tmp_path, monkeypatch):
    import qte.gui.form_inputs as forms
    monkeypatch.setattr(forms, "_read_regular", lambda *_: pytest.fail("lab capture read data"))
    value = {"data": {"adapter": "csv", "path": "data/not-read.csv"}, "seed": "not-validated-yet"}
    captured = capture_form_inputs(tmp_path, "strategy_lab", origin(), value)
    assert captured.kind == "strategy_lab"
    assert captured.original_config_path is None
    assert (captured.original_data_path, captured.adapter, captured.data_bytes, captured.data_sha256) == (
        None, None, None, None)
