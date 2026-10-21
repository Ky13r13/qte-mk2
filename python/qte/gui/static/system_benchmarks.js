/* Explicit, bounded projections for synthetic system benchmark jobs. */
export function createSystemBenchmarks(ui) {
  const LIMIT = 10;
  let epoch = 0;
  const word = item => ui.humanize ? ui.humanize(item || "unknown") : String(item || "unknown").replaceAll("_", " ");
  const value = (item, fallback = "Not recorded") => ui.displayValue ? ui.displayValue(item, fallback) : (item === null || item === undefined || item === "" ? fallback : String(item));
  const active = (ticket, request) => ticket === epoch && ui.isCurrentView(request);
  const freeze = item => { if (item && typeof item === "object" && !Object.isFrozen(item)) { Object.freeze(item); for (const child of Object.values(item)) freeze(child); } return item; };
  function uuid() {
    if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }
  function dispose() { epoch += 1; }
  function action(label, handler) { const button = ui.element("button", { type: "button", text: label }); button.addEventListener("click", handler); return button; }

  async function load() {
    const request = ui.beginView(); const ticket = ++epoch;
    ui.setNavigation("system"); ui.setStatus("Loading benchmark jobs.");
    let root, launchArea, listArea, detailArea, notice, barsInput, strategySelect;
    let listOffset = 0, listRevision = 0, detailRevision = 0, selectedId = null, selectedPayload = null;
    let listLoading = false, submitting = false, uncertain = false, intent = null;
    let launchRevision = 0, formBars = "10000", formStrategy = "hold";
    const mutations = new Set();
    const guarded = () => active(ticket, request);
    const setNotice = (message, error = false) => { if (!notice) return; notice.textContent = message || ""; notice.className = error ? "error" : "muted"; notice.hidden = !message; };
    const fields = rows => { const list = ui.element("dl", { class: "job-meta" }); for (const [label, item] of rows) list.append(ui.element("dt", { text: label }), ui.element("dd", { text: value(item) })); return list; };
    function renderLaunch() {
      const revision = ++launchRevision;
      launchArea.replaceChildren();
      barsInput = ui.element("input", { type: "number", min: "1", max: "100000", step: "1", value: formBars, "aria-label": "Bars" });
      strategySelect = ui.element("select", { "aria-label": "Benchmark workload" });
      const hold = ui.element("option", { value: "hold", text: "No-op (no trades)" });
      const sma = ui.element("option", { value: "sma", text: "SMA" });
      strategySelect.append(hold, sma); strategySelect.value = formStrategy;
      const run = action("Run benchmark", () => submit(false, revision)); run.disabled = submitting || uncertain;
      barsInput.disabled = submitting || uncertain; strategySelect.disabled = submitting || uncertain;
      launchArea.append(ui.element("label", { text: "Bars" }, [barsInput]), ui.element("label", { text: "Workload" }, [strategySelect]), run);
      if (uncertain && intent) { const retry = action("Retry same request", () => submit(true, revision)); retry.disabled = submitting; launchArea.append(retry); }
    }
    function renderList(payload) {
      const revision = ++listRevision; listArea.replaceChildren(); const items = Array.isArray(payload?.items) ? payload.items : [];
      if (!items.length) listArea.append(ui.element("p", { class: "muted", text: "No benchmark jobs have been recorded." }));
      else {
        const body = ui.element("tbody");
        for (const job of items) { const id = typeof job?.id === "string" ? job.id : ""; const open = action(id || "Unavailable", () => { if (id && guarded() && revision === listRevision) select(id); }); open.disabled = !id; body.append(ui.element("tr", {}, [ui.element("td", {}, [open]), ui.element("td", { text: word(job?.kind) }), ui.element("td", { text: word(job?.state) }), ui.element("td", { text: value(job?.created_ns) })])); }
        const table = ui.element("table"); table.append(ui.element("caption", { class: "visually-hidden", text: "Recent benchmark jobs" }), ui.element("thead", {}, [ui.element("tr", {}, ["Job", "Kind", "State", "Created"].map(label => ui.element("th", { scope: "col", text: label })))]), body); listArea.append(ui.element("div", { class: "table-wrap" }, [table]));
      }
      if (Number.isInteger(payload?.total)) { const offset = Number.isInteger(payload.offset) ? payload.offset : 0; const limit = Number.isInteger(payload.limit) ? payload.limit : LIMIT; listArea.append(ui.element("p", { class: "muted", text: `Showing ${items.length ? offset + 1 : 0}–${offset + items.length} of ${payload.total} benchmark jobs.` })); const controls = ui.element("div", { class: "pagination" }); const previous = action("Previous benchmark jobs", () => { if (guarded() && revision === listRevision) refreshList(true, Math.max(0, offset - limit)); }); const next = action("Next benchmark jobs", () => { if (guarded() && revision === listRevision) refreshList(true, offset + limit); }); previous.disabled = offset <= 0; next.disabled = offset + limit >= payload.total; controls.append(previous, next); listArea.append(controls); }
    }
    function renderDetail(payload) {
      selectedPayload = payload; detailArea.replaceChildren(); const job = payload?.job, evidence = payload?.evidence, revision = detailRevision;
      if (!job || typeof job !== "object") { detailArea.append(ui.element("p", { class: "error", text: "The selected benchmark response was incomplete." })); return; }
      detailArea.append(ui.element("h2", { text: `Benchmark job ${value(job.id)}` }), ui.element("p", { class: "lede", text: `State: ${word(job.state)}.` }));
      detailArea.append(ui.element("details", {}, [ui.element("summary", { text: "Job details" }), fields([["Kind", word(job.kind)], ["Created", job.created_ns], ["Started", job.start_ns], ["Finished", job.end_ns], ["Timeout seconds", job.timeout_seconds]])]));
      const actions = ui.element("div", { class: "job-actions" }), mutating = mutations.has(job.id);
      if (job.state === "queued" || job.state === "running") { const cancel = action("Cancel benchmark job", () => cancelSelected(job.id, revision)); cancel.disabled = mutating; actions.append(cancel); }
      const refresh = action("Refresh selected benchmark", () => { if (guarded() && selectedId === job.id && detailRevision === revision) select(job.id); }); refresh.disabled = mutating; actions.append(refresh); detailArea.append(actions);
      if (evidence?.state !== "verified") { detailArea.append(ui.element("p", { class: "muted", text: "Verified synthetic benchmark evidence is unavailable for this job." })); return; }
      const measurement = evidence.measurement && typeof evidence.measurement === "object" ? evidence.measurement : {};
      detailArea.append(ui.element("p", { class: "lede", text: `Synthetic single-sample timing, not strategy performance. Workload: ${value(measurement.workload_label)}; bars: ${value(measurement.bars)}; strategy: ${measurement.strategy === "hold" ? "No-op (no trades)" : value(measurement.strategy)}.` }));
      detailArea.append(fields([["Load seconds", measurement.load_seconds], ["Replay seconds", measurement.replay_seconds], ["Bars per second", measurement.bars_per_second]]));
      detailArea.append(ui.element("p", { class: "muted", text: "The no-op workload makes no trades; it is not buy-and-hold. Existing binaries are measured without rebuilding them. Source-to-binary correspondence is unknown." }));
      const details = ui.element("details"); details.append(ui.element("summary", { text: "Additional benchmark metadata" }), fields([["Fills", measurement.fills], ["Final equity", measurement.final_equity], ["Started", evidence.started_ns], ["Finished", evidence.finished_ns], ["Source to binary", evidence.source_to_binary]]));
      const identity = evidence.identity_stability; if (identity && typeof identity === "object") details.append(ui.element("h3", { text: "Identity stability" }), fields(Object.entries(identity).map(([name, item]) => [word(name), item])));
      const machine = measurement.machine; if (machine && typeof machine === "object") details.append(ui.element("h3", { text: "Machine" }), fields(Object.entries(machine).map(([name, item]) => [word(name), item]))); detailArea.append(details);
    }
    async function refreshList(report = true, offset = listOffset) { if (!guarded() || listLoading) return; listLoading = true; if (report) ui.setStatus("Refreshing benchmark jobs."); try { const payload = await ui.api(`/api/v1/system/benchmarks?offset=${offset}&limit=${LIMIT}`); if (!guarded()) return; listOffset = Number.isInteger(payload?.offset) ? payload.offset : offset; renderList(payload); setNotice(""); ui.setStatus(""); } catch (error) { if (guarded()) { setNotice(`${error.message} No benchmark job state was changed.`, true); ui.setStatus(""); } } finally { listLoading = false; } }
    async function select(id) { if (!guarded() || !id) return; selectedId = id; const revision = ++detailRevision; selectedPayload = null; detailArea.replaceChildren(ui.element("p", { class: "muted", text: "Loading selected benchmark." })); ui.setStatus("Loading selected benchmark."); try { const payload = await ui.api(`/api/v1/system/benchmarks/${encodeURIComponent(id)}`); if (!guarded() || revision !== detailRevision || selectedId !== id) return; renderDetail(payload); ui.setStatus(""); } catch (error) { if (guarded() && revision === detailRevision && selectedId === id) { detailArea.replaceChildren(ui.element("p", { class: "error", text: `${error.message} The selected benchmark was not changed.` })); ui.setStatus(""); } } }
    async function submit(retry = false, revision) { if (!guarded() || revision !== launchRevision || submitting || (!retry && uncertain)) return; if (retry) { if (!uncertain || !intent) return; } else { const bars = Number(barsInput?.value); const strategy = strategySelect?.value; if (!Number.isInteger(bars) || bars < 1 || bars > 100000 || (strategy !== "hold" && strategy !== "sma")) { setNotice("Bars must be an integer from 1 through 100000 and the workload must be selected.", true); return; } formBars = String(bars); formStrategy = strategy; intent = freeze({ schema_version: 1, bars, strategy, timeout_seconds: 300, idempotency_key: uuid() }); uncertain = false; }
      const body = intent, retryWasUncertain = retry && uncertain; submitting = true; renderLaunch(); setNotice(""); ui.setStatus("Submitting benchmark job."); try { const result = await ui.api("/api/v1/system/benchmarks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!guarded()) return; const job = result?.job; if (!job || typeof job.id !== "string" || !job.id) throw new Error("The benchmark response did not identify a job."); uncertain = false; intent = null; await refreshList(false, 0); if (!guarded()) return; setNotice("Benchmark job accepted. Results are synthetic timing evidence."); await select(job.id); } catch (error) { if (!guarded()) return; const definitive = Number.isInteger(error?.status) && error.status >= 400 && error.status < 500; if (definitive && !retryWasUncertain) { intent = null; uncertain = false; setNotice(`${error.message} The benchmark job was not accepted.`, true); } else { uncertain = true; setNotice("Submission outcome is unknown. Retry sends the exact same request and idempotency key.", true); } ui.setStatus(""); } finally { submitting = false; if (guarded()) renderLaunch(); } }
    async function cancelSelected(id, revision) { if (!guarded() || mutations.has(id) || selectedId !== id || detailRevision !== revision || !selectedPayload) return; mutations.add(id); renderDetail(selectedPayload); ui.setStatus("Requesting benchmark cancellation."); try { await ui.api(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schema_version: 1 }) }); if (guarded() && selectedId === id && detailRevision === revision) await select(id); } catch (error) { if (guarded() && selectedId === id && detailRevision === revision) setNotice(`${error.message} The action was not confirmed; refresh to inspect the current state.`, true); } finally { mutations.delete(id); if (guarded() && selectedId === id && selectedPayload) renderDetail(selectedPayload); } }
    root = ui.element("section", { class: "system-benchmarks", "aria-labelledby": "system-benchmarks-heading" }); launchArea = ui.element("div", { class: "job-actions" }); notice = ui.element("p", { class: "muted" }); notice.hidden = true; listArea = ui.element("section", { "aria-labelledby": "recent-benchmarks-heading" }); detailArea = ui.element("section", { "aria-label": "Selected benchmark job" });
    root.append(ui.element("p", { class: "muted" }, [ui.textLink("/system", "System tests")]), ui.element("h1", { id: "system-benchmarks-heading", text: "System benchmarks" }), ui.element("p", { class: "lede", text: "Run a synthetic, single-sample timing workload. Opening this page never runs a benchmark." }), launchArea, notice, ui.element("h2", { id: "recent-benchmarks-heading", text: "Recent benchmark jobs" }), action("Refresh benchmark jobs", () => refreshList(true, listOffset)), listArea, detailArea); renderLaunch(); ui.replaceView(root); await refreshList(false);
  }
  return { load, dispose };
}
