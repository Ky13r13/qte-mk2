"""Registry-derived form controls and read-only metadata boundary."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from qte.comparison_config import _historical_experiment, _legacy_experiment, compile_comparison_configuration
from qte.gui.comparison_forms import comparison_form_options
from qte.gui.comparison_inputs import prepare_comparison_inputs
from qte.strategies import CandidateConfig
from qte.strategies.candidates import FAMILIES
from test_gui_comparison_inputs import fixture
from test_gui_job_api import visit


def defaults(fields):
    result = {}
    for path, descriptor in fields.items():
        value = deepcopy(descriptor["default"])
        if descriptor["type"] == "integer_string":
            assert type(value) is str
            value = int(value)
        target = result
        parts = path.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return result


def test_metadata_has_complete_canonical_defaults_without_dataset_or_execution_access():
    options = comparison_form_options()
    assert options["schema_version"] == 1
    assert [item["type"] for item in options["strategies"]] == ["moving_average_regime", *FAMILIES]
    canonical = asdict(CandidateConfig())
    canonical.pop("kind")
    for item in options["strategies"][1:]:
        assert defaults(item["fields"]) == canonical
        assert set(item["exposed_fields"]) <= set(item["fields"])
    sma = options["strategies"][0]
    assert set(sma["fields"]) == {"fast_period", "slow_period", "quantity"}
    assert all(field["type"] == "integer_string" and "default" not in field for field in sma["fields"].values())
    legacy, experiment = options["workflows"]
    assert (legacy["min_windows"], legacy["max_windows"], legacy["max_candidates"]) == (2, 2, 1)
    assert (experiment["min_windows"], experiment["max_windows"], experiment["max_candidates"]) == (3, 24, 32)
    assert defaults(legacy["experiment_fields"]) == _legacy_experiment({})
    assert defaults(experiment["experiment_fields"]) == _historical_experiment({})
    # Returned values are owned; caller edits do not mutate future metadata.
    options["strategies"][1]["fields"]["fast_period"]["default"] = "99"
    assert comparison_form_options()["strategies"][1]["fields"]["fast_period"]["default"] == "10"


@pytest.mark.parametrize("family", FAMILIES)
def test_all_candidate_fields_from_metadata_roundtrip_through_comparison_contract(tmp_path, family):
    catalog, form = fixture(tmp_path)
    prepared = prepare_comparison_inputs(catalog, form)
    import json
    config = json.loads(prepared.configuration_bytes)
    strategy = next(item for item in comparison_form_options()["strategies"] if item["type"] == family)
    parameters = defaults(strategy["fields"])
    config["candidates"] = [{"name": "candidate", "type": family, "symbol": "SPY", "parameters": parameters}]
    assert compile_comparison_configuration(config)["candidates"][0]["parameters"] == parameters


def test_form_metadata_is_authenticated_no_store_and_creates_no_job_or_catalog(tmp_path, monkeypatch):
    from qte.gui.jobs import JobManager
    import qte.gui.comparison_routes as routes
    monkeypatch.setattr(JobManager, "submit", lambda *args, **kwargs: pytest.fail("metadata started a job"))
    monkeypatch.setattr(routes, "prepare_comparison_inputs", lambda *args: pytest.fail("metadata captured data"))
    async def check(client, headers):
        path = "/api/v1/comparisons/form-options"
        assert (await client.get(path)).status_code == 403
        assert (await client.get(path + "?x=1", headers=headers)).status_code == 400
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == comparison_form_options()
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get("/research/new/comparison", headers={"sec-fetch-site": "none"})).status_code == 200
        assert not (tmp_path / "build/gui/jobs").exists()
        assert not (tmp_path / ".cache").exists()
    visit(tmp_path, check)


def test_shell_exposes_comparison_form_without_changing_existing_routes():
    script = (Path(__file__).parents[2] / "python/qte/gui/static/app.js").read_text()
    assert 'import { createComparisonForms } from "./comparison_forms.js"' in script
    assert 'textLink("/research/new/comparison", "New comparison")' in script
    assert 'comparisonForms.load()' in script
