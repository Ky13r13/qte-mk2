import { createExperimentViews } from "./experiments.js";

const app = document.querySelector("#app");
const pageStatus = document.querySelector("#page-status");
const logoutButton = document.querySelector("#logout-button");
const state = { token: null, session: null, library: null };
let activeView = 0;
const runViews = new Set(["overview", "metrics", "orders", "fills", "equity", "sampled_equity", "equity_table", "sampled_equity_table", "positions", "closed_trades", "open_trades", "order_events", "provenance", "files", "catalog"]);

function beginView() { activeView += 1; return activeView; }
function isCurrentView(request) { return request === activeView; }

function element(name, attributes = {}, children = []) {
  const node = document.createElement(name);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function replaceView(...nodes) {
  app.replaceChildren(...nodes);
  document.querySelector("#main-content").focus({ preventScroll: true });
}

function setStatus(message = "") { pageStatus.textContent = message; }

function scrollToFragment() {
  const fragment = window.location.hash.slice(1);
  if (!fragment) return;
  try {
    document.getElementById(decodeURIComponent(fragment))?.scrollIntoView();
  } catch {
    // Ignore malformed URL fragments rather than treating a document as unavailable.
  }
}

function setNavigation(section) {
  for (const link of document.querySelectorAll("[data-nav]")) {
    link.toggleAttribute("aria-current", link.dataset.nav === section);
    if (link.dataset.nav === section) link.setAttribute("aria-current", "page");
  }
}

function apiError(response, payload) {
  const message = payload?.error?.message || `Request failed (${response.status}).`;
  const error = new Error(message);
  error.status = response.status;
  error.code = payload?.error?.code;
  return error;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (state.token && options.includeToken !== false) headers.set("X-QTE-Token", state.token);
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  let payload = null;
  try { payload = await response.json(); } catch { /* The server response is intentionally not rendered. */ }
  if (response.status === 401 && options.redirectOnUnauthorized !== false) {
    state.token = null;
    state.session = null;
    window.location.assign("/login");
    throw new Error("Your session has ended. Enter a new terminal code to continue.");
  }
  if (!response.ok) throw apiError(response, payload);
  return payload;
}

async function apiBlob(path) {
  const headers = new Headers();
  if (state.token) headers.set("X-QTE-Token", state.token);
  const response = await fetch(path, { headers, credentials: "same-origin" });
  if (response.status === 401) {
    state.token = null;
    state.session = null;
    window.location.assign("/login");
    throw new Error("Your session has ended. Enter a new terminal code to continue.");
  }
  if (!response.ok) throw new Error(`Download failed (${response.status}).`);
  return response.blob();
}

function renderError(message, detail = "No data has been changed.") {
  replaceView(
    element("h1", { text: "Unavailable" }),
    element("p", { class: "error", text: message }),
    element("p", { class: "muted", text: detail }),
  );
}

function textLink(href, text) { return element("a", { href, text }); }

function statusWord(value) { return element("span", { class: "status-word", text: value || "unknown" }); }

function libraryTable(items) {
  const table = element("table");
  table.append(element("caption", { class: "visually-hidden", text: "Library documents" }));
  const head = element("thead", {}, [element("tr", {}, ["Title", "Category", "Source", "Status"].map((label) => element("th", { scope: "col", text: label })))]);
  const body = element("tbody");
  for (const item of items) {
    const sourceText = item.availability === "unavailable" ? "Source unavailable" : item.source_path || "Unavailable";
    const source = element("span", { class: "source-path", text: sourceText });
    body.append(element("tr", {}, [
      element("td", {}, [textLink(`/library/${encodeURIComponent(item.id)}`, item.title || "Untitled document")]),
      element("td", { text: item.category || "unknown" }),
      element("td", {}, [source]),
      element("td", {}, [statusWord(item.implementation_status)]),
    ]));
  }
  table.append(head, body);
  return table;
}

function pagination(payload, query) {
  const nav = element("nav", { class: "pagination", "aria-label": "Library pages" });
  const shown = Array.isArray(payload.items) ? payload.items.length : 0;
  nav.append(element("p", { class: "muted", text: `${shown} of ${payload.total} documents` }));
  const previous = element("button", { type: "button", text: "Previous" });
  previous.disabled = payload.offset <= 0;
  previous.addEventListener("click", () => loadLibrary(query, Math.max(0, payload.offset - payload.limit)));
  const next = element("button", { type: "button", text: "Next" });
  next.disabled = payload.offset + payload.limit >= payload.total;
  next.addEventListener("click", () => loadLibrary(query, payload.offset + payload.limit));
  nav.append(previous, next);
  return nav;
}

async function loadLibrary(query = "", offset = 0) {
  const request = beginView();
  setNavigation("library");
  setStatus("Loading library.");
  const params = new URLSearchParams({ offset: String(offset), limit: "25" });
  if (query) params.set("q", query);
  try {
    const payload = await api(`/api/v1/library?${params}`);
    if (!isCurrentView(request)) return;
    state.library = payload;
    const heading = element("h1", { text: "Library" });
    const lede = element("p", { class: "lede", text: "Approved QTE documentation and contracts. Read-only." });
    const form = element("form", { class: "search-form", role: "search" });
    const label = element("label", { class: "field-label" }, [element("span", { text: "Search library" })]);
    const input = element("input", { type: "search", name: "q", value: query, maxlength: "200", autocomplete: "off" });
    label.append(input);
    form.append(label, element("button", { type: "submit", text: "Search" }));
    form.addEventListener("submit", (event) => { event.preventDefault(); loadLibrary(input.value, 0); });
    const content = payload.items?.length
      ? [element("div", { class: "table-wrap" }, [libraryTable(payload.items)]), pagination(payload, query)]
      : [element("p", { text: "No approved documents match this literal search." })];
    replaceView(heading, lede, form, ...content);
    setStatus("");
  } catch (error) { if (isCurrentView(request)) { setStatus(""); renderError(error.message); } }
}

function documentMetadata(document) {
  const details = element("details", { class: "document-meta" });
  details.append(element("summary", { text: "Source and implementation details" }));
  const list = element("dl");
  const source = document.availability === "unavailable" ? "Source unavailable" : document.source_path;
  const values = [["Source", source], ["SHA-256", document.source_sha256], ["Source availability", document.availability], ["Implementation status", document.implementation_status]];
  for (const [label, value] of values) {
    list.append(element("dt", { text: label }), element("dd", { class: label === "SHA-256" ? "hash" : "source-path", text: value || "Unavailable" }));
  }
  details.append(list);
  return details;
}

async function loadDocument(id) {
  const request = beginView();
  setNavigation("library");
  setStatus("Loading document.");
  try {
    const payload = await api(`/api/v1/documents/${encodeURIComponent(id)}`);
    if (!isCurrentView(request)) return;
    const summary = payload.document || {};
    const breadcrumb = element("p", { class: "muted" }, [textLink("/library", "Library"), globalThis.document.createTextNode(" / "), globalThis.document.createTextNode(summary.title || "Document")]);
    const status = element("p", { class: "lede", text: `Implementation status: ${summary.implementation_status || "unknown"}.` });
    const body = element("article", { class: "document-body" });
    // This is the sole HTML insertion point. The server owns sanitization and rendering.
    body.innerHTML = payload.rendered_html || "";
    replaceView(breadcrumb, status, body, documentMetadata(summary));
    scrollToFragment();
    setStatus("");
  } catch (error) { if (isCurrentView(request)) { setStatus(""); renderError(error.message); } }
}

async function loadUnavailable(section, title) {
  const request = beginView();
  setNavigation(section);
  setStatus("Loading capability status.");
  try {
    const payload = await api("/api/v1/capabilities");
    if (!isCurrentView(request)) return;
    const capability = (payload.capabilities || []).find((item) => item.id === section);
    const stateWord = capability?.state || "unavailable";
    const reason = capability?.reason || "This area is not available yet.";
    replaceView(
      element("h1", { text: title }),
      element("p", { class: "lede", text: `Status: ${stateWord}.` }),
      element("section", { class: "unavailable" }, [element("p", { text: reason }), element("p", { class: "muted", text: "This area has no controls, metrics, connections, or simulated results yet." })]),
    );
    setStatus("");
  } catch (error) { if (isCurrentView(request)) { setStatus(""); renderError(error.message); } }
}

function artifactValue(artifact, name) {
  const status = artifact.status;
  if (status && typeof status === "object" && status[name] !== undefined) return status[name];
  return artifact[name];
}

function humanize(value) {
  return String(value || "unknown").replaceAll("_", " ");
}

function artifactStatus(artifact) {
  return artifactValue(artifact, "combined_status") || `${humanize(artifactValue(artifact, "export"))} export`;
}

function artifactTable(items) {
  const table = element("table");
  table.append(element("caption", { class: "visually-hidden", text: "Registered research artifacts" }));
  const head = element("thead", {}, [element("tr", {}, ["Name", "Kind", "Registered", "Status"].map((label) => element("th", { scope: "col", text: label })))]);
  const body = element("tbody");
  for (const artifact of items) {
    body.append(element("tr", {}, [
      element("td", {}, [textLink(`/research/${encodeURIComponent(artifact.id)}`, artifact.name || "Unnamed artifact")]),
      element("td", { text: humanize(artifact.kind) }),
      element("td", { text: artifact.registered_at || "Registration time unavailable" }),
      element("td", {}, [statusWord(artifactStatus(artifact))]),
    ]));
  }
  table.append(head, body);
  return table;
}

function artifactPagination(payload, offset) {
  const nav = element("nav", { class: "pagination", "aria-label": "Research artifact pages" });
  const shown = Array.isArray(payload.items) ? payload.items.length : 0;
  nav.append(element("p", { class: "muted", text: `${shown} of ${payload.total} registered artifacts` }));
  const previous = element("button", { type: "button", text: "Previous" });
  previous.disabled = offset <= 0;
  previous.addEventListener("click", () => loadResearch(Math.max(0, offset - payload.limit)));
  const next = element("button", { type: "button", text: "Next" });
  next.disabled = offset + payload.limit >= payload.total;
  next.addEventListener("click", () => loadResearch(offset + payload.limit));
  nav.append(previous, next);
  return nav;
}

async function registerArtifact(path, submit) {
  const request = activeView;
  submit.disabled = true;
  setStatus("Registering artifact.");
  try {
    const payload = await api("/api/v1/catalog/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ schema_version: 1, path }),
    });
    if (!isCurrentView(request)) return;
    const artifact = payload.artifact || payload;
    if (!artifact.id) throw new Error("The registration response did not identify an artifact.");
    window.history.pushState({}, "", `/research/${encodeURIComponent(artifact.id)}`);
    loadArtifact(artifact.id);
  } catch (error) {
    if (isCurrentView(request)) setStatus(`${error.message} Artifact registration was not confirmed.`);
  } finally {
    submit.disabled = false;
  }
}

