import json
from pathlib import Path

import pytest

from qte.cli import run_research


ROOT = Path(__file__).resolve().parents[2]


def config_file(tmp_path, **strategy):
    config = json.loads((ROOT / "examples/trend-research-run.json").read_text())
    config["data"]["path"] = str(ROOT / "examples/research-bars.csv")
    config["strategy"].update(strategy)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return path


def test_candidate_run_records_effective_defaults_and_fills(tmp_path):
    output = run_research(config_file(tmp_path), tmp_path / "result")
    report = json.loads((output / "report.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert report["fill_count"] == 2
    assert manifest["strategy"]["kind"] == "trend"
    assert manifest["strategy"]["max_holding_bars"] == 20
    assert manifest["strategy"]["allocation_fraction"] == 0.1
    assert (output / "complete.json").is_file()


@pytest.mark.parametrize("strategy", [{"unknown": 1}, {"quantity": 4}, {"type": "imaginary"}])
def test_candidate_run_rejects_unknown_parameters(tmp_path, strategy):
    with pytest.raises(ValueError):
        run_research(config_file(tmp_path, **strategy), tmp_path / "result")
    assert not (tmp_path / "result").exists()
