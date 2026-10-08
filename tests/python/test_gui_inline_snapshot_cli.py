"""Captured inline snapshots preserve ordinary CLI economics, without GUI launch."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from qte.cli import run_research
from qte.gui.artifacts import strict_json
from qte.gui.catalog import Catalog
from qte.gui.form_inputs import capture_form_inputs
from qte.gui.inline_inputs import prepare_inline_inputs
from qte.gui.job_inputs import verify_snapshot, write_snapshot
from qte.gui.research_forms import config_origin, prepare_form
from qte.strategy_lab import run_lab

ROOT = Path(__file__).parents[2]


def snapshot(repository, kind, configuration):
    captured = capture_form_inputs(repository, kind, config_origin(), configuration)
    Catalog(repository).authorize_job_inputs(captured)
    preview = prepare_form(kind, strict_json(captured.configuration_bytes), captured.data_bytes)
    prepared = prepare_inline_inputs(captured, preview)
    saved = write_snapshot(repository, "a" * 32, prepared)
    assert verify_snapshot(repository, saved.job_id, saved.snapshot_hashes) == saved
    return preview, saved


def test_inline_research_snapshot_matches_direct_cli_results_after_source_edit(tmp_path):
    (tmp_path / "data").mkdir()
    source = tmp_path / "data/bars.csv"
    shutil.copyfile(ROOT / "examples/research-bars.csv", source)
    wire = {
        "data": {"adapter": "csv", "path": "data/bars.csv", "interval_ns": "3600000000000",
                 "action_free": True},
        "strategy": {"type": "moving_average_regime", "symbol": "SPY", "fast_period": "2",
                     "slow_period": "3", "quantity": "10"},
        "engine": {"initial_cash": 100000.0, "random_seed": "18446744073709551615"},
        "execution": {"commission_bps": 1.0, "spread_bps": 2.0, "slippage_bps": 2.0},
        "sampling": {"interval_ns": "3600000000000", "max_staleness_ns": "0", "periods_per_year": 8766.0},
    }
    preview, saved = snapshot(tmp_path, "research_run", wire)
    direct_config = tmp_path / "direct.json"
    direct_config.write_text(json.dumps(preview["effective_configuration"]), encoding="utf-8")
    direct = run_research(direct_config, tmp_path / "direct-result", export_version=2)
    source.write_bytes(b"changed source must not affect captured run")
    actual = run_research(tmp_path / saved.effective_config_path, tmp_path / "captured-result", export_version=2)
    # Only configuration/provenance path records may differ; every recorded
    # economic result, including owned order/trade/fill events, must match.
    files = {path.name for path in direct.iterdir()} - {"manifest.json", "complete.json"}
    assert {path.name for path in actual.iterdir()} == {path.name for path in direct.iterdir()}
    assert "report.json" in files and "fill_events.csv" in files
    for name in sorted(files):
        assert (actual / name).read_bytes() == (direct / name).read_bytes(), name
    assert verify_snapshot(tmp_path, saved.job_id, saved.snapshot_hashes) == saved
    assert not (tmp_path / ".cache").exists()


def test_inline_lab_config_only_snapshot_matches_every_direct_cli_artifact(tmp_path):
    wire = {"schema_version": 1, "seed": "817", "bars_per_window": "128",
            "timeframes": ["hourly"], "regimes": ["low_vol_trend"]}
    preview, saved = snapshot(tmp_path, "strategy_lab", wire)
    direct_config = tmp_path / "direct.json"
    direct_config.write_text(json.dumps(preview["effective_configuration"]), encoding="utf-8")
    direct = run_lab(direct_config, tmp_path / "direct-lab")
    direct_config.write_text("{}", encoding="utf-8")
    actual = run_lab(tmp_path / saved.effective_config_path, tmp_path / "captured-lab")
    expected = {path.relative_to(direct).as_posix(): path.read_bytes()
                for path in direct.rglob("*") if path.is_file()}
    observed = {path.relative_to(actual).as_posix(): path.read_bytes()
                for path in actual.rglob("*") if path.is_file()}
    assert observed == expected
    summary = json.loads(observed["summary.json"])
    assert summary["evidence"] == "synthetic_only"
    assert summary["experiments"][0]["selected_candidate"] is None
    assert summary["experiments"][0]["holdout_status"] == "not_run_no_selection"
    assert saved.data_path is None
    assert set(saved.snapshot_hashes) == {"submitted-form.json", "effective-config.json", "input-manifest.json"}
    assert not (tmp_path / ".cache").exists()