async function loadResearch(offset = 0) {
  const request = beginView();
  setNavigation("research");
  setStatus("Loading registered artifacts.");
  try {
    const params = new URLSearchParams({ offset: String(offset), limit: "25" });
    const payload = await api(`/api/v1/artifacts?${params}`);
    if (!isCurrentView(request)) return;
    const heading = element("h1", { text: "Research" });
    const lede = element("p", { class: "lede", text: "Registered research artifacts. Registration is explicit; this page never scans build/." });
    const form = element("form", { class: "artifact-form" });
    const label = element("label", { class: "field-label" }, [element("span", { text: "Repository-relative artifact path" })]);
    const input = element("input", { type: "text", name: "path", required: "", maxlength: "512", autocomplete: "off", placeholder: "build/strategy-lab-20260922" });
    const submit = element("button", { type: "submit", text: "Register artifact" });
    label.append(input);
    form.append(label, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      registerArtifact(input.value.trim(), submit);
    });
    const content = payload.items?.length
      ? [element("div", { class: "table-wrap" }, [artifactTable(payload.items)]), artifactPagination(payload, offset)]
      : [element("p", { text: "No research artifacts are registered." })];
    replaceView(heading, lede, form, ...content);
    setStatus("");
  } catch (error) { if (isCurrentView(request)) { setStatus(""); renderError(error.message); } }
}

