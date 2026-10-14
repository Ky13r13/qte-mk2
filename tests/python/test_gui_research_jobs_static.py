"""Nonbehavioral source checks for the bounded research-jobs client module."""

from pathlib import Path
import unittest


STATIC = Path(__file__).parents[2] / "python" / "qte" / "gui" / "static"


class ResearchJobsStaticTests(unittest.TestCase):
    """These are source-shape checks; browser interaction belongs to the root gate."""

    def test_jobs_module_is_injected_and_does_not_bypass_the_shell_api(self) -> None:
        script = (STATIC / "research_jobs.js").read_text(encoding="utf-8")
        self.assertIn("export function createResearchJobs(ui)", script)
        self.assertIn("return { recent, load, dispose }", script)
        self.assertIn("ui.api(", script)
        self.assertNotIn("fetch(", script)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)

    def test_jobs_use_fixed_bounded_endpoints_and_explicit_mutations(self) -> None:
        script = (STATIC / "research_jobs.js").read_text(encoding="utf-8")
        self.assertIn('`/api/v1/jobs/${encodeURIComponent(id)}`', script)
        self.assertIn('`/api/v1/jobs/${encodeURIComponent(id)}/log?offset=0&limit=65536`', script)
        self.assertIn('`/api/v1/jobs/${encodeURIComponent(lastJob.id)}/cancel`', script)
        self.assertIn('method: "POST"', script)
        self.assertIn('JSON.stringify({ schema_version: 1 })', script)
        self.assertIn('job?.state === "queued" || job?.state === "running"', script)
        self.assertIn('job?.state === "succeeded"', script)
        self.assertIn('"/api/v1/catalog/register"', script)
        self.assertIn('ui.navigate(`/research/${encodeURIComponent(artifact.id)}`)', script)
        self.assertIn('artifact.kind === "dataset_validation_v1"', script)
        self.assertIn('ui.navigate(`/data/${encodeURIComponent(artifact.id)}`)', script)
        self.assertNotIn("reveal-holdout", script)
        self.assertNotIn("/files/", script)

    def test_polling_is_single_flight_visible_only_and_finitely_resumable(self) -> None:
        script = (STATIC / "research_jobs.js").read_text(encoding="utf-8")
        self.assertIn("let inFlight = false", script)
        self.assertIn("if (!active(ticket, request) || inFlight || (mutation && !allowMutation)) return;", script)
        self.assertIn("globalThis.document.hidden", script)
        self.assertIn("burst >= 60", script)
        self.assertIn('"Resume live updates"', script)
        self.assertIn("globalThis.setTimeout", script)
        self.assertIn("}, 1000);", script)
        self.assertIn("visibilitychange", script)
        self.assertIn("stopPolling();", script)
        self.assertIn("if (!active(ticket, request)) return;", script)
        self.assertIn("actions.replaceChildren();", script)
        self.assertIn("if (!root || !summary)", script)

    def test_recent_jobs_are_paged_without_module_owned_list_fetching(self) -> None:
        script = (STATIC / "research_jobs.js").read_text(encoding="utf-8")
        self.assertIn("function recent(payload, error = null, onPage = null)", script)
        self.assertIn('"Previous jobs"', script)
        self.assertIn('"Next jobs"', script)
        self.assertIn("onPage(payload.offset + payload.limit)", script)
        self.assertNotIn('"/api/v1/jobs?"', script)

    def test_neutral_job_styles_preserve_narrow_controls_and_bounded_log(self) -> None:
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        for selector in (".research-jobs", ".job-detail", ".job-actions", ".job-poll-controls", ".job-log"):
            self.assertIn(selector, css)
        self.assertIn("max-height: 24rem", css)
        self.assertIn("@media (max-width: 38rem)", css)
        self.assertIn(".job-actions button, .job-poll-controls button { width: 100%; }", css)


if __name__ == "__main__":
    unittest.main()
