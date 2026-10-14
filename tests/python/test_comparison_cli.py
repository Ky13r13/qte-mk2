"""CLI comparison entry points and the catalog's guarded artifact boundary."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog
from qte.gui.documents import DocumentLibrary


ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("example,scenario_count", [("experiment", 8), ("sma", 4)])
def test_example_command_runs_offline_and_never_selects_a_synthetic_winner(tmp_path, example, scenario_count):
    output = tmp_path / "build" / example
    result = subprocess.run([sys.executable, "-m", "qte", "compare", "--config",
                             str(ROOT / "examples/comparisons" / f"{example}.json"),
                             "--output", str(output)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "report.json").read_text())
    assert len(report["scenarios"]) == scenario_count
    if example == "experiment":
        assert report["selected_candidate"] is None
        assert report["holdout_status"] == "not_run_no_selection"
        assert all(row["role"] != "test" for row in report["scenarios"])
        assert all("synthetic_not_promotion_evidence" in row["exclusions"] for row in report["assessments"])
    else:
        assert {row["sample"] for row in report["scenarios"]} == {"in_sample", "out_of_sample"}
        assert {row["cost_scenario"] for row in report["scenarios"]} == {"baseline_zero_cost", "stressed_costs"}
    complete = json.loads((output / "complete.json").read_text())
    actual = {p.relative_to(output).as_posix(): sha256(p.read_bytes()).hexdigest()
              for p in output.rglob("*") if p.is_file() and p.name != "complete.json"}
    assert complete == {"schema_version": 1, "sha256": actual}
    # Verification allows safe files, never raw mixed/holdout contents.
    catalog = Catalog(tmp_path)
    item = catalog.register(f"build/{example}")
    assert item["kind"] == "comparison_export_v1"
    assert item["status"]["integrity"] == "verified"
    assert item["status"]["evidence"] == "synthetic"
    assert any(row["available"] for row in item["files"])
    manifest = json.loads((output / "manifest.json").read_text())
    guarded = set(manifest["holdout_files"]) | set(manifest["mixed_role_files"])
    assert all(not row["available"] for row in item["files"] if row["name"] in guarded)
    report_id = sha256(b"report.json").hexdigest()
    with pytest.raises(ArtifactError, match="disclosure"):
        catalog.read_file(item["id"], report_id)


def test_comparison_marker_never_downgrades_to_legacy_reader(tmp_path):
    root = tmp_path / "build/comparison"
    root.mkdir(parents=True)
    # Even a broken marker claims a new format: don't infer an old safe layout.
    for name in ("comparison_artifact.json", "configuration.json", "manifest.json", "complete.json"):
        (root / name).write_text("{}")
    item = Catalog(tmp_path).register("build/comparison")
    assert item["kind"] == "comparison_export_v1"
    assert item["status"]["integrity"] == "invalid"
    assert all(not row["available"] for row in item["files"])


def test_cli_rejects_invalid_comparison_before_creating_output(tmp_path):
    config = tmp_path / "invalid.json"
    config.write_text('{"schema_version":1,"workflow":"arbitrary.module:factory"}')
    output = tmp_path / "not-created"
    result = subprocess.run([sys.executable, "-m", "qte", "compare", "--config", str(config),
                             "--output", str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert not output.exists()


def test_comparison_and_strategy_guides_are_available_in_the_local_library():
    library = DocumentLibrary(ROOT)
    for document_id in ("gui-comparisons", "test-build-guide"):
        rendered = library.render(document_id)
        assert rendered["document"]["source_sha256"]
        assert library.by_id[document_id].implementation_status == "mixed"