function artifactErrors(errors) {
  if (!Array.isArray(errors) || !errors.length) return [];
  const list = element("ul", { class: "artifact-errors" });
  for (const problem of errors) list.append(element("li", { text: String(problem) }));
  return [element("section", { class: "artifact-problems", "aria-label": "Artifact verification issues" }, [element("h2", { text: "Verification issues" }), list])];
}

function artifactMetadata(artifact) {
  const details = element("details", { class: "artifact-meta" });
  details.append(element("summary", { text: "Artifact metadata" }));
  const list = element("dl");
  const values = [
    ["Registered path", artifact.relative_path], ["Registered", artifact.registered_at],
    ["Path date (if present)", artifact.artifact_date],
    ["Identity", artifact.identity], ["Files", artifact.file_count],
    ["Export", artifactValue(artifact, "export")], ["Integrity", artifactValue(artifact, "integrity")],
    ["Evidence", artifactValue(artifact, "evidence")], ["Scenario outcome", artifactValue(artifact, "scenario_outcome")],
    ["Selection", artifactValue(artifact, "selection")], ["Holdout evaluation", artifactValue(artifact, "holdout_evaluation")],
    ["Disclosure", artifactValue(artifact, "disclosure")], ["Code match", artifactValue(artifact, "code_match")],
  ];
  for (const [label, value] of values) {
    list.append(element("dt", { text: label }), element("dd", { class: label === "Identity" ? "hash" : "", text: humanize(value) }));
  }
  details.append(list);
  return details;
}

function artifactFiles(artifactId, payload, offset, error, onPage = (nextOffset) => loadArtifact(artifactId, nextOffset, true)) {
  const section = element("section", { class: "artifact-files" });
  section.append(element("h2", { text: "File inventory" }), element("p", { class: "muted", text: "File contents are not previewed here." }));
  if (error) {
    section.append(element("p", { class: "error", text: `File inventory unavailable: ${error.message}` }));
    return section;
  }
  const items = payload?.items || [];
  if (!items.length) {
    section.append(element("p", { text: "No files are recorded for this artifact." }));
    return section;
  }
  const table = element("table");
  table.append(element("caption", { class: "visually-hidden", text: "Artifact files" }));
  const head = element("thead", {}, [element("tr", {}, ["Name", "Protection", "Availability", "Size"].map((label) => element("th", { scope: "col", text: label })))]);
  const body = element("tbody");
  for (const file of items) {
    const name = element("span", { class: "source-path", text: file.name || "Unnamed file" });
    const fileCell = element("td", {}, [name]);
    if (file.available) {
      const download = element("button", { class: "file-download", type: "button", text: "Download" });
      download.addEventListener("click", () => downloadArtifactFile(artifactId, file, download));
      fileCell.append(globalThis.document.createTextNode(" "), download);
    }
    body.append(element("tr", {}, [
      fileCell,
      element("td", {}, [statusWord(humanize(file.protection))]),
      element("td", {}, [statusWord(file.available ? "available" : "unavailable")]),
      element("td", { class: "source-path", text: file.size || "unknown" }),
    ]));
  }
  table.append(head, body);
  section.append(element("div", { class: "table-wrap" }, [table]));
  const nav = element("nav", { class: "pagination", "aria-label": "Artifact file pages" });
  const shown = items.length;
  nav.append(element("p", { class: "muted", text: `${shown} of ${payload.total} files` }));
  const previous = element("button", { type: "button", text: "Previous" });
  previous.disabled = offset <= 0;
  previous.addEventListener("click", () => onPage(Math.max(0, offset - payload.limit)));
  const next = element("button", { type: "button", text: "Next" });
  next.disabled = offset + payload.limit >= payload.total;
  next.addEventListener("click", () => onPage(offset + payload.limit));
  nav.append(previous, next);
  section.append(nav);
  return section;
}

