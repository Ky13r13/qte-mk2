"""Authenticated inline macro-router job and route-audit API acceptance."""
from __future__ import annotations

import json

from qte.cli import run_research
from test_gui_form_api import ORIGIN, PREVIEW, repository
from test_gui_job_api import ROOT, finished, visit
from test_router_config import profile as native_router_profile


START_NS = 1_704_204_000_000_000_000
INTEGER_CANDIDATE_FIELDS = {
    "fast_period", "slow_period", "breakout_period", "exit_period", "atr_period",
    "contraction_window", "contraction_memory", "max_holding_bars",
}


def _profile_text(*, growth: float = 3.0) -> str:
    profile = native_router_profile()
    for candidate in profile["candidates"]:
        for name in INTEGER_CANDIDATE_FIELDS & candidate.keys():
            candidate[name] = str(candidate[name])
    for observation in profile["macro_observations"]:
        observation["reference_ns"] = str(START_NS)
        observation["available_ns"] = str(START_NS)
        if observation["feature"] == "growth_z":
            observation["value"] = growth
    return json.dumps(profile, separators=(",", ":"), allow_nan=False)


def _configuration() -> dict:
    return {
        "data": {"adapter": "csv", "path": "data/bars.csv",
                 "interval_ns": "3600000000000", "action_free": True},
        "strategy": {"type": "macro_router", "symbol": "SPY",
                     "profile": _profile_text()},
        "engine": {"initial_cash": 100000.0, "history_capacity": "256", "random_seed": "0"},
        "sampling": {"interval_ns": "3600000000000", "max_staleness_ns": "0",
                     "periods_per_year": 8766.0},
    }


def _preview_body(configuration: dict) -> dict:
    return {"schema_version": 1, "kind": "research_run", "config_origin": dict(ORIGIN),
            "configuration": configuration}


def test_inline_router_job_registers_exact_asof_route_audit_with_bounded_paging(repository):
    # Exercise the explicit not-recorded contract against a real ordinary v2 export too.
    ordinary_config = ROOT / "examples/research-run.json"
    run_research(ordinary_config, repository / "build/ordinary", export_version=2)

    async def check(client, headers):
        configuration = _configuration()
        preview = await client.post(PREVIEW, headers=headers, json=_preview_body(configuration))
        assert preview.status_code == 200, preview.text
        fingerprint = preview.json()["preview_fingerprint"]

        changed = json.loads(json.dumps(configuration))
        changed["strategy"]["profile"] = _profile_text(growth=4.0)
        stale = await client.post("/api/v1/jobs", headers=headers, json={
            **_preview_body(changed), "preview_fingerprint": fingerprint,
            "idempotency_key": "router-stale-fingerprint-0001", "timeout_seconds": 30,
        })
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "stale_preview"

        submitted = await client.post("/api/v1/jobs", headers=headers, json={
            **_preview_body(configuration), "preview_fingerprint": fingerprint,
            "idempotency_key": "router-inline-real-job-0001", "timeout_seconds": 30,
        })
        assert submitted.status_code == 200, submitted.text
        job = await finished(client, headers, submitted.json()["job"]["id"])
        assert job["state"] == "succeeded", job

        registered = await client.post("/api/v1/catalog/register", headers=headers, json={
            "schema_version": 1, "path": job["output_path"],
        })
        assert registered.status_code == 200, registered.text
        artifact_id = registered.json()["artifact"]["id"]
        route = await client.get(
            f"/api/v1/runs/{artifact_id}/tables/route_events?offset=0&limit=1", headers=headers)
        assert route.status_code == 200, route.text
        table = route.json()
        assert table["table"] == "route_events" and table["total"] >= 1
        assert len(table["rows"]) == 1 and table["limit"] == 1 and table["offset"] == 0
        event = table["rows"][0]
        assert isinstance(event["timestamp_ns"], str)
        assert event["macro_inputs"]
        assert all(isinstance(item["reference_ns"], str)
                   and isinstance(item["available_ns"], str)
                   and int(item["available_ns"]) <= int(event["timestamp_ns"])
                   for item in event["macro_inputs"])
        tail = await client.get(
            f"/api/v1/runs/{artifact_id}/tables/route_events?offset={table['total']}&limit=1",
            headers=headers)
        assert tail.status_code == 200 and tail.json()["rows"] == []
        bad = await client.get(
            f"/api/v1/runs/{artifact_id}/tables/route_events?limit=201", headers=headers)
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_query"

        ordinary = await client.post("/api/v1/catalog/register", headers=headers, json={
            "schema_version": 1, "path": "build/ordinary",
        })
        assert ordinary.status_code == 200, ordinary.text
        old_id = ordinary.json()["artifact"]["id"]
        missing = await client.get(
            f"/api/v1/runs/{old_id}/tables/route_events", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_recorded"

    visit(repository, check)
