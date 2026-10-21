"""Disclosure propagation between financial records and router diagnostics."""
from __future__ import annotations

from pathlib import Path

import pytest

from qte.cli import run_research
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.run_views import RunViews
from test_gui_catalog import _unknown
from test_gui_router_export import ROOT, _router_export


def _files(detail: dict) -> dict[str, dict]:
    return {row["name"]: row for row in detail["files"]}


def test_protected_legacy_equity_blocks_route_file_and_route_table_until_reveal(tmp_path):
    output = _router_export(tmp_path)
    catalog = Catalog(tmp_path)
    catalog.register(_unknown(tmp_path, "protected-equity", (output / "equity.csv").read_bytes()))
    detail = catalog.register("build/router")
    files = _files(detail)
    assert files["route_events.json"]["protection"] == "protected"
    assert files["route_events.json"]["available"] is False

    with pytest.raises(ArtifactError, match="disclosure") as blocked:
        RunViews(catalog).table(detail["id"], "route_events")
    assert blocked.value.code == "holdout_protected"

    catalog.reveal(detail["id"], "reveal outside protocol")
    assert _files(catalog.get(detail["id"]))["route_events.json"]["available"] is True
    assert RunViews(catalog).table(detail["id"], "route_events")["table"] == "route_events"


def test_route_only_protection_does_not_taint_financial_files_or_views(tmp_path):
    output = _router_export(tmp_path)
    catalog = Catalog(tmp_path)
    catalog.register(_unknown(tmp_path, "protected-route", (output / "route_events.json").read_bytes()))
    detail = catalog.register("build/router")
    files = _files(detail)
    assert files["route_events.json"]["available"] is False
    for name in ("report.json", "equity.csv", "equity_events.csv", "fills.csv",
                 "fill_events.csv", "orders.csv", "order_snapshots.csv", "positions.csv",
                 "closed_trades.csv", "open_trades.csv", "order_events.csv"):
        assert files[name]["available"] is True, name
    assert RunViews(catalog).table(detail["id"], "equity")["table"] == "equity"


def test_ordinary_v2_projection_behavior_is_unchanged_without_route_file(tmp_path):
    output = run_research(
        ROOT / "examples/research-run.json", tmp_path / "build/ordinary", export_version=2,
    )
    catalog = Catalog(tmp_path)
    catalog.register(_unknown(tmp_path, "protected-equity", (output / "equity.csv").read_bytes()))
    detail = catalog.register("build/ordinary")
    files = _files(detail)
    assert "route_events.json" not in files
    assert files["equity.csv"]["available"] is False
    assert files["equity_events.csv"]["available"] is False
    assert files["report.json"]["available"] is True