async function downloadArtifactFile(artifactId, file, button) {
  button.disabled = true;
  setStatus("Preparing download.");
  try {
    const blob = await apiBlob(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/files/${encodeURIComponent(file.id)}/download`);
    const url = URL.createObjectURL(blob);
    const link = element("a", { href: url, download: file.name || "artifact-file" });
    globalThis.document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus("");
  } catch (error) {
    setStatus(error.message);
  } finally {
    button.disabled = false;
  }
}

function showRevealConfirmation(id, warning) {
  const form = element("form", { class: "holdout-confirmation" });
  const label = element("label", { class: "field-label" }, [element("span", { text: "Type reveal outside protocol to confirm" })]);
  const confirmation = element("input", { type: "text", required: "", autocomplete: "off", maxlength: "64" });
  const submit = element("button", { type: "submit", text: "Confirm holdout disclosure" });
  submit.disabled = true;
  confirmation.addEventListener("input", () => { submit.disabled = confirmation.value !== "reveal outside protocol"; });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    revealHoldout(id, confirmation.value, submit);
  });
  label.append(confirmation);
  form.append(label, submit);
  warning.append(element("p", { class: "error", text: "This immutable disclosure records a reveal outside the protocol." }), form);
  confirmation.focus();
}

function canOpenRun(artifact) {
  return ["single_run_v1", "research_export_v2"].includes(artifact.kind) && artifactValue(artifact, "integrity") === "verified" && artifact.needs_disclosure === false;
}

function canOpenExperiment(artifact) {
  return artifact.kind === "strategy_lab_v1" && artifactValue(artifact, "integrity") === "verified";
}

function renderArtifact(artifact, filesPayload, fileOffset, filesError, viewError = null) {
  const breadcrumb = element("p", { class: "muted" }, [textLink("/research", "Research"), globalThis.document.createTextNode(" / "), globalThis.document.createTextNode(artifact.name || "Artifact")]);
  const status = element("p", { class: "lede", text: `Status: ${artifactStatus(artifact)}. Evidence: ${humanize(artifactValue(artifact, "evidence"))}.` });
  const planned = artifact.kind === "strategy_lab_v1" ? [element("p", { class: "muted", text: "Safe strategy-lab views are role-filtered. This catalog record does not infer a combined return." })] : [];
  const warning = element("section", { class: "artifact-warning", "aria-label": "Holdout disclosure warning" }, [
    element("h2", { text: "Holdout disclosure" }),
    element("p", { text: `Holdout evaluation: ${humanize(artifactValue(artifact, "holdout_evaluation"))}. Disclosure: ${humanize(artifactValue(artifact, "disclosure"))}.` }),
    element("p", { class: "muted", text: "Holdout contents are not shown automatically. A reveal is recorded outside protocol and cannot prove that direct filesystem access did not occur." }),
  ]);
  if (artifact.needs_disclosure === true) {
    const count = artifact.blocked_protected_count;
    const blocked = typeof count === "string" && /^[0-9]+$/.test(count)
      ? count : Number.isSafeInteger(count) && count >= 0 ? String(count) : null;
    const remaining = blocked === null ? "Protected or mixed file contents remain blocked." : `${blocked} protected or mixed file${blocked === "1" ? "" : "s"} remain blocked.`;
    warning.append(element("p", { class: "error", text: remaining }));
    if (artifactValue(artifact, "disclosure") === "revealed_outside_protocol") {
      warning.append(element("p", { class: "muted", text: "A prior disclosure is recorded outside protocol; this newly blocked content still requires an explicit confirmation." }));
    }
    const reveal = element("button", { type: "button", text: "Begin holdout disclosure" });
    reveal.addEventListener("click", () => { reveal.disabled = true; showRevealConfirmation(artifact.id, warning); });
    warning.append(reveal);
  } else if (artifactValue(artifact, "disclosure") === "revealed_outside_protocol") {
    warning.append(element("p", { class: "muted", text: "A disclosure is recorded outside protocol. No protected or mixed file contents are currently blocked." }));
  } else {
    warning.append(element("p", { class: "muted", text: "No protected, mixed, or unclassified files are registered for this artifact." }));
  }
  replaceView(
    breadcrumb,
    element("h1", { text: artifact.name || "Unnamed artifact" }),
    status,
    ...(viewError ? [element("p", { class: "error", text: `Run view unavailable: ${viewError} The catalog record remains available below.` })] : []),
    ...(canOpenRun(artifact) ? [textLink(`/research/${encodeURIComponent(artifact.id)}`, "Open run overview")] : []),
    ...planned,
    ...artifactErrors(artifact.errors),
    warning,
    artifactMetadata(artifact),
    artifactFiles(artifact.id, filesPayload, fileOffset, filesError),
  );
}

function svgElement(name, attributes = {}) {
  const node = globalThis.document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function displayValue(value, fallback = "Not recorded") {
  if (value === null || value === undefined || value === "") return fallback;
  if (Array.isArray(value)) return value.map((item) => displayValue(item, "")).filter(Boolean).join("; ") || fallback;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function metricDisplay(metric) {
  if (!metric || metric.value === null || metric.value === undefined) {
    return `undefined — ${metric?.undefined_reason || "no reason recorded"}`;
  }
  return `${displayValue(metric.value)}${metric.unit ? ` ${metric.unit}` : ""}`;
}

function sampledRecorded(run) { return run.recordings?.sampled_equity === "recorded"; }

function samplingLabel(policy) {
  if (!policy || typeof policy !== "object") return "Not recorded";
  const grid = policy.interval_ns !== undefined ? `interval ${displayValue(policy.interval_ns)} ns` : "explicit timestamp grid";
  return `${displayValue(policy.policy)}; ${grid}; ${displayValue(policy.periods_per_year)} periods/year; maximum staleness ${displayValue(policy.max_staleness_ns)} ns`;
}

function runHeader(id, run, selected) {
  const breadcrumb = element("p", { class: "muted" }, [textLink("/research", "Research"), globalThis.document.createTextNode(" / "), globalThis.document.createTextNode(run.name || "Single run")]);
  const status = element("p", { class: "lede", text: `Status: ${artifactStatus(run)}. Evidence: ${humanize(artifactValue(run, "evidence"))}.` });
  const nav = element("nav", { class: "run-nav", "aria-label": "Single-run views" });
  const views = [["overview", "Overview"], ["metrics", "Metrics"], ["orders", "Orders"], ["fills", "Fills"], ["equity", "Event equity"], ["sampled_equity", "Sampled equity"], ["equity_table", "Equity records"], ["sampled_equity_table", "Sampled records"]];
  const ownedViews = [["positions", "positions", "Positions"], ["closed_trades", "closed_trades", "Closed trades"], ["open_trades", "open_trades", "Open trades"], ["order_events", "order_events", "Order events"]];
  for (const [view, capability, label] of ownedViews) {
    if (run.capabilities?.[capability] === "recorded") views.push([view, label]);
    else nav.append(element("span", { class: "disabled-view", text: `${label} — not recorded` }));
  }
  views.push(["provenance", "Provenance"], ["files", "Files"], ["catalog", "Catalog record"]);
  for (const [view, label] of views) {
    if (["sampled_equity", "sampled_equity_table"].includes(view) && !sampledRecorded(run)) {
      nav.append(element("span", { class: "disabled-view", text: `${label} — not recorded` }));
      continue;
    }
    const link = textLink(`/research/${encodeURIComponent(id)}?view=${encodeURIComponent(view)}`, label);
    if (view === selected) link.setAttribute("aria-current", "page");
    nav.append(link);
  }
  return [breadcrumb, element("h1", { text: run.name || "Single run" }), status, nav];
}

function seriesChart(series) {
  const section = element("section", { class: "equity-chart", "aria-label": "Equity chart" });
  section.append(element("h2", { text: series.sampling === "sampled" ? "Sampled equity" : "Event equity" }));
  const points = Array.isArray(series.points) ? series.points : [];
  if (!points.length) {
    section.append(element("p", { text: "No recorded equity points are available for this view." }));
    return section;
  }
  let timestamps;
  try { timestamps = points.map((point) => BigInt(point.timestamp_ns)); }
  catch { section.append(element("p", { class: "error", text: "Recorded timestamps are unavailable for chart display." })); return section; }
  if (!points.every((point) => Number.isFinite(point.equity))) {
    section.append(element("p", { class: "error", text: "Recorded equity values are unavailable for chart display." }));
    return section;
  }
  const minTime = timestamps.reduce((minimum, value) => value < minimum ? value : minimum);
  const maxTime = timestamps.reduce((maximum, value) => value > maximum ? value : maximum);
  const span = maxTime - minTime;
  const equities = points.map((point) => point.equity);
  const minEquity = Math.min(...equities);
  const maxEquity = Math.max(...equities);
  // Normalize before subtracting: two valid finite extremes can overflow a
  // direct max-min difference. This is display geometry, not PnL calculation.
  const equityScale = Math.max(Math.abs(minEquity), Math.abs(maxEquity), 1);
  const low = minEquity / equityScale;
  const high = maxEquity / equityScale;
  const equitySpan = high - low;
  const width = 720;
  const height = 280;
  const left = 52;
  const right = 16;
  const top = 20;
  const bottom = 34;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const x = (timestamp) => span === 0n ? left + plotWidth / 2 : left + plotWidth * (Number((timestamp - minTime) * 1000000n / span) / 1000000);
  const y = (equity) => equitySpan === 0 ? top + plotHeight / 2 : top + plotHeight * (1 - (equity / equityScale - low) / equitySpan);
  let path = "";
  for (let index = 0; index < points.length; index += 1) {
    const point = points[index];
    path += `${index === 0 || point.gap_before ? "M" : "L"}${x(timestamps[index]).toFixed(2)} ${y(point.equity).toFixed(2)} `;
  }
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${series.sampling} equity chart from server-supplied points` });
  svg.append(
    svgElement("line", { x1: left, y1: top, x2: left, y2: height - bottom, class: "chart-axis" }),
    svgElement("line", { x1: left, y1: height - bottom, x2: width - right, y2: height - bottom, class: "chart-axis" }),
    svgElement("path", { d: path.trim(), class: "chart-line" }),
  );
  const equityLabel = svgElement("text", { x: left, y: 13, class: "chart-label" });
  equityLabel.textContent = "Equity";
  const timeLabel = svgElement("text", { x: width - right, y: height - 8, class: "chart-label", "text-anchor": "end" });
  timeLabel.textContent = "Time";
  svg.append(equityLabel, timeLabel);
  section.append(svg);
  section.append(element("p", { class: "muted source-path", text: `Displayed equity range: ${minEquity} to ${maxEquity}. UTC ns: ${minTime.toString()} to ${maxTime.toString()}.` }));
  section.append(element("p", { class: "muted", text: `Server display points: ${displayValue(series.display_count)} of ${displayValue(series.raw_count)}. Event sequence: ${displayValue(series.sequence_status)}. Gap policy: ${displayValue(series.gap_policy)}.` }));
  if (Array.isArray(series.warnings)) for (const warning of series.warnings) section.append(element("p", { class: "error", text: String(warning) }));
  return section;
}

