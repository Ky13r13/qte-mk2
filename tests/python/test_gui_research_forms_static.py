"""Source-level contract checks for the dependency-free G5b.3 form module."""

from pathlib import Path
import unittest


SCRIPT = (Path(__file__).parents[2] / "python/qte/gui/static/research_forms.js").read_text(encoding="utf-8")


class ResearchFormsStaticTests(unittest.TestCase):
    def test_module_is_injected_and_uses_only_fixed_local_endpoints(self) -> None:
        self.assertIn("export function createResearchForms(ui)", SCRIPT)
        self.assertIn('ui.api("/api/v1/research/form-options")', SCRIPT)
        self.assertIn('ui.api("/api/v1/configs/validate"', SCRIPT)
        self.assertIn('ui.api("/api/v1/jobs"', SCRIPT)
        self.assertIn("return { loadRun:", SCRIPT)
        self.assertNotIn("fetch(", SCRIPT)
        self.assertNotIn("innerHTML", SCRIPT)
        self.assertNotIn("localStorage", SCRIPT)
        self.assertNotIn("sessionStorage", SCRIPT)

    def test_registry_choices_and_candidate_fields_come_from_metadata(self) -> None:
        self.assertIn("for (const strategy of metadata.strategies || [])", SCRIPT)
        self.assertIn("for (const name of strategy.exposed_fields || [])", SCRIPT)
        self.assertIn("metadata.timeframes", SCRIPT)
        self.assertIn("metadata.regimes", SCRIPT)
        self.assertIn("Object.entries(metadata.strategy_parameter_fields || {})", SCRIPT)
        self.assertNotIn('const families =', SCRIPT)
        self.assertNotIn('"trend", "breakout"', SCRIPT)

    def test_integer_text_is_never_rounded_through_preview_or_controls(self) -> None:
        self.assertIn('descriptor?.type === "integer_string"', SCRIPT)
        self.assertIn("return { present: true, value }", SCRIPT)
        self.assertIn("configuration: clone(configuration)", SCRIPT)
        self.assertIn("...envelope(state.preview.configuration)", SCRIPT)
        self.assertNotIn("parseInt(", SCRIPT)
        self.assertNotIn("envelope(result.effective_configuration)", SCRIPT)
        self.assertNotIn("configuration: clone(result.effective_configuration)", SCRIPT)
        self.assertIn("Number.isFinite(number)", SCRIPT)  # Economic values only.

    def test_optional_omit_value_null_and_adapter_switches_are_explicit(self) -> None:
        self.assertIn('value: "omit", text: "Use default (omit)"', SCRIPT)
        self.assertIn('value: "null", text: "Set null"', SCRIPT)
        self.assertIn('if (mode?.value === "omit"', SCRIPT)
        self.assertIn('if (mode?.value === "null")', SCRIPT)
        self.assertIn('const csv = dataCore.adapter.input.value === "csv"', SCRIPT)
        self.assertIn("csvOnly.disabled = !csv", SCRIPT)
        self.assertIn('if (data.adapter === "csv")', SCRIPT)
        self.assertIn("if (Object.keys(schema).length) data.schema = schema", SCRIPT)

    def test_sampling_keeps_exact_timestamp_lines_and_one_selected_grid(self) -> None:
        self.assertIn('value: "interval", text: "Fixed interval"', SCRIPT)
        self.assertIn('value: "timestamps", text: "Explicit timestamps"', SCRIPT)
        self.assertIn("timestamps.value.split(/\\r?\\n/)", SCRIPT)
        self.assertIn("result.timestamps_ns = values", SCRIPT)
        self.assertIn("result.interval_ns = interval.read().value", SCRIPT)
        self.assertIn("body.disabled = !enabled.checked", SCRIPT)
        self.assertNotIn("timestamps.value.trim", SCRIPT)

    def test_preview_is_revision_bound_and_every_edit_invalidates_authority(self) -> None:
        self.assertIn("const revision = state.revision", SCRIPT)
        self.assertIn("revision !== state.revision", SCRIPT)
        self.assertIn('form.addEventListener("input", invalidateDraft)', SCRIPT)
        self.assertIn('form.addEventListener("change", invalidateDraft)', SCRIPT)
        self.assertIn("state.preview = null", SCRIPT)
        self.assertIn("previewArea.replaceChildren()", SCRIPT)
        self.assertIn("if (state.previewing || state.submitting", SCRIPT)
        self.assertIn("launchButton.disabled = true", SCRIPT)
        self.assertIn("Preview assumptions", SCRIPT)

    def test_launch_is_frozen_single_flight_and_uncertain_retry_reuses_it(self) -> None:
        self.assertIn("globalThis.crypto.getRandomValues(bytes)", SCRIPT)
        self.assertIn("state.intent = newIntent()", SCRIPT)
        self.assertIn("if (state.submitting", SCRIPT)
        self.assertIn("state.submitting = true", SCRIPT)
        self.assertIn("body: JSON.stringify(state.intent)", SCRIPT)
        self.assertIn("Retry same request", SCRIPT)
        self.assertIn("same request and idempotency key", SCRIPT)
        self.assertIn("A job may already exist", SCRIPT)
        self.assertIn("const retryOfUncertainIntent = state.uncertain", SCRIPT)
        self.assertIn("Review recent jobs", SCRIPT)
        self.assertIn("Start a new run", SCRIPT)
        self.assertIn("timeout_seconds: timeoutNumber", SCRIPT)

    def test_definitive_errors_clear_authority_but_bounded_fields_receive_focus(self) -> None:
        self.assertIn("problem.status >= 400 && problem.status < 500", SCRIPT)
        self.assertIn("state.intent = null", SCRIPT)
        self.assertIn("state.preview = null", SCRIPT)
        self.assertIn("problem.field", SCRIPT)
        self.assertIn("field.slice(0, 160)", SCRIPT)
        self.assertIn("if (parent === current) break", SCRIPT)
        self.assertIn('target.setAttribute("aria-invalid", "true")', SCRIPT)
        self.assertIn("target.focus({ preventScroll: false })", SCRIPT)
        self.assertIn('if (ancestor.tagName === "DETAILS") ancestor.open = true', SCRIPT)
        self.assertIn('role: "alert"', SCRIPT)
        self.assertNotIn("No data has been changed", SCRIPT)

    def test_secondary_engine_controls_are_collapsed_but_remain_available(self) -> None:
        self.assertIn('text: "Advanced engine"', SCRIPT)
        self.assertIn('fields["engine.history_capacity"]', SCRIPT)
        self.assertIn('fields["engine.random_seed"]', SCRIPT)
        self.assertIn("...readObject(engineAdvanced)", SCRIPT)

    def test_numbers_reject_surrounding_whitespace_and_selected_strategy_owns_focus(self) -> None:
        self.assertIn("value.trim() !== value", SCRIPT)
        self.assertIn('key.startsWith("configuration.strategy.")', SCRIPT)
        self.assertIn("state.fields.delete(key)", SCRIPT)
        self.assertIn("state.fields.set(`configuration.strategy.${name}`, value.input)", SCRIPT)

    def test_lab_control_plane_schema_stays_numeric_and_forbidden_fields_do_not_exist(self) -> None:
        self.assertIn("const configuration = { schema_version: 1", SCRIPT)
        self.assertNotIn("configuration.candidates", SCRIPT)
        self.assertNotIn("configuration.factories", SCRIPT)
        self.assertNotIn("configuration.windows", SCRIPT)
        self.assertNotIn("configuration.environment", SCRIPT)
        self.assertNotIn("configuration.command", SCRIPT)


if __name__ == "__main__":
    unittest.main()
