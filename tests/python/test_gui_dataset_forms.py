from hashlib import sha256

import pytest

from qte.gui.research_forms import FormError, compile_configuration, form_identity, form_options, prepare_form


DATA = (b"symbol,timestamp,open,high,low,close,volume\n"
        b"SPY,2024-01-01T00:00:00Z,10,11,9,10.5,100\n")


def wire(expected=None):
    data = {"adapter": "csv", "path": "data/registered.csv", "interval_ns": "60000000000",
            "action_free": True}
    if expected is not None:
        data["expected_sha256"] = expected
    return {"data": data}


def test_dataset_form_has_data_only_config_and_deterministic_preview():
    digest = sha256(DATA).hexdigest()
    compiled = compile_configuration("dataset_validate", wire(digest))
    assert set(compiled) == {"data"}
    assert compiled["data"]["interval_ns"] == 60_000_000_000
    preview = prepare_form("dataset_validate", wire(digest), DATA)
    identity = form_identity("dataset_validate", wire(digest), digest)
    assert preview["kind"] == "dataset_validate"
    assert preview["preview_fingerprint"] == identity["preview_fingerprint"]
    assert len(preview["assumptions"]) == 4
    options = form_options()["dataset_validate"]
    assert options["adapters"] == ["csv", "alpaca"]
    assert "data.expected_sha256" in options["fields"]


def test_dataset_form_rejects_strategy_and_stale_registered_file():
    digest = sha256(DATA).hexdigest()
    with pytest.raises(FormError):
        compile_configuration("dataset_validate", {**wire(), "strategy": {}})
    with pytest.raises(FormError):
        prepare_form("dataset_validate", wire("0" * 64), DATA)
    with pytest.raises(FormError):
        form_identity("dataset_validate", wire(digest), None)