function chartRange(id, view, sampling, range = {}, expanded = false) {
  const details = element("details", { class: "artifact-meta" });
  if (expanded) details.open = true;
  details.append(element("summary", { text: "Chart range (UTC nanoseconds)" }));
  const form = element("form", { class: "search-form" });
  const inputs = {};
  for (const [name, label] of [["start_ns", "Start (inclusive)"], ["end_ns", "End (inclusive)"]]) {
    const field = element("label", { class: "field-label" }, [element("span", { text: label })]);
    const input = element("input", { type: "text", name, maxlength: "20", pattern: "-?(0|[1-9][0-9]*)", placeholder: "No bound" });
    input.value = range[name] || "";
    inputs[name] = input;
    field.append(input);
    form.append(field);
  }
  form.append(element("button", { type: "submit", text: "Apply chart range" }));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const selected = {};
    for (const [name, input] of Object.entries(inputs)) if (input.value) selected[name] = input.value;
    loadRun(id, view, sampling, 0, selected);
  });
  details.append(element("p", { class: "muted", text: "Display only. Recorded metrics always describe the complete run." }), form);
  return details;
}

function runOverview(id, run, series, range = {}) {
  const metrics = run.metrics || {};
  const headline = element("p", { class: "run-headline", text: `Total return: ${metricDisplay(metrics.total_return)} · Maximum drawdown: ${metricDisplay(metrics.maximum_drawdown)}` });
  const sampling = element("label", { class: "field-label chart-selector" }, [element("span", { text: "Equity series" })]);
  const select = element("select", { name: "sampling" });
  select.append(element("option", { value: "event", text: "Event equity" }));
  const sampled = element("option", { value: "sampled", text: sampledRecorded(run) ? "Sampled equity" : "Sampled equity — not recorded" });
  sampled.disabled = !sampledRecorded(run);
  select.append(sampled);
  select.value = series.sampling;
  select.addEventListener("change", () => loadRun(id, "overview", select.value, 0, range));
  sampling.append(select);
  replaceView(...runHeader(id, run, "overview"), headline, sampling, seriesChart(series), chartRange(id, "overview", series.sampling, range));
}

