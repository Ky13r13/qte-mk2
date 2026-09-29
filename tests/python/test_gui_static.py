"""Source-level regression checks for the packaged G1 shell, not browser tests."""

from pathlib import Path
import unittest


STATIC = Path(__file__).parents[2] / "python" / "qte" / "gui" / "static"


class GuiStaticContentTests(unittest.TestCase):
    def test_shell_loads_only_local_external_assets(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/assets/app.css"', html)
        self.assertIn('src="/assets/app.js"', html)
        self.assertIn("Content-Security-Policy", html)
        self.assertNotIn("<style", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("frame-ancestors", html)

    def test_styles_preserve_the_sparse_neutral_contract(self) -> None:
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        for color in ("#FAFAF8", "#202020", "#595959", "#D6D6D2"):
            self.assertIn(color, css)
        self.assertIn("border-radius: 0", css)
        self.assertIn(".skip-link", css)
        self.assertIn("@media", css)

    def test_script_keeps_request_token_out_of_persistent_browser_storage(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('headers.set("X-QTE-Token", state.token)', script)
        self.assertIn("body.innerHTML = payload.rendered_html", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)

    def test_script_preserves_accessible_and_deep_link_fragments(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('const rawHref = link.getAttribute("href")', script)
        self.assertIn('if (!rawHref || rawHref.startsWith("#")) return;', script)
        self.assertIn('href.pathname + href.search + href.hash', script)
        self.assertIn("function scrollToFragment()", script)
        self.assertIn("scrollToFragment();", script)

    def test_script_reports_actual_document_rows_and_unavailable_sources(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("Array.isArray(payload.items) ? payload.items.length : 0", script)
        self.assertIn('item.availability === "unavailable" ? "Source unavailable"', script)
        self.assertIn('document.availability === "unavailable" ? "Source unavailable"', script)

    def test_script_keeps_session_state_when_logout_is_not_confirmed(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('redirectOnUnauthorized: false', script)
        self.assertIn('if (error.status !== 401)', script)
        self.assertIn("Log out failed. Your current session may still be active; try again.", script)

    def test_research_catalog_is_explicit_paged_and_does_not_autoscan(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('"/api/v1/catalog/register"', script)
        self.assertIn('placeholder: "build/strategy-lab-20260922"', script)
        self.assertIn('`/api/v1/artifacts?${params}`', script)
        self.assertIn('["Name", "Kind", "Registered", "Status"]', script)
        self.assertIn('"Path date (if present)"', script)
        self.assertIn("this page never scans build/", script)
        self.assertIn('limit: "25"', script)

    def test_research_detail_keeps_holdout_explicit_without_previewing_files(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('confirmation.value !== "reveal outside protocol"', script)
        self.assertIn("JSON.stringify({ schema_version: 1, confirmation })", script)
        self.assertIn('`/api/v1/artifacts/${encodeURIComponent(id)}/reveal-holdout`', script)
        self.assertIn("Holdout contents are not shown automatically.", script)
        self.assertIn('artifact.needs_disclosure === true', script)
        self.assertIn("blocked_protected_count", script)
        self.assertIn("this newly blocked content still requires an explicit confirmation.", script)
        self.assertIn("File contents are not previewed here.", script)
        self.assertIn('`/api/v1/artifacts/${encodeURIComponent(id)}/files?${params}`', script)

    def test_research_mutation_failures_do_not_claim_no_data_changed(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("Artifact registration was not confirmed.", script)
        self.assertIn("Holdout disclosure was not confirmed.", script)

    def test_script_discards_stale_views_and_malformed_route_encodings(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("function beginView()", script)
        self.assertIn("function isCurrentView(request)", script)
        self.assertIn("if (!isCurrentView(request)) return;", script)
        self.assertIn("const request = activeView;", script)
        self.assertIn("function decodeRouteSegment(path, prefix)", script)
        self.assertIn("This page address is malformed.", script)

    def test_single_run_view_uses_server_values_and_safe_svg_coordinates(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('`/api/v1/runs/${encodeURIComponent(id)}`', script)
        self.assertIn('`/api/v1/runs/${encodeURIComponent(id)}/series?${params}`', script)
        self.assertIn('`/api/v1/runs/${encodeURIComponent(id)}/tables/${encodeURIComponent(table)}?${params}`', script)
        self.assertIn('new URLSearchParams(window.location.search).get("view")', script)
        self.assertIn('?view=${encodeURIComponent(view)}', script)
        self.assertIn("row[column]", script)
        self.assertIn(": columns.slice(0, 4);", script)
        self.assertIn('BigInt(point.timestamp_ns)', script)
        self.assertIn('point.gap_before ? "M" : "L"', script)
        self.assertIn('sampled_equity === "recorded"', script)
        self.assertIn("Sampled equity — not recorded", script)
        self.assertIn('createElementNS("http://www.w3.org/2000/svg"', script)
        self.assertIn("undefined_reason", script)

    def test_authenticated_file_download_does_not_use_tokenless_anchor_urls(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function apiBlob(path)", script)
        self.assertIn('headers.set("X-QTE-Token", state.token)', script)
        self.assertIn("URL.createObjectURL(blob)", script)
        self.assertIn("/files/${encodeURIComponent(file.id)}/download", script)
        self.assertNotIn('href: `/api/v1/artifacts/', script)

    def test_invalid_or_protected_runs_retain_catalog_controls(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('artifactValue(artifact, "integrity") === "verified"', script)
        self.assertIn('artifact.needs_disclosure === false', script)
        self.assertIn('if (canOpenRun(artifact) && !catalogRecord)', script)
        self.assertIn('await loadArtifact(id, 0, true, "overview", error.message)', script)
        self.assertIn('The catalog record remains available below.', script)

    def test_all_record_fields_and_sampling_remain_accessible(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('for (const field of columns)', script)
        self.assertIn('Open complete record', script)
        self.assertIn('Back to records', script)
        self.assertIn('samplingLabel(metric.recorded_sampling_policy)', script)
        self.assertIn('equity / equityScale - low', script)
        self.assertIn('Chart range (UTC nanoseconds)', script)
        self.assertIn('Recorded metrics always describe the complete run.', script)
        self.assertIn('params.set(key, range[key])', script)

    def test_v2_owned_result_views_are_capability_gated(self) -> None:
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('["single_run_v1", "research_export_v2"].includes(artifact.kind)', script)
        self.assertIn('run.capabilities?.[capability] === "recorded"', script)
        self.assertIn('`${label} — not recorded`', script)
        self.assertIn('Capabilities not recorded in this export:', script)
        self.assertIn('positions: ["symbol", "quantity", "mark_price", "mark_ns"]', script)
        self.assertIn('closed_trades: ["symbol", "direction", "opened_ns", "net_realized_pnl"]', script)
        self.assertIn('order_events: ["timestamp_ns", "sequence", "order_id", "kind"]', script)


if __name__ == "__main__":
    unittest.main()
