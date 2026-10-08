"""Read-only form preview, exact values, and authorization-before-diagnostics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from qte.gui.catalog import Catalog
from qte.gui.form_inputs import capture_form_inputs
from test_gui_catalog import _unknown
from test_gui_job_api import BASE, visit

ROOT = Path(__file__).parents[2]
PREVIEW = "/api/v1/configs/validate"
OPTIONS = "/api/v1/research/form-options"
ORIGIN = {"kind": "inline_form", "path_base": "repository_root", "form_schema_version": 1}


@pytest.fixture
def repository(tmp_path):
    (tmp_path / "data").mkdir()
    shutil.copyfile(ROOT / "examples/research-bars.csv", tmp_path / "data/bars.csv")
    return tmp_path


def body(kind="research_run"):
    configuration = {
        "data": {"adapter": "csv", "path": "data/bars.csv", "interval_ns": "3600000000000",
                 "action_free": True},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY", "fast_period": "2",
                     "slow_period": "3", "quantity": "10"},
        "engine": {"initial_cash": 100000.0, "random_seed": "18446744073709551615"},
        "sampling": {"interval_ns": "3600000000000", "max_staleness_ns": "0", "periods_per_year": 8766.0},
    } if kind == "research_run" else {
        "schema_version": 1, "seed": "4611686018427387903", "bars_per_window": "128", "timeframes": ["hourly"]}
    return {"schema_version": 1, "kind": kind, "config_origin": dict(ORIGIN), "configuration": configuration}


@pytest.mark.parametrize("kind", ["research_run", "strategy_lab"])
def test_preview_is_exact_repeatable_and_creates_no_jobs_or_catalog(repository, kind, monkeypatch):
    from qte.gui.jobs import JobManager
    monkeypatch.setattr(JobManager, "submit", lambda *args: pytest.fail("preview launched a job"))

    async def check(client, headers):
        options = await client.get(OPTIONS, headers=headers)
        assert options.status_code == 200, options.text
        assert options.json()["schema_version"] == 1
        assert options.json()["config_origin"] == ORIGIN
        first = await client.post(PREVIEW, headers=headers, json=body(kind))
        assert first.status_code == 200, first.text
        result = first.json()
        second = await client.post(PREVIEW, headers=headers, json=body(kind))
        assert second.json() == result
        assert first.headers["cache-control"] == options.headers["cache-control"] == "no-store"
        assert result["schema_version"] == 1 and result["kind"] == kind
        assert result["config_origin"] == ORIGIN
        assert len(result["preview_fingerprint"]) == 64
        assert len(result["canonical_config_sha256"]) == 64
        assert result["captured_config_sha256"] != result["canonical_config_sha256"]
        assert result["assumptions"] and all(isinstance(value, str) for value in result["assumptions"])
        config = result["effective_configuration"]
        lineage = result["lineage"]
        if kind == "research_run":
            assert config["engine"]["random_seed"] == "18446744073709551615"
            assert config["strategy"]["quantity"] == "10"
            assert lineage["input_disclosure"] == "unknown"
            assert len(lineage["input_identities"]) == 2
        else:
            assert type(config["schema_version"]) is int and config["schema_version"] == 1
            assert config["seed"] == "4611686018427387903"
            assert result["data_sha256"] is None
            assert lineage["input_disclosure"] == "not_required"
            assert lineage["evidence"] == "synthetic_only"
            assert lineage["input_identities"] == ["file-sha256:" + result["captured_config_sha256"]]
        assert not (repository / "build/gui").exists()
        assert not (repository / ".cache").exists()
    visit(repository, check)


def test_preview_auth_origin_query_envelope_and_bounded_json(repository):
    async def check(client, headers):
        assert (await client.get(OPTIONS)).status_code == 403
        assert (await client.post(PREVIEW, json=body())).status_code == 403
        assert (await client.post(PREVIEW, headers={**headers, "Origin": "https://outside.invalid"},
                                  json=body())).status_code == 403
        assert (await client.get(OPTIONS + "?path=data/bars.csv", headers=headers)).status_code == 400
        assert (await client.post(PREVIEW + "?x=1", headers=headers, json=body())).status_code == 400
        for payload in ({**body(), "command": "not-allowed"}, {**body(), "kind": []},
                        {**body(), "configuration": []}, {**body(), "schema_version": True},
                        {**body(), "config_origin": {**ORIGIN, "form_schema_version": True}}):
            assert (await client.post(PREVIEW, headers=headers, json=payload)).status_code == 400
        raw = json.dumps(body()).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)
        assert (await client.post(PREVIEW, headers={**headers, "Content-Type": "application/json"},
                                  content=raw)).status_code == 400
        assert (await client.post(PREVIEW, headers={**headers, "Content-Type": "application/json"},
                                  content=b" " * 16385)).status_code == 413
        assert (await client.post(PREVIEW, headers=headers, content="not JSON")).status_code == 415
        assert not (repository / "build/gui").exists()
    visit(repository, check)


@pytest.mark.parametrize("fault", ["strategy", "numeric", "csv", "csv_field_limit"])
def test_protected_data_blocks_even_malformed_validation_before_disclosure(repository, fault):
    payload = body()
    path = repository / "data/bars.csv"
    if fault == "strategy": payload["configuration"]["strategy"]["type"] = "PRIVATE_STRATEGY_SENTINEL"
    elif fault == "numeric": payload["configuration"]["engine"]["random_seed"] = 12
    elif fault == "csv": path.write_bytes(b"PRIVATE_ADAPTER_SENTINEL,malformed\n")
    else: path.write_bytes(b"PRIVATE_ADAPTER_SENTINEL" + b"x" * 140000 + b"\n")
    catalog = Catalog(repository)
    protected = catalog.register(_unknown(repository, "protected-input", path.read_bytes()))
    before = sorted(catalog.disclosures.root.iterdir())

    async def check(client, headers):
        response = await client.post(PREVIEW, headers=headers, json=payload)
        assert response.status_code == 403 and response.json()["error"]["code"] == "holdout_protected"
        assert "PRIVATE_" not in response.text and "invalid_configuration" not in response.text
        assert sorted(catalog.disclosures.root.iterdir()) == before
        assert catalog.list()[1] == 1
        catalog.reveal(protected["id"], "reveal outside protocol")
        response = await client.post(PREVIEW, headers=headers, json=payload)
        assert response.status_code == 400, response.text
        error = response.json()["error"]
        assert error["code"] == "invalid_configuration" and len(error["field"]) <= 160
        assert "PRIVATE_" not in response.text
        assert not (repository / "build/gui/jobs").exists()
    visit(repository, check)


@pytest.mark.parametrize("kind", ["research_run", "strategy_lab"])
def test_inline_configuration_hash_is_authorized_including_synthetic_lab(repository, kind):
    payload = body(kind)
    captured = capture_form_inputs(repository, kind, payload["config_origin"], payload["configuration"])
    catalog = Catalog(repository)
    protected = catalog.register(_unknown(repository, "protected-config", captured.configuration_bytes))

    async def check(client, headers):
        response = await client.post(PREVIEW, headers=headers, json=payload)
        assert response.status_code == 403 and response.json()["error"]["code"] == "holdout_protected"
        catalog.reveal(protected["id"], "reveal outside protocol")
        response = await client.post(PREVIEW, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        lineage = response.json()["lineage"]
        assert lineage["input_disclosure"] == "revealed_outside_protocol"
        assert lineage["protected_input_identities"] == ["file-sha256:" + captured.config_sha256]
    visit(repository, check)


def test_successful_preview_rechecks_new_copied_hash_protection(repository):
    catalog = Catalog(repository)

    async def check(client, headers):
        assert (await client.post(PREVIEW, headers=headers, json=body())).status_code == 200
        catalog.register(_unknown(repository, "protected-later", (repository / "data/bars.csv").read_bytes()))
        response = await client.post(PREVIEW, headers=headers, json=body())
        assert response.status_code == 403 and response.json()["error"]["code"] == "holdout_protected"
    visit(repository, check)


def test_preview_validates_the_authorized_capture_not_a_later_path_read(repository, monkeypatch):
    original = Catalog.authorize_job_inputs
    path = repository / "data/bars.csv"
    original_bytes = path.read_bytes()

    def authorize_then_mutate(self, captured):
        lineage = original(self, captured)
        path.write_bytes(b"later malformed source must never be reread")
        return lineage

    monkeypatch.setattr(Catalog, "authorize_job_inputs", authorize_then_mutate)

    async def check(client, headers):
        response = await client.post(PREVIEW, headers=headers, json=body())
        assert response.status_code == 200, response.text
        assert response.json()["data_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    visit(repository, check)


@pytest.mark.parametrize("kind", ["research_run", "strategy_lab"])
def test_unknown_hashes_cannot_bypass_unavailable_disclosure_storage(repository, kind):
    target = repository / "saved-disclosures"
    target.mkdir()
    root = repository / "build/gui"
    root.mkdir(parents=True)
    (root / "disclosures").symlink_to(target)

    async def check(client, headers):
        response = await client.post(PREVIEW, headers=headers, json=body(kind))
        assert response.status_code == 503 and response.json()["error"]["code"] == "disclosure_unavailable"
    visit(repository, check)
