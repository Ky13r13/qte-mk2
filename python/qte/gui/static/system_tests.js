/* Explicit controls and sparse, protected evidence for supervised test jobs. */
export function createSystemTests(ui) {
  const LIST_LIMIT = 10;
  const CASE_LIMIT = 25;
  let epoch = 0;

  const word = (item) => ui.humanize ? ui.humanize(item || "unknown") : String(item || "unknown").replaceAll("_", " ");
  const value = (item, fallback = "Not recorded") => ui.displayValue ? ui.displayValue(item, fallback) :
    item === null || item === undefined || item === "" ? fallback : String(item);
  const active = (ticket, request) => ticket === epoch && ui.isCurrentView(request);

  function dispose() { epoch += 1; }

  function action(label, handler) {
    const button = ui.element("button", { type: "button", text: label });
    button.addEventListener("click", handler);
    return button;
  }

  function freeze(item) {
    if (item && typeof item === "object" && !Object.isFrozen(item)) {
      Object.freeze(item);
      for (const child of Object.values(item)) freeze(child);
    }
    return item;
  }

  function uuid() {
    if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (item) => item.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }

  async function load() {
    const request = ui.beginView();
    // beginView may dispose this module. Claim the new view only afterward.
    const ticket = ++epoch;
    ui.setNavigation("system");
    ui.setStatus("Loading test jobs.");

    let root = null;
    let listArea = null;
    let detailArea = null;
    let launchArea = null;
    let notice = null;
    let listOffset = 0;
    let listRevision = 0;
    let selectedId = null;
    let selectedPayload = null;
    let detailRevision = 0;
    const detailMutations = new Set();
    let listLoading = false;
    let submitting = false;
    let uncertain = false;
    let intent = null;
    let launchRevision = 0;

    function guarded() { return active(ticket, request); }

    function setNotice(message, error = false) {
      if (!notice) return;
      notice.textContent = message || "";
      notice.className = error ? "error" : "muted";
      notice.hidden = !message;
    }

    function fields(rows, className = "job-meta") {
      const list = ui.element("dl", { class: className });
      for (const [label, item] of rows) {
        list.append(ui.element("dt", { text: label }), ui.element("dd", { text: value(item) }));
      }
      return list;
    }

    function runnerLabel(runner) { return runner === "pytest" ? "Python (pytest)" : runner === "ctest" ? "C++ (CTest)" : word(runner); }

    function renderLaunch() {
      if (!launchArea) return;
      const revision = ++launchRevision;
      launchArea.replaceChildren();
      const pytest = action("Run Python tests", () => submit("pytest", false, revision));
      const ctest = action("Run C++ tests", () => submit("ctest", false, revision));
      pytest.disabled = submitting || uncertain;
      ctest.disabled = submitting || uncertain;
      launchArea.append(pytest, ctest);
      if (uncertain && intent) {
        const retry = action("Retry same request", () => submit(intent.runner, true, revision));
        retry.disabled = submitting;
        launchArea.append(retry);
      }
    }

    function renderList(payload) {
      if (!listArea) return;
      const revision = ++listRevision;
      listArea.replaceChildren();
      const items = Array.isArray(payload?.items) ? payload.items : [];
      if (!items.length) {
        listArea.append(ui.element("p", { class: "muted", text: "No supervised test jobs have been recorded." }));
        return;
      }
      const body = ui.element("tbody");
      for (const job of items) {
        const id = typeof job?.id === "string" ? job.id : "";
        const open = action(id || "Unavailable", () => {
          if (id && guarded() && revision === listRevision) select(id, 0);
        });
        open.disabled = !id;
        body.append(ui.element("tr", {}, [
          ui.element("td", {}, [open]),
          ui.element("td", { text: word(job?.kind) }),
          ui.element("td", { text: word(job?.state) }),
          ui.element("td", { text: value(job?.created_ns) }),
        ]));
      }
      const table = ui.element("table");
      table.append(
        ui.element("caption", { class: "visually-hidden", text: "Recent supervised test jobs" }),
        ui.element("thead", {}, [ui.element("tr", {}, ["Job", "Kind", "State", "Created"].map((label) => ui.element("th", { scope: "col", text: label })))]),
        body,
      );
      listArea.append(ui.element("div", { class: "table-wrap" }, [table]));
      if (Number.isInteger(payload?.total)) {
        const offset = Number.isInteger(payload?.offset) ? payload.offset : 0;
        listArea.append(ui.element("p", { class: "muted", text: `Showing ${items.length ? offset + 1 : 0}–${offset + items.length} of ${payload.total} test jobs.` }));
        const limit = Number.isInteger(payload?.limit) ? payload.limit : LIST_LIMIT;
        const controls = ui.element("div", { class: "pagination" });
        const previous = action("Previous test jobs", () => {
          if (guarded() && revision === listRevision) refreshList(true, Math.max(0, offset - limit));
        });
        const next = action("Next test jobs", () => {
          if (guarded() && revision === listRevision) refreshList(true, offset + limit);
        });
        previous.disabled = offset <= 0;
        next.disabled = offset + limit >= payload.total;
        controls.append(previous, next);
        listArea.append(controls);
      }
    }

    function appendObject(target, heading, item) {
      target.append(ui.element("h3", { text: heading }));
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        target.append(ui.element("p", { text: value(item) }));
        return;
      }
      const rows = [];
      for (const [name, child] of Object.entries(item)) {
        let shown;
        if (Array.isArray(child)) shown = child.map((part) => value(part, "")).filter(Boolean).join("; ") || "Not recorded";
        else if (child && typeof child === "object") shown = Object.entries(child).map(([key, part]) => `${word(key)}: ${value(part)}`).join("; ");
        else shown = value(child);
        rows.push([word(name), shown]);
      }
      target.append(fields(rows));
    }

    function renderCases(target, evidence, id, revision) {
      target.append(ui.element("h3", { text: "Test cases" }));
      const cases = Array.isArray(evidence?.cases) ? evidence.cases : [];
      if (!cases.length) {
        target.append(ui.element("p", { class: "muted", text: "No case rows were disclosed on this page." }));
      } else {
        const list = ui.element("ol", { start: String((Number.isInteger(evidence.offset) ? evidence.offset : 0) + 1) });
        for (const item of cases) {
          const row = ui.element("li");
          row.append(ui.element("p", { text: `${value(item?.name)} — ${word(item?.outcome)}` }));
          row.append(fields([["Class", item?.classname], ["Duration", item?.duration], ["Ordinal", item?.ordinal]]));
          const issues = Array.isArray(item?.issues) ? item.issues : [];
          for (const issue of issues) {
            row.append(ui.element("p", { text: `${word(issue?.kind)}: ${value(issue?.message)} — ${value(issue?.text)}` }));
          }
          list.append(row);
        }
        target.append(list);
      }
      const offset = Number.isInteger(evidence?.offset) ? evidence.offset : 0;
      const limit = Number.isInteger(evidence?.limit) ? evidence.limit : CASE_LIMIT;
      const total = Number.isInteger(evidence?.total) ? evidence.total : cases.length;
      if (total > cases.length || offset > 0) {
        const controls = ui.element("div", { class: "pagination" });
        const previous = action("Previous cases", () => {
          if (guarded() && selectedId === id && detailRevision === revision) select(id, Math.max(0, offset - limit));
        });
        const next = action("Next cases", () => {
          if (guarded() && selectedId === id && detailRevision === revision) select(id, offset + limit);
        });
        previous.disabled = detailMutations.has(id) || offset <= 0;
        next.disabled = detailMutations.has(id) || offset + limit >= total;
        controls.append(previous, next);
        target.append(controls);
      }
    }

    function renderDetail(payload) {
      if (!detailArea) return;
      selectedPayload = payload;
      const job = payload?.job;
      const evidence = payload?.evidence;
      const revision = detailRevision;
      const mutating = detailMutations.has(job?.id);
      detailArea.replaceChildren();
      if (!job || typeof job !== "object") {
        detailArea.append(ui.element("p", { class: "error", text: "The selected test job response was incomplete." }));
        return;
      }
      detailArea.append(ui.element("h2", { text: `Test job ${value(job.id)}` }));
      detailArea.append(ui.element("p", { class: "lede", text: `State: ${word(job.state)}.` }));
      detailArea.append(ui.element("details", {}, [ui.element("summary", { text: "Job details" }), fields([
        ["Kind", word(job.kind)], ["Created", job.created_ns],
        ["Started", job.start_ns], ["Finished", job.end_ns], ["Timeout seconds", job.timeout_seconds],
      ])]));

      const actions = ui.element("div", { class: "job-actions" });
      if (job.state === "queued" || job.state === "running") {
        const cancelButton = action("Cancel test job", () => cancelSelected(job.id, revision));
        cancelButton.disabled = mutating;
        actions.append(cancelButton);
      }
      const refresh = action("Refresh selected test", () => {
        if (guarded() && selectedId === job.id && detailRevision === revision) {
          select(job.id, Number.isInteger(evidence?.offset) ? evidence.offset : 0);
        }
      });
      refresh.disabled = mutating;
      actions.append(refresh);
      detailArea.append(actions);

      if (evidence?.state === "protected") {
        detailArea.append(ui.element("p", { class: "error", text: "Test evidence is protected because test output can contain sensitive local text. Revealing it is recorded as a disclosure outside protocol." }));
        const reveal = action("Reveal test evidence", () => revealSelected(job.id, revision));
        reveal.disabled = mutating;
        detailArea.append(reveal);
        return;
      }
      if (evidence?.state !== "verified") {
        detailArea.append(ui.element("p", { class: "muted", text: "Verified test evidence is unavailable for this job." }));
        return;
      }

      const counts = evidence.counts && typeof evidence.counts === "object" ? evidence.counts : {};
      detailArea.append(ui.element("p", { class: "lede", text:
        `Evidence outcome: ${word(evidence.outcome)}. Tests ${value(counts.tests)}; passed ${value(counts.passed)}; failed ${value(counts.failed)}; errors ${value(counts.errors)}; skipped ${value(counts.skipped)}; xfailed ${value(counts.xfailed)}.` }));
      detailArea.append(ui.element("p", { class: "muted", text:
        `Disclosure: ${word(evidence.disclosure)}. Source-to-binary correspondence: ${word(evidence.source_to_binary)}; this evidence does not establish that binaries were built from the recorded source.` }));
      detailArea.append(ui.element("p", { class: "muted", text: "Identity comparisons cover explicitly listed files only, not all dependencies." }));
      if (evidence.runner === "ctest") {
        detailArea.append(ui.element("p", { class: "muted", text: "CTest runs existing discovered test binaries; it does not automatically compile them." }));
      }
      if (evidence.details_truncated === true) {
        detailArea.append(ui.element("p", { class: "error", text: "Evidence details are truncated; the displayed cases and issue text are not the complete record." }));
      }
      if (evidence.checked_ns !== undefined) {
        detailArea.append(ui.element("p", { class: "muted", text: `Identity observed at: ${value(evidence.checked_ns)}.` }));
      }
      detailArea.append(ui.element("p", { class: "muted", text: "Current identity is observed only on request; it is not a continuous freshness guarantee." }));
      const check = action("Observe current identity now", () => checkCurrent(job.id, revision));
      check.disabled = mutating;
      detailArea.append(check);

      const details = ui.element("details");
      details.append(ui.element("summary", { text: "Evidence details" }));
      details.append(fields([
        ["Runner", runnerLabel(evidence.runner)], ["Source to binary", evidence.source_to_binary],
        ["Log truncated", evidence.log_truncated], ["Termination", evidence.termination],
        ["Exit code", evidence.exit_code], ["Disclosure", evidence.disclosure],
      ]));
      appendObject(details, "During-run identity stability", evidence.identity_stability);
      appendObject(details, "Current identity match", evidence.current_match);
      if (evidence.versions_match !== undefined) appendObject(details, "Current tool version match", evidence.versions_match);
      appendObject(details, "Tool and platform versions", evidence.versions);
      renderCases(details, evidence, job.id, revision);
      detailArea.append(details);
    }

    async function refreshList(report = true, offset = listOffset) {
      if (!guarded() || listLoading) return;
      listLoading = true;
      if (report) ui.setStatus("Refreshing test jobs.");
      try {
        const payload = await ui.api(`/api/v1/system/tests?offset=${offset}&limit=${LIST_LIMIT}`);
        if (!guarded()) return;
        listOffset = Number.isInteger(payload?.offset) ? payload.offset : offset;
        renderList(payload);
        setNotice("");
        ui.setStatus("");
      } catch (problem) {
        if (guarded()) {
          setNotice(`${problem.message} No test job state was changed.`, true);
          ui.setStatus("");
        }
      } finally { listLoading = false; }
    }

    async function select(id, offset = 0) {
      if (!guarded() || typeof id !== "string" || !id) return;
      selectedId = id;
      const revision = ++detailRevision;
      selectedPayload = null;
      detailArea.replaceChildren(ui.element("p", { class: "muted", text: "Loading selected test job." }));
      ui.setStatus("Loading selected test job.");
      try {
        const payload = await ui.api(`/api/v1/system/tests/${encodeURIComponent(id)}?offset=${offset}&limit=${CASE_LIMIT}`);
        if (!guarded() || revision !== detailRevision || selectedId !== id) return;
        renderDetail(payload);
        ui.setStatus("");
      } catch (problem) {
        if (guarded() && revision === detailRevision && selectedId === id) {
          detailArea.replaceChildren(ui.element("p", { class: "error", text: `${problem.message} The selected test job was not changed.` }));
          ui.setStatus("");
        }
      }
    }

    async function submit(runner, retry, revision) {
      if (!guarded() || submitting || revision !== launchRevision || !retry && uncertain) return;
      if (retry) {
        if (!uncertain || !intent || intent.runner !== runner) return;
      } else {
        intent = freeze({ schema_version: 1, runner, timeout_seconds: 300, idempotency_key: uuid() });
        uncertain = false;
      }
      const body = intent;
      const retryWasUncertain = retry && uncertain;
      submitting = true;
      renderLaunch();
      setNotice("");
      ui.setStatus(`Submitting ${runnerLabel(runner)} test job.`);
      try {
        const result = await ui.api("/api/v1/system/tests", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        if (!guarded()) return;
        const job = result?.job;
        if (!job || typeof job.id !== "string" || !job.id) throw new Error("The test job response did not identify a job.");
        uncertain = false;
        intent = null;
        await refreshList(false, 0);
        if (!guarded()) return;
        setNotice("Test job accepted. Evidence remains protected until explicitly revealed.");
        await select(job.id, 0);
      } catch (problem) {
        if (!guarded()) return;
        const definitive = Number.isInteger(problem?.status) && problem.status >= 400 && problem.status < 500;
        if (definitive && !retryWasUncertain) {
          intent = null;
          uncertain = false;
          setNotice(`${problem.message} The test job was not accepted.`, true);
        } else {
          uncertain = true;
          setNotice("Submission outcome is unknown. Retry sends the exact same request and idempotency key.", true);
        }
        ui.setStatus("");
      } finally {
        submitting = false;
        if (guarded()) renderLaunch();
      }
    }

    async function mutateSelected(id, revision, path, body, statusText) {
      if (!guarded() || detailMutations.has(id) || !selectedPayload || selectedId !== id || detailRevision !== revision) return null;
      detailMutations.add(id);
      renderDetail(selectedPayload);
      ui.setStatus(statusText);
      try {
        const result = await ui.api(path, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        if (!guarded() || revision !== detailRevision || selectedId !== id) return null;
        return result;
      } catch (problem) {
        if (guarded() && revision === detailRevision && selectedId === id) {
          setNotice(`${problem.message} The action was not confirmed; refresh to inspect the current state.`, true);
          ui.setStatus("");
        }
        return null;
      } finally {
        detailMutations.delete(id);
        // Re-enable the current view even after A -> B -> A navigation. Render
        // only its current payload; the older mutation result remains discarded.
        if (guarded() && selectedId === id && selectedPayload) renderDetail(selectedPayload);
      }
    }

    async function cancelSelected(id, revision) {
      const result = await mutateSelected(id, revision, `/api/v1/jobs/${encodeURIComponent(id)}/cancel`, { schema_version: 1 }, "Requesting test cancellation.");
      if (result && guarded() && selectedId === id) await select(id, 0);
    }

    async function revealSelected(id, revision) {
      const result = await mutateSelected(id, revision, `/api/v1/system/tests/${encodeURIComponent(id)}/reveal`,
        { schema_version: 1, confirmation: "reveal outside protocol" }, "Requesting protected evidence disclosure.");
      if (result && guarded() && selectedId === id) await select(id, 0);
    }

    async function checkCurrent(id, revision) {
      const result = await mutateSelected(id, revision, `/api/v1/system/tests/${encodeURIComponent(id)}/check-current`,
        { schema_version: 1 }, "Checking current repository identities.");
      if (!result || !guarded() || selectedId !== id || selectedPayload?.evidence?.state !== "verified") return;
      const evidence = { ...selectedPayload.evidence, current_match: result.current_match };
      if (result.versions_match !== undefined) evidence.versions_match = result.versions_match;
      if (result.checked_ns !== undefined) evidence.checked_ns = result.checked_ns;
      selectedPayload = {
        ...selectedPayload,
        evidence,
      };
      renderDetail(selectedPayload);
      ui.setStatus("");
    }

    root = ui.element("section", { class: "system-tests", "aria-labelledby": "system-tests-heading" });
    launchArea = ui.element("div", { class: "job-actions" });
    notice = ui.element("p", { class: "muted" });
    notice.hidden = true;
    listArea = ui.element("section", { "aria-labelledby": "recent-tests-heading" });
    detailArea = ui.element("section", { "aria-label": "Selected test job" });
    const refresh = action("Refresh test jobs", () => refreshList(true, listOffset));
    root.append(
      ui.element("p", { class: "muted" }, [ui.textLink("/library", "Library"), globalThis.document.createTextNode(" / System")]),
      ui.element("h1", { id: "system-tests-heading", text: "System tests" }),
      ui.element("p", {}, [ui.textLink("/system/benchmarks", "Benchmarks"),
        globalThis.document.createTextNode(" · "), ui.textLink("/system/connections", "Connections")]),
      ui.element("p", { class: "lede", text: "Run the fixed local Python or C++ test command. Opening this page never starts a test." }),
      launchArea, notice,
      ui.element("h2", { id: "recent-tests-heading", text: "Recent test jobs" }), refresh, listArea, detailArea,
    );
    renderLaunch();
    ui.replaceView(root);
    await refreshList(false);
  }

  return { load, dispose };
}
