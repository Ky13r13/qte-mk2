"""Recorded candles must match the executed dataset, never reconstructed prices."""
import hashlib
import json
from pathlib import Path

import pytest

from qte.cli import run_research
from qte.gui.artifacts import ArtifactError, inspect_artifact
from qte.gui.catalog import Catalog

ROOT = Path(__file__).parents[2]


def test_single_run_market_records_exact_candles_and_execution(tmp_path):
    output = run_research(ROOT / "examples/research-run.json", tmp_path / "build/run", export_version=2)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/run")
    market = catalog.market_view(item["id"])
    recorded = json.loads((output / "market_data.json").read_text())
    assert market["status"] == "recorded"
    assert market["candles"] == recorded["candles"]
    assert all(row["available_ns"] == row["end_ns"] for row in market["candles"])
    assert market["regimes"] == []
    assert market["runs"][0]["equity"]
    assert all(fill["signal_ns"] <= fill["timestamp_ns"] for fill in market["runs"][0]["fills"])
    with pytest.raises(ArtifactError):
        catalog.market_view(item["id"], symbol="Spy")


def test_market_price_tampering_fails_even_with_updated_file_checksum(tmp_path):
    output = run_research(ROOT / "examples/research-run.json", tmp_path / "run", export_version=2)
    path = output / "market_data.json"
    payload = json.loads(path.read_text())
    payload["candles"][0]["high"] += 1
    path.write_text(json.dumps(payload))
    completion = json.loads((output / "complete.json").read_text())
    completion["sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "complete.json").write_text(json.dumps(completion))
    with pytest.raises(ArtifactError):
        inspect_artifact(output)


def test_legacy_market_is_explicitly_unrecorded(tmp_path):
    run_research(ROOT / "examples/research-run.json", tmp_path / "build/run")
    catalog = Catalog(tmp_path)
    item = catalog.register("build/run")
    market = catalog.market_view(item["id"])
    assert market["status"] == "not_recorded"
    assert market["candles"] == []