function runMetrics(id, run) {
  const table = element("table");
  table.append(element("caption", { class: "visually-hidden", text: "Recorded run metrics" }));
  const head = element("thead", {}, [element("tr", {}, ["Metric", "Value", "Recorded sampling"].map((label) => element("th", { scope: "col", text: label })))]);
  const body = element("tbody");
  for (const [name, metric] of Object.entries(run.metrics || {})) {
    body.append(element("tr", {}, [element("td", { text: humanize(name) }), element("td", { text: metricDisplay(metric) }), element("td", { text: samplingLabel(metric.recorded_sampling_policy) })]));
  }
  table.append(head, body);
  replaceView(...runHeader(id, run, "metrics"), element("h2", { text: "Recorded metrics" }), element("div", { class: "table-wrap" }, [table]));
}

function runProvenance(id, run) {
  const details = element("dl");
  const provenance = run.provenance || {};
  const fields = [["Source ID", provenance.source_id], ["Provider", provenance.provider], ["Adapter version", provenance.adapter_version], ["Source schema", provenance.schema_id], ["Source checksum", provenance.source_sha256], ["Execution model", provenance.execution_model], ["Research label", provenance.research_label], ["Dataset start (UTC ns)", provenance.dataset_start_ns], ["Dataset end (UTC ns)", provenance.dataset_end_ns], ["Interval (ns)", provenance.interval_ns], ["Source identity", provenance.source_identity], ["Dataset hash", provenance.dataset_hash], ["Dataset hash algorithm", provenance.dataset_hash_algorithm], ["Assumptions", provenance.assumptions]];
  for (const [label, value] of fields) details.append(element("dt", { text: label }), element("dd", { class: label.includes("identity") ? "hash" : "", text: displayValue(value) }));
  const sampling = element("details", { class: "artifact-meta" });
  sampling.append(element("summary", { text: "Recorded sampling policy" }));
  if (run.provenance?.sampling && typeof run.provenance.sampling === "object") {
    const list = element("dl");
    for (const [name, value] of Object.entries(run.provenance.sampling)) list.append(element("dt", { text: humanize(name) }), element("dd", { text: displayValue(value) }));
    sampling.append(list);
  } else sampling.append(element("p", { text: "Not recorded." }));
  const missing = Object.entries(run.capabilities || {}).filter(([, value]) => value === "not_recorded").map(([name]) => humanize(name));
  const capabilities = element("p", { class: "muted", text: missing.length ? `Capabilities not recorded in this export: ${missing.join(", ")}.` : "All listed result capabilities are recorded in this export." });
  const warnings = (run.warnings || []).map((warning) => element("p", { class: "error", text: String(warning) }));
  replaceView(...runHeader(id, run, "provenance"), element("h2", { text: "Recorded provenance" }), details, sampling, capabilities, ...warnings);
}

