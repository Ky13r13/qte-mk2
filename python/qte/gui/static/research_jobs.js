/* Passive, bounded client views for already-accepted local research jobs. */
export function createResearchJobs(ui) {
  let epoch = 0;
  let timer = null;
  let visibleListener = null;
  let burst = 0;

  const active = (ticket, request) => ticket === epoch && ui.isCurrentView(request);
  const word = (value) => ui.humanize ? ui.humanize(value || "unknown") : String(value || "unknown").replaceAll("_", " ");
  const value = (item) => ui.displayValue ? ui.displayValue(item) : String(item ?? "Unavailable");

  function clearTimer() {
    if (timer !== null) { globalThis.clearTimeout(timer); timer = null; }
  }

  function stopPolling() {
    clearTimer();
    if (visibleListener) {
      globalThis.document.removeEventListener("visibilitychange", visibleListener);
      visibleListener = null;
    }
  }

  function dispose() {
    epoch += 1;
    stopPolling();
  }

  function jobRows(items) {
    const body = ui.element("tbody");
    for (const job of items) {
      const id = typeof job?.id === "string" ? job.id : "";
      body.append(ui.element("tr", {}, [
        ui.element("td", {}, [id ? ui.textLink(`/research/jobs/${encodeURIComponent(id)}`, id) : ui.element("span", { text: "Unavailable" })]),
        ui.element("td", { text: word(job?.kind) }),
        ui.element("td", { class: "job-state", text: word(job?.state) }),
        ui.element("td", { text: value(job?.created_ns) }),
      ]));
    }
    return body;
  }

  function recent(payload, error = null, onPage = null) {
    const section = ui.element("section", { class: "research-jobs", "aria-labelledby": "research-jobs-heading" });
    section.append(ui.element("h2", { id: "research-jobs-heading", text: "Research jobs" }));
    if (error) {
      section.append(ui.element("p", { class: "error", text: "Jobs could not be loaded. No job state was changed." }));
      return section;
    }
    const items = Array.isArray(payload?.items) ? payload.items : [];
    if (!items.length) {
      section.append(ui.element("p", { class: "muted", text: "No local research jobs have been accepted." }));
      return section;
    }
    const table = ui.element("table");
    table.append(
      ui.element("caption", { class: "visually-hidden", text: "Recent research jobs" }),
      ui.element("thead", {}, [ui.element("tr", {}, ["Job", "Kind", "State", "Created"].map((label) => ui.element("th", { scope: "col", text: label })))]),
      jobRows(items),
    );
    section.append(ui.element("div", { class: "table-wrap job-list" }, [table]));
    if (Number.isInteger(payload?.total) && payload.total > items.length) {
      const start = Number.isInteger(payload?.offset) ? payload.offset + 1 : 1;
      section.append(ui.element("p", { class: "muted", text: `Showing jobs ${start}–${start + items.length - 1} of ${payload.total}.` }));
    }
    if (typeof onPage === "function" && Number.isInteger(payload?.offset) && Number.isInteger(payload?.limit)) {
      const controls = ui.element("nav", { class: "pagination", "aria-label": "Research job pages" });
      const previous = actionButton("Previous jobs", () => onPage(Math.max(0, payload.offset - payload.limit)));
      const next = actionButton("Next jobs", () => onPage(payload.offset + payload.limit));
      previous.disabled = payload.offset <= 0;
      next.disabled = !Number.isInteger(payload.total) || payload.offset + payload.limit >= payload.total;
      controls.append(previous, next);
      section.append(controls);
    }
    return section;
  }

  function actionButton(label, handler) {
    const button = ui.element("button", { type: "button", text: label });
    button.addEventListener("click", handler);
    return button;
  }

  async function load(id) {
    const request = ui.beginView();
    // The shell's beginView disposes this module. Increment only after that
    // callback so this newly requested detail remains current.
    stopPolling();
    const ticket = ++epoch;
    ui.setNavigation("research");
    ui.setStatus("Loading job.");
    let root = null;
    let summary = null;
    let metadata = null;
    let details = null;
    let actions = null;
    let logNode = null;
    let inFlight = false;
    let mutation = false;
    let lastJob = null;
    let lastLog = null;
    let pollControls = null;

    const fields = (target, rows) => target.replaceChildren(...rows.flatMap(([label, item]) => [
      ui.element("dt", { text: label }), ui.element("dd", { text: value(item) }),
    ]));
    const render = (job, log) => {
      lastJob = job; lastLog = log;
      if (!root || !summary) {
        root = ui.element("section", { class: "job-detail", "aria-labelledby": "job-heading" });
        const jobId = typeof job?.id === "string" ? job.id : "Unavailable";
        summary = ui.element("p", { class: "lede" });
        metadata = ui.element("dl", { class: "job-meta" });
        details = ui.element("dl", { class: "job-meta" });
        actions = ui.element("div", { class: "job-actions" });
        logNode = ui.element("pre", { class: "job-log" });
        root.append(ui.element("p", { class: "muted" }, [ui.textLink("/research", "Research"), globalThis.document.createTextNode(" / Job")]),
          ui.element("h1", { id: "job-heading", text: `Job ${jobId}` }), summary, metadata,
          ui.element("details", {}, [ui.element("summary", { text: "Job details" }), details]), actions,
          ui.element("h2", { text: "Sanitized job log" }), logNode);
        ui.replaceView(root);
      }
      summary.textContent = `State: ${word(job?.state)}.`;
      fields(metadata, [["Kind", word(job?.kind)], ["Reason", job?.reason || "None"],
        ["Created", job?.created_ns ?? "Unavailable"], ["Started", job?.start_ns ?? "Not started"],
        ["Finished", job?.end_ns ?? "Not finished"], ["Exit code", job?.exit_code ?? "Unavailable"]]);
      fields(details, [["Phase", job?.phase || "Unavailable"], ["Timeout seconds", job?.timeout_seconds ?? "Unavailable"],
        ["Output path", job?.output_path || "Unavailable"], ["Source identity", job?.source_identity || "Unavailable"]]);
      const focused = globalThis.document.activeElement?.dataset?.jobAction;
      actions.replaceChildren();
      const addAction = (key, label, handler) => {
        const button = actionButton(label, handler); button.dataset.jobAction = key; button.disabled = mutation; actions.append(button);
      };
      if (job?.state === "queued" || job?.state === "running") addAction("cancel", "Cancel job", cancel);
      if (job?.state === "succeeded" && typeof job?.output_path === "string" && job.output_path) addAction("register", "Register output", register);
      addAction("refresh", "Refresh", () => refresh(true));
      if (focused) actions.querySelector(`[data-job-action="${focused}"]`)?.focus();
      logNode.textContent = typeof log?.text === "string" ? log.text.slice(0, 65536) : "Log unavailable.";
    };
    const releaseMutation = () => {
      mutation = false;
      if (active(ticket, request) && lastJob) {
        render(lastJob, lastLog);
        if (lastJob.state === "queued" || lastJob.state === "running") schedule();
      }
    };
    const cancel = async () => {
      if (!active(ticket, request) || mutation || !lastJob) return;
      mutation = true; clearTimer(); render(lastJob, lastLog); ui.setStatus("Requesting cancellation.");
      try {
        await ui.api(`/api/v1/jobs/${encodeURIComponent(lastJob.id)}/cancel`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schema_version: 1 }),
        });
        if (active(ticket, request)) { ui.setStatus("Cancellation request sent. Confirming the recorded state."); await refresh(true, true); }
      } catch (error) {
        if (active(ticket, request)) ui.setStatus(`${error.message} Cancellation was not confirmed; refresh the recorded state.`);
      } finally { releaseMutation(); }
    };
    const register = async () => {
      if (!active(ticket, request) || mutation || !lastJob) return;
      mutation = true; clearTimer(); render(lastJob, lastLog); ui.setStatus("Registering completed output.");
      try {
        const result = await ui.api("/api/v1/catalog/register", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ schema_version: 1, path: lastJob.output_path }) });
        if (!active(ticket, request)) return;
        const artifact = result?.artifact || result;
        if (typeof artifact?.id !== "string" || !artifact.id) throw new Error("Registration was not confirmed.");
        if (artifact.kind === "alpaca_download_v1") ui.navigate(`/data/downloads/${encodeURIComponent(artifact.id)}`);
        else if (artifact.kind === "dataset_validation_v1") ui.navigate(`/data/${encodeURIComponent(artifact.id)}`);
        else ui.navigate(`/research/${encodeURIComponent(artifact.id)}`);
      } catch (error) {
        if (active(ticket, request)) ui.setStatus(`${error.message} Registration was not confirmed.`);
      } finally { releaseMutation(); }
    };
    const refresh = async (manual = false, allowMutation = false) => {
      if (!active(ticket, request) || inFlight || (mutation && !allowMutation)) return;
      inFlight = true;
      let keepPolling = false;
      try {
        const detail = await ui.api(`/api/v1/jobs/${encodeURIComponent(id)}`);
        if (!active(ticket, request)) return;
        const job = detail?.job;
        if (!job || typeof job !== "object") throw new Error("The job response was incomplete.");
        const log = await ui.api(`/api/v1/jobs/${encodeURIComponent(id)}/log?offset=0&limit=65536`);
        if (!active(ticket, request)) return;
        render(job, log);
        ui.setStatus("");
        if (job?.state === "queued" || job?.state === "running") {
          if (manual) burst = 0;
          keepPolling = true;
        } else {
          pollControls?.remove(); pollControls = null;
          stopPolling();
        }
      } catch (error) {
        if (active(ticket, request)) {
          ui.setStatus(`${error.message} The recorded job state was not changed.`);
          if (!root) {
            root = ui.element("section", { class: "job-detail" }, [ui.element("h1", { text: "Job unavailable" }),
              ui.element("p", { class: "error", text: "The job details could not be loaded." }),
              actionButton("Refresh", () => { root = null; refresh(true); })]);
            ui.replaceView(root);
          }
        }
      } finally {
        inFlight = false;
        if (keepPolling && active(ticket, request)) schedule();
      }
    };
    const schedule = () => {
      if (!active(ticket, request) || globalThis.document.hidden || timer !== null || inFlight || mutation) return;
      if (burst >= 60) {
        if (root && !pollControls) {
          pollControls = ui.element("div", { class: "job-poll-controls" }, [actionButton("Resume live updates", () => {
            burst = 0; pollControls?.remove(); pollControls = null; schedule();
          })]);
          root.append(pollControls);
        }
        return;
      }
      timer = globalThis.setTimeout(() => {
        timer = null;
        if (!active(ticket, request) || globalThis.document.hidden) return;
        burst += 1;
        refresh(false);
      }, 1000);
    };
    visibleListener = () => { if (!globalThis.document.hidden && active(ticket, request)) schedule(); };
    globalThis.document.addEventListener("visibilitychange", visibleListener);
    await refresh(true);
  }

  return { recent, load, dispose };
}
