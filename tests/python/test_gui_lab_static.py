"""Source-level G4 lab-view checks, not behavioral browser tests."""

from pathlib import Path
import unittest


STATIC = Path(__file__).parents[2] / "python" / "qte" / "gui" / "static"


class GuiLabStaticTests(unittest.TestCase):
    def test_lab_module_is_a_small_injected_factory(self) -> None:
        script = (STATIC / "experiments.js").read_text(encoding="utf-8")
        self.assertIn("export function createExperimentViews(ui)", script)
        self.assertIn("return { load, isView:", script)
        self.assertIn("ui.api(", script)
        self.assertIn("ui.loadArtifact(id, 0, true", script)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("fetch(", script)

    def test_lab_uses_only_role_filtered_experiment_endpoints(self) -> None:
        script = (STATIC / "experiments.js").read_text(encoding="utf-8")
        self.assertIn('`/api/v1/experiments/${encodeURIComponent(id)}', script)
        self.assertIn('/tables/${encodeURIComponent(table)}?${tableParams}', script)
        self.assertIn('item.role === "train" || item.role === "validation"', script)
        self.assertIn('"comparison", "macro", "regime_decisions"', script)
        self.assertIn('"Select a recorded train or validation window before viewing this table."', script)
        self.assertIn('tableParams.set("window", windowName)', script)
        self.assertNotIn("reveal-holdout", script)
        self.assertNotIn("/files/", script)

    def test_lab_keeps_recorded_context_and_withheld_holdout_explicit(self) -> None:
        script = (STATIC / "experiments.js").read_text(encoding="utf-8")
        self.assertIn("recorded validation screening", script)
        self.assertIn("structured contents withheld", script)
        self.assertIn('"Recorded context"', script)
        self.assertIn('"daily 24h (synthetic clock)"', script)
        self.assertIn("experiment.warnings", script)
        self.assertIn('values.map((warning) => String(warning)).join(" ")', script)
        self.assertIn("selection.selected_candidate", script)
        self.assertIn("no selection; recorded validation screening", script)
        self.assertIn("appendRecordValue(list, name, value)", script)
        self.assertNotIn("selected_candidate.sort", script)
        self.assertNotIn("portfolio", script)
        self.assertNotIn("chart", script)

    def test_lab_tables_have_four_default_columns_and_complete_record_focus(self) -> None:
        script = (STATIC / "experiments.js").read_text(encoding="utf-8")
        self.assertIn('comparison: ["candidate", "base_return", "stress_return", "result"]', script)
        self.assertIn('macro: ["feature", "value", "reference_ns", "available_ns"]', script)
        self.assertIn('regime_decisions: ["timestamp_ns", "macro_regime", "volatility_regime", "permitted_families"]', script)
        self.assertIn("Open complete record", script)
        self.assertIn("Back to records", script)
        self.assertIn("appendRecordValue", script)
        self.assertIn("typeof value === \"object\"", script)
        self.assertIn("new Set([...(Array.isArray(payload.columns)", script)

    def test_lab_keeps_window_controls_and_deep_link_state_after_a_safe_table_load(self) -> None:
        script = (STATIC / "experiments.js").read_text(encoding="utf-8")
        self.assertIn('window.history.pushState({}, "", href(id, view, timeframe, windowName))', script)
        self.assertIn("function navigate(id, view, timeframe, windowName", script)
        self.assertIn("? [windowSelector(id, experiment, selected, timeframe, windowName)]", script)
        self.assertIn("...warnings(experiment), ...selector, section", script)
        self.assertIn("...warnings(experiment), ui.element(\"h2\"", script)

    def test_main_shell_loads_the_module_and_verified_labs_open_safely(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('import { createExperimentViews } from "./experiments.js"', script)
        self.assertIn("const experimentViews = createExperimentViews({", script)
        self.assertIn('artifact.kind === "strategy_lab_v1"', script)
        self.assertIn("experimentViews.load(id);", script)
        self.assertIn('query.get("lab_view")', script)
        self.assertIn("experimentViews.isView(requestedLabView)", script)
        self.assertNotIn("canOpenExperiment(artifact) && artifact.needs_disclosure", script)


if __name__ == "__main__":
    unittest.main()