function runTable(id, run, payload, name, selected, offset) {
  const table = element("table");
  table.append(element("caption", { class: "visually-hidden", text: `${name} records` }));
  const columns = Array.isArray(payload.columns) ? payload.columns : [];
  const preferred = {
    positions: ["symbol", "quantity", "mark_price", "mark_ns"],
    closed_trades: ["symbol", "direction", "opened_ns", "net_realized_pnl"],
    open_trades: ["symbol", "direction", "opened_ns", "net_realized_pnl"],
    order_events: ["timestamp_ns", "sequence", "order_id", "kind"],
  }[name];
  const displayColumns = preferred ? preferred.filter((column) => columns.includes(column)) : columns.slice(0, 4);
  const head = element("thead", {}, [element("tr", {}, displayColumns.map((column) => element("th", { scope: "col", text: humanize(column) })))]);
  const body = element("tbody");
  for (const [index, row] of (payload.rows || []).entries()) {
    const cells = displayColumns.map((column, columnIndex) => {
      const cell = element("td", { class: /(?:_ns|_id|quantity|sequence)$/.test(column) ? "source-path" : "" });
      if (columnIndex === 0) {
        const open = element("button", { type: "button", class: "record-open", text: displayValue(row[column], "Open record"), "aria-label": `Open complete record ${offset + index + 1}` });
        open.addEventListener("click", () => {
          const detail = element("dl");
          for (const field of columns) detail.append(element("dt", { text: humanize(field) }), element("dd", { class: "source-path", text: displayValue(row[field], "") }));
          const back = element("button", { type: "button", text: "Back to records" });
          back.addEventListener("click", () => runTable(id, run, payload, name, selected, offset));
          replaceView(...runHeader(id, run, selected), element("h2", { text: `${humanize(name)} record ${offset + index + 1}` }), back, detail);
        });
        cell.append(open);
      } else cell.textContent = displayValue(row[column], "");
      return cell;
    });
    body.append(element("tr", {}, cells));
  }
  table.append(head, body);
  const section = element("section");
  section.append(element("h2", { text: humanize(name) }));
  if (columns.length > displayColumns.length) section.append(element("p", { class: "muted", text: `Showing ${displayColumns.length} of ${columns.length} recorded columns. Open a record for all fields. Exact exports remain available in Files.` }));
  section.append(element("div", { class: "table-wrap" }, [table]));
  const nav = element("nav", { class: "pagination", "aria-label": `${name} pages` });
  const shown = Array.isArray(payload.rows) ? payload.rows.length : 0;
  nav.append(element("p", { class: "muted", text: `${shown} of ${payload.total} records` }));
  const previous = element("button", { type: "button", text: "Previous" });
  previous.disabled = offset <= 0;
  previous.addEventListener("click", () => loadRun(id, selected, "event", Math.max(0, offset - payload.limit)));
  const next = element("button", { type: "button", text: "Next" });
  next.disabled = offset + payload.limit >= payload.total;
  next.addEventListener("click", () => loadRun(id, selected, "event", offset + payload.limit));
  nav.append(previous, next);
  section.append(nav);
  if (Array.isArray(payload.warnings)) for (const warning of payload.warnings) section.append(element("p", { class: "error", text: String(warning) }));
  replaceView(...runHeader(id, run, selected), section);
}

function runSeries(id, run, series, selected, range = {}) {
  replaceView(...runHeader(id, run, selected), seriesChart(series), chartRange(id, selected, series.sampling, range));
}

function runFiles(id, run, filesPayload, fileOffset, filesError) {
  replaceView(...runHeader(id, run, "files"), artifactFiles(id, filesPayload, fileOffset, filesError,
    (nextOffset) => loadRun(id, "files", "event", nextOffset)));
}

async function loadRun(id, view = "overview", sampling = "event", offset = 0, range = {}) {
  const request = beginView();
  setNavigation("research");
  setStatus("Loading single run.");
  try {
    const payload = await api(`/api/v1/runs/${encodeURIComponent(id)}`);
    if (!isCurrentView(request)) return;
    const run = payload.run || payload;
    if (view === "overview" || view === "equity" || view === "sampled_equity") {
      const requestedSampling = view === "sampled_equity" ? "sampled" : sampling;
      const params = new URLSearchParams({ sampling: requestedSampling, max_points: "2000" });
      for (const key of ["start_ns", "end_ns"]) if (range[key]) params.set(key, range[key]);
      let seriesPayload;
      try { seriesPayload = await api(`/api/v1/runs/${encodeURIComponent(id)}/series?${params}`); }
      catch (error) {
        if (!isCurrentView(request)) return;
        if (error.code !== "chart_range_too_wide" && error.code !== "invalid_query") throw error;
        replaceView(...runHeader(id, run, view), element("p", { class: "error", text: error.message }), chartRange(id, view, requestedSampling, range, true));
        setStatus("");
        return;
      }
      if (!isCurrentView(request)) return;
      const series = seriesPayload.series || seriesPayload;
      if (view === "overview") runOverview(id, run, series, range);
      else runSeries(id, run, series, view, range);
    } else if (["orders", "fills", "equity_table", "sampled_equity_table", "positions", "closed_trades", "open_trades", "order_events"].includes(view)) {
      const table = view === "equity_table" ? "equity" : view === "sampled_equity_table" ? "sampled_equity" : view;
      const params = new URLSearchParams({ offset: String(offset), limit: "25" });
      const tablePayload = await api(`/api/v1/runs/${encodeURIComponent(id)}/tables/${encodeURIComponent(table)}?${params}`);
      if (!isCurrentView(request)) return;
      runTable(id, run, tablePayload, table, view, offset);
    } else if (view === "metrics") {
      runMetrics(id, run);
    } else if (view === "provenance") {
      runProvenance(id, run);
    } else if (view === "files") {
      let filesPayload = null;
      let filesError = null;
      try {
        const params = new URLSearchParams({ offset: String(offset), limit: "25" });
        filesPayload = await api(`/api/v1/artifacts/${encodeURIComponent(id)}/files?${params}`);
      } catch (error) { filesError = error; }
      if (!isCurrentView(request)) return;
      runFiles(id, run, filesPayload, offset, filesError);
    }
    if (isCurrentView(request)) setStatus("");
  } catch (error) {
    if (isCurrentView(request)) await loadArtifact(id, 0, true, "overview", error.message);
  }
}

