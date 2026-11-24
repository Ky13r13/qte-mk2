from hashlib import sha256
import json
from pathlib import Path

import pytest

from qte.adapters.session_bundle import load_session_bundle
from qte.cli import run_research
from qte.data_validation import verify_dataset_output, write_dataset_output
from qte.gui.artifacts import inspect_artifact
from qte.market_export import validate_market_recording

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
BUNDLE = EXAMPLES / "session-bars.json"


def test_delayed_short_session_round_trips_through_cli_validation_and_chart(tmp_path):
    raw = BUNDLE.read_bytes()
    data = {"adapter": "session_bundle", "path": str(BUNDLE), "expected_sha256": sha256(raw).hexdigest()}
    config = tmp_path / "dataset.json"
    config.write_text(json.dumps({"data": data}))
    saved = verify_dataset_output(write_dataset_output(config, tmp_path / "dataset"))
    assert saved["summary"]["interval_ns"] is None
    assert saved["summary"]["availability"]["available_at"] == "end_plus_declared_delay"

    out = run_research(EXAMPLES / "session-run.json", tmp_path / "run", export_version=2)
    assert inspect_artifact(out).kind == "research_export_v2"
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert manifest["dataset_hash"] == saved["manifest"]["dataset_hash"]
    recording = json.loads((out / "market_data.json").read_bytes())
    candles = validate_market_recording(recording, manifest)
    assert len(candles) == 11
    assert candles[-1]["end_ns"] - candles[-1]["start_ns"] == 1_800_000_000_000
    assert all(c["available_ns"] - c["end_ns"] == 1_000_000_000 for c in candles)
    assert manifest["timing"]["observation_end_ns"] == candles[-1]["end_ns"]
    assert manifest["dataset_end_ns"] == candles[-1]["available_ns"]
    candles[0]["available_ns"] -= 1
    with pytest.raises(ValueError):
        validate_market_recording(recording, manifest)


def test_import_rejects_undeclared_calendar_day_and_coerced_timestamps():
    bundle = json.loads(BUNDLE.read_bytes())
    bundle["schedule"]["days"].pop(1)
    with pytest.raises(ValueError, match="per coverage date"):
        load_session_bundle(json.dumps(bundle).encode())
    bundle = json.loads(BUNDLE.read_bytes())
    bundle["bars"][0]["start_ns"] = True
    with pytest.raises(ValueError, match="exact UTC"):
        load_session_bundle(json.dumps(bundle).encode())
    raw = BUNDLE.read_bytes().replace(b'"action_free": true', b'"action_free": false, "action_free": true')
    with pytest.raises(ValueError, match="duplicate"):
        load_session_bundle(raw)


def test_session_bundle_can_be_previewed_and_reused_from_registered_validation(tmp_path):
    from qte.gui.catalog import Catalog
    from qte.gui.dataset_views import dataset_view
    from qte.gui.research_forms import prepare_form

    root = tmp_path / "examples"
    root.mkdir()
    (root / "session-bars.json").write_bytes(BUNDLE.read_bytes())
    config = root / "validate.json"
    data = {"adapter": "session_bundle", "path": "session-bars.json"}
    config.write_text(json.dumps({"data": data}))
    write_dataset_output(config, tmp_path / "build/validated")
    catalog = Catalog(tmp_path)
    item = catalog.register("build/validated")
    view = dataset_view(catalog, item["id"])
    assert view["source"]["state"] == "available"
    wire = view["source"]["data"]
    assert wire["expected_sha256"] == sha256(BUNDLE.read_bytes()).hexdigest()
    preview = prepare_form("dataset_validate", {"data": wire}, BUNDLE.read_bytes())
    assert any("declared delay" in line for line in preview["assumptions"])
