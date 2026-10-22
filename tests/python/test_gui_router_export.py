"""End-to-end validation of the optional macro-router diagnostics table."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qte.cli import run_research
from qte.gui.artifacts import ArtifactError, inspect_artifact
from test_router_config import profile as router_profile


ROOT = Path(__file__).parents[2]


def _rehash(root: Path) -> None:
    checks = {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
              for item in root.iterdir() if item.name != "complete.json"}
    (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": checks}), encoding="utf-8")


def _router_export(tmp_path: Path, *, two_candidates: bool = False) -> Path:
    config = json.loads((ROOT / "examples/research-run.json").read_text(encoding="utf-8"))
    config["data"]["path"] = str((ROOT / "examples/research-bars.csv").resolve())
    profile = router_profile()
    if two_candidates:
        profile["candidates"].append({**profile["candidates"][0], "kind": "breakout"})
    # The example bars are in 2024, rather than the unit fixture's epoch.
    start = 1_704_067_200_000_000_000
    for observation in profile["macro_observations"]:
        observation["reference_ns"] = observation["available_ns"] = start
    config["strategy"] = {"type": "macro_router", "symbol": "SPY", "profile": profile}
    path = tmp_path / "router.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    output = tmp_path / "build" / "router"
    run_research(path, output, export_version=2)
    return output


def test_router_export_is_optional_and_verified_against_its_manifest_profile(tmp_path):
    root = _router_export(tmp_path)
    wrapper = json.loads((root / "research_export.json").read_text(encoding="utf-8"))
    assert wrapper["files"]["route_events"] == "route_events.json"
    assert wrapper["capabilities"]["route_events"] == "recorded"
    assert inspect_artifact(root).metadata["integrity"] == "verified"

    ordinary = tmp_path / "build" / "ordinary"
    run_research(ROOT / "examples/research-run.json", ordinary, export_version=2)
    ordinary_wrapper = json.loads((ordinary / "research_export.json").read_text(encoding="utf-8"))
    assert "route_events" not in ordinary_wrapper["files"]
    assert "route_events" not in ordinary_wrapper["capabilities"]
    assert not (ordinary / "route_events.json").exists()


@pytest.mark.parametrize(("field", "value"), [
    ("capabilities", []), ("capabilities", None), ("files", []), ("files", None),
])
def test_router_export_rejects_nonobject_wrapper_optional_containers_before_lookup(tmp_path, field, value):
    root = _router_export(tmp_path)
    wrapper_path = root / "research_export.json"
    wrapper = json.loads(wrapper_path.read_text(encoding="utf-8"))
    wrapper[field] = value
    wrapper_path.write_text(json.dumps(wrapper), encoding="utf-8")
    _rehash(root)
    with pytest.raises(ArtifactError) as failure:
        inspect_artifact(root)
    assert failure.value.code == "artifact_invalid"


@pytest.mark.parametrize("mutation", ["priority", "unconfigured_active"])
def test_router_export_rejects_targets_or_active_families_outside_the_profile_contract(tmp_path, mutation):
    root = _router_export(tmp_path, two_candidates=True)
    path = root / "route_events.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    event = value["events"][0]
    # With a low-volatility risk-on decision, priorities select breakout before
    # trend when both are configured and permitted.
    if mutation == "priority":
        event["target_family"] = event["active_family"] = "trend"
    else:
        # Preserve the as-of target; retirement may retain a different active
        # family, which must still be one of the configured candidates.
        event["phase"] = "retiring"
        event["active_family"] = "mean_reversion"
    path.write_text(json.dumps(value), encoding="utf-8")
    _rehash(root)
    with pytest.raises(ArtifactError) as failure:
        inspect_artifact(root)
    assert failure.value.code == "artifact_invalid"


@pytest.mark.parametrize("mutation", ["future", "profile_mismatch", "proxy_gamma"])
def test_router_export_rejects_rehashed_noncausal_or_profile_inconsistent_diagnostics(tmp_path, mutation):
    root = _router_export(tmp_path)
    path = root / "route_events.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    event = value["events"][0]
    if mutation == "future":
        event["macro_inputs"][0]["available_ns"] = event["timestamp_ns"] + 1
    elif mutation == "profile_mismatch":
        event["reasons"] = ["macro:risk_off"]
    else:
        # Add a valid-looking proxy snapshot, then falsely describe it as
        # qualified gamma positioning.  The strict reader must still reject it.
        event["dealer_input"] = {"signed_gamma": -1, "reference_ns": event["timestamp_ns"],
                                 "available_ns": event["timestamp_ns"], "source_id": "dealer",
                                 "vintage_id": "v1", "methodology_id": "method", "underlying": "SPX",
                                 "classification": "proxy", "unit": "usd_per_1pct_move"}
        event["dealer_regime"] = "negative_gamma"
    path.write_text(json.dumps(value), encoding="utf-8")
    _rehash(root)
    with pytest.raises(ArtifactError) as failure:
        inspect_artifact(root)
    assert failure.value.code == "artifact_invalid"