async function revealHoldout(id, confirmation, button) {
  const request = activeView;
  button.disabled = true;
  setStatus("Recording holdout disclosure.");
  try {
    await api(`/api/v1/artifacts/${encodeURIComponent(id)}/reveal-holdout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ schema_version: 1, confirmation }),
    });
    if (!isCurrentView(request)) return;
    loadArtifact(id);
  } catch (error) {
    if (isCurrentView(request)) setStatus(`${error.message} Holdout disclosure was not confirmed.`);
  } finally {
    button.disabled = false;
  }
}

async function loadArtifact(id, fileOffset = 0, catalogRecord = false, runView = "overview", viewError = null) {
  const request = beginView();
  setNavigation("research");
  setStatus("Loading artifact.");
  try {
    const artifactPayload = await api(`/api/v1/artifacts/${encodeURIComponent(id)}`);
    if (!isCurrentView(request)) return;
    const artifact = artifactPayload.artifact || artifactPayload;
    if (canOpenRun(artifact) && !catalogRecord) {
      loadRun(id, runView);
      return;
    }
    if (canOpenExperiment(artifact) && !catalogRecord) {
      experimentViews.load(id);
      return;
    }
    let filesPayload = null;
    let filesError = null;
    try {
      const params = new URLSearchParams({ offset: String(fileOffset), limit: "25" });
      filesPayload = await api(`/api/v1/artifacts/${encodeURIComponent(id)}/files?${params}`);
    } catch (error) {
      filesError = error;
    }
    if (!isCurrentView(request)) return;
    renderArtifact(artifact, filesPayload, fileOffset, filesError, viewError);
    setStatus("");
  } catch (error) { if (isCurrentView(request)) { setStatus(""); renderError(error.message); } }
}

const experimentViews = createExperimentViews({
  api, element, replaceView, beginView, isCurrentView, setStatus, setNavigation,
  displayValue, humanize, textLink, loadArtifact,
});

function renderLogin(errorMessage = "") {
  logoutButton.hidden = true;
  setNavigation("");
  const heading = element("h1", { text: "QTE Library" });
  const lede = element("p", { class: "lede", text: "Enter the one-time code shown in the local terminal." });
  const form = element("form", { class: "login-form" });
  const label = element("label", { class: "field-label" }, [element("span", { text: "One-time code" })]);
  const code = element("input", { name: "code", type: "password", required: "", autocomplete: "one-time-code", maxlength: "512" });
  label.append(code);
  const submit = element("button", { type: "submit", text: "Continue" });
  form.append(label, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    setStatus("Signing in.");
    try {
      const payload = await api("/api/v1/session", { method: "POST", includeToken: false, redirectOnUnauthorized: false, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schema_version: 1, code: code.value }) });
      state.token = payload.request_token;
      state.session = payload;
      code.value = "";
      window.location.assign("/library");
    } catch (error) { setStatus(""); renderLogin(error.message); }
    finally { submit.disabled = false; }
  });
  const nodes = [heading, lede];
  if (errorMessage) nodes.push(element("p", { class: "error", text: errorMessage }));
  nodes.push(form);
  replaceView(...nodes);
}

async function bootstrap() {
  if (window.location.pathname === "/login") { renderLogin(); return; }
  try {
    state.session = await api("/api/v1/session", { includeToken: false });
    state.token = state.session.request_token;
    logoutButton.hidden = false;
    route();
  } catch (error) {
    if (!window.location.pathname.startsWith("/login")) window.location.assign("/login");
  }
}

function route() {
  const path = window.location.pathname;
  if (path === "/library" || path === "/") loadLibrary();
  else if (path.startsWith("/library/")) {
    const id = decodeRouteSegment(path, "/library/");
    if (id !== null) loadDocument(id);
  }
  else if (path === "/research") loadResearch();
  else if (path.startsWith("/research/")) {
    const id = decodeRouteSegment(path, "/research/");
    const query = new URLSearchParams(window.location.search);
    const requestedLabView = query.get("lab_view");
    if (id !== null && experimentViews.isView(requestedLabView)) {
      experimentViews.load(id, { view: requestedLabView, timeframe: query.get("timeframe") || "", windowName: query.get("window") || "" });
      return;
    }
    const requestedView = new URLSearchParams(window.location.search).get("view");
    const view = runViews.has(requestedView) ? requestedView : "overview";
    if (id !== null) loadArtifact(id, 0, view === "catalog", view);
  }
  else if (path === "/data") loadUnavailable("data", "Data");
  else if (path === "/system") loadUnavailable("system", "System");
  else { beginView(); renderError("This page is unavailable."); }
}

function decodeRouteSegment(path, prefix) {
  try {
    return decodeURIComponent(path.slice(prefix.length));
  } catch {
    beginView();
    renderError("This page address is malformed.");
    return null;
  }
}

logoutButton.addEventListener("click", async () => {
  logoutButton.disabled = true;
  try {
    try {
      await api("/api/v1/session", { method: "DELETE", redirectOnUnauthorized: false });
    } catch (error) {
      if (error.status !== 401) {
        setStatus("Log out failed. Your current session may still be active; try again.");
        return;
      }
      // A 401 confirms the server no longer accepts this session, so it is safe to
      // discard the stale client state and return to the login view.
    }
    state.token = null;
    state.session = null;
    window.location.assign("/login");
  } finally {
    logoutButton.disabled = false;
  }
});

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[href]");
  if (!link || link.origin !== window.location.origin || link.target || link.hasAttribute("download") || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const rawHref = link.getAttribute("href");
  // Native fragment navigation is required for the skip link and in-page anchors.
  if (!rawHref || rawHref.startsWith("#")) return;
  const href = new URL(link.href);
  if (!["/library", "/research", "/data", "/system"].some((prefix) => href.pathname === prefix || href.pathname.startsWith(`${prefix}/`))) return;
  event.preventDefault();
  window.history.pushState({}, "", href.pathname + href.search + href.hash);
  route();
});
window.addEventListener("popstate", route);
bootstrap();
