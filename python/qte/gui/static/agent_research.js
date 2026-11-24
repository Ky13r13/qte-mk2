// Optional five-role research coordination over a fixed synthetic backtest.
import { renderWeeklyReversal } from "./weekly_reversal_report.js";

export function createAgentResearch(ui) {
  const roles = [
    ["premarket_bias", "Premarket bias"],
    ["strategy_a", "Strategy A"],
    ["strategy_b", "Strategy B"],
    ["exposure", "Exposure and regime sizing"],
    ["documentation", "Documentation"],
  ];
  const roleIds = new Set(roles.map(([id]) => id));
  let timer = null;
  let request = 0;
  let runTicket = 0;

  function dispose() {
    if (timer !== null) globalThis.clearTimeout(timer);
    timer = null;
    request += 1;
    runTicket += 1;
  }

  function text(value, fallback = "Unavailable") {
    return typeof value === "string" && value ? value.slice(0, 4000) : fallback;
  }

  function number(value) {
    if (typeof value === "string" && /^-?\d+$/.test(value)) return value;
    return typeof value === "number" && Number.isFinite(value) ? Number(value.toPrecision(6)).toString() : "—";
  }

  function percent(value) {
    return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—";
  }

  function table(headers, rows, caption) {
    const node = ui.element("table", { class: "agent-research-table" });
    node.append(ui.element("caption", { class: "visually-hidden", text: caption }));
    node.append(ui.element("thead", {}, [ui.element("tr", {}, headers.map((label) => ui.element("th", { scope: "col", text: label })))]));
    node.append(ui.element("tbody", {}, rows.map((row) => ui.element("tr", {}, row.map((value) => ui.element("td", { text: value }))))));
    return node;
  }

  function renderMaster(target, backtest) {
    const master = backtest?.master || {};
    const list = (value) => Array.isArray(value) ? value : [];
    const words = (value) => text(value).replaceAll("_", " ");
    const reasons = (value) => list(value).map(words).join(", ");
    const children = [
      ui.element("h2", { text: "Short-duration options master" }),
      ui.element("p", { class: "muted", text: backtest?.evidence === "synthetic_mechanics_only"
        ? "Synthetic mechanics only. Positive fixture P&L does not establish an options trading edge."
        : "Captured-data replay. Data quality and independent validation determine what can be concluded." }),
      ui.element("p", { text: `Research candidate ${words(master.candidate_id)}. Operational policy ${words(master.operational_candidate_id)}.` }),
      ui.element("p", { text: text(master.selection_basis, "Selection uses design evidence only. Evaluation cannot trigger another revision in this run.") }),
    ];
    const blockers = list(master.blockers).concat(list(backtest?.blockers));
    if (blockers.length) children.push(ui.element("ul", {}, blockers.map(value => ui.element("li", { text: words(value) }))));
    const revisions = list(backtest?.revisions);
    if (revisions.length) children.push(ui.element("h3", { text: "Recorded master revisions" }),
      table(["Candidate", "Proposed by", "Decision", "Reason"], revisions.map(value => [
        words(value?.candidate_id), words(value?.proposed_by),
        typeof value?.accepted === "boolean" ? (value.accepted ? "Accepted for research" : "Rejected") : text(value?.status),
        text(value?.reason),
      ]), "Master strategy revisions from design evidence"));
    const failures = list(backtest?.failures);
    if (failures.length) children.push(ui.element("h3", { text: "What failed" }),
      table(["Candidate", "Stress P&L ($)", "Execution drag ($)", "Worst session ($)", "Failed checks"], failures.map(value => [
        words(value?.candidate_id), number(value?.design_stress?.net_pnl),
        number(value?.design_stress?.closed_execution_drag), number(value?.design_stress?.worst_session),
        reasons(value?.reasons) || "Passed development gates",
      ]), "Recorded candidate failures and counterexamples"));
    const validation = backtest?.validation || {};
    const stats = [];
    for (const scenario of ["base", "stress"]) {
      const value = validation[scenario];
      if (value && typeof value === "object") stats.push([scenario, number(value.net_pnl),
        number(value.closed_trades), number(value.unresolved), number(value.sessions)]);
    }
    if (stats.length) children.push(ui.element("h3", { text: "Frozen candidate evaluation" }),
      table(["Costs", "Net P&L ($)", "Closed trades", "Unresolved", "Sessions"], stats, "Evaluation after candidate selection"));
    const agents = list(backtest?.agents);
    if (agents.length) children.push(ui.element("details", {}, [
      ui.element("summary", { text: "Deterministic research roles" }),
      ...agents.map(value => ui.element("p", { text: `${words(value?.role)}. ${text(value?.finding)}` })),
    ]));
    children.push(ui.element("p", { class: "muted", text: "Model agents can nominate registered rules using development evidence. Fixed gates decide revisions. Validation evaluates the frozen candidate once." }));
    target.replaceChildren(...children);
  }

  function renderReport(target, report) {
    const backtest = report?.backtest;
    if (backtest?.experiment === "options_master") {
      renderMaster(target, backtest);
      appendAgentNotes(target, report);
      return;
    }
    if (backtest?.experiment === "weekly_reversal") {
      target.replaceChildren(...renderWeeklyReversal(ui, backtest));
      appendAgentNotes(target, report);
      return;
    }
    if (!backtest || backtest.evidence !== "synthetic_known_mechanics_only") {
      target.replaceChildren(ui.element("p", { class: "error", text: "The completed run did not include the fixed synthetic evidence report." }));
      return;
    }
    const children = [
      ui.element("h2", { id: "local-backtest", text: "Local fixed backtest" }),
      ui.element("h3", { id: "verified-outcomes", text: "Verified synthetic outcomes" }),
      ui.element("p", { class: "muted", text: "Synthetic quotes and prices. Use these results to check how the rules behave." }),
    ];
    if (typeof report.agent_provenance === "string") {
      children.push(ui.element("p", { class: "muted", text: text(report.agent_provenance) }));
    }
    const assumptions = Array.isArray(backtest.assumptions) ? backtest.assumptions : [];
    if (assumptions.length) children.push(ui.element("ul", {}, assumptions.map((value) => ui.element("li", { text: text(value) }))));

    const candidates = Array.isArray(backtest.candidates) ? backtest.candidates : [];
    if (candidates.length) children.push(
      ui.element("h3", { id: "candidate-evidence", text: "Retail confirmation and regime sizing" }),
      table(["Candidate ID", "Confirmation", "Regime sizing", "Oracle", "Net P&L", "Risk deployed", "Return on debit"], candidates.map((value) => [
        text(value?.id), String(value?.confirmation ?? "—"), String(value?.regime_sizing ?? "—"), String(value?.oracle ?? "—"),
        number(value?.aggregate?.net_pnl), number(value?.aggregate?.risk_deployed), percent(value?.aggregate?.return_on_debit_including_fees),
      ]), "Candidate confirmation and sizing"),
    );

    const regimeRows = [];
    for (const regime of Array.isArray(backtest.regimes) ? backtest.regimes : []) {
      const variants = regime?.variants && typeof regime.variants === "object" ? regime.variants : {};
      for (const [variant, result] of Object.entries(variants)) {
        regimeRows.push([text(regime?.name), text(regime?.fixture_label), text(variant), number(result?.trades),
          number(result?.risk_deployed), percent(result?.return_on_debit_including_fees),
          number(result?.gross_pnl), number(result?.fees), number(result?.net_pnl), percent(result?.win_rate)]);
      }
    }
    if (regimeRows.length) children.push(
      ui.element("h3", { id: "regime-outcomes", text: "Outcomes by regime and strategy" }),
      table(["Regime", "Fixture", "Strategy ID", "Trades", "Risk deployed", "Return on debit", "Gross P&L", "Fees", "Net P&L", "Win rate"], regimeRows, "Synthetic outcomes by regime and strategy"),
    );

    const skipped = Array.isArray(backtest.skipped) ? backtest.skipped : [];
    if (skipped.length) children.push(
      ui.element("h3", { id: "skip-reasons", text: "Skipped cases" }),
      table(["Strategy ID", "Regime", "Reason"], skipped.map((value) => [
        text(value?.variant), text(value?.regime), text(value?.reason),
      ]), "Reasons synthetic cases did not enter"),
    );

    const gammaRows = [];
    for (const trade of Array.isArray(backtest.trades) ? backtest.trades : []) {
      const diagnostics = trade?.gamma_diagnostics;
      if (!diagnostics || typeof diagnostics !== "object") continue;
      const risk = typeof trade.risk_deployed === "number" && trade.risk_deployed !== 0
        ? trade.net_pnl / trade.risk_deployed : null;
      gammaRows.push([text(trade?.variant), text(trade?.regime), number(trade?.risk_deployed), percent(risk),
        number(diagnostics.entry_pair_gamma), number(diagnostics.exit_pair_gamma),
        number(diagnostics.entry_pair_gamma_forward_up_1pct)]);
    }
    if (gammaRows.length) children.push(
      ui.element("h3", { id: "gamma-diagnostics", text: "Gamma diagnostics by recorded trade" }),
      table(["Strategy ID", "Regime", "Risk deployed", "Return on debit", "Entry pair gamma", "Exit pair gamma", "Entry gamma after +1% forward"],
        gammaRows, "Gamma diagnostics used in the synthetic replay"),
    );

    const surface = backtest.surface_example;
    const inputs = surface?.inputs;
    const params = new URLSearchParams();
    for (const key of ["forward", "strike", "volatility_pct", "days", "rate_pct", "multiplier", "quantity", "option_type", "span_pct"]) {
      if (["number", "string"].includes(typeof inputs?.[key])) params.set(key, inputs[key]);
    }
    children.push(ui.element("p", {}, [ui.textLink(params.size ? `/gamma?${params}` : "/gamma",
      params.size ? "Inspect the expansion entry in Gamma Lab" : "Open Gamma Lab")]));
    target.replaceChildren(...children);
    appendAgentNotes(target, report);
  }

  function appendAgentNotes(target, report) {
    const children = [];
    const agents = Array.isArray(report.agents) ? report.agents.filter((value) => roleIds.has(value?.id)) : [];
    if (agents.length) {
      const list = ui.element("div", { class: "agent-research-notes" });
      for (const agent of agents) list.append(ui.element("article", {}, [
        ui.element("h3", { text: roles.find(([id]) => id === agent.id)?.[1] || agent.id }),
        ui.element("p", { class: "muted", text: `Phase: ${text(agent.phase, "general")}. State: ${text(agent.state, "unknown")}.` }),
        ...(typeof agent.text === "string" && agent.text ? [ui.element("p", { text: text(agent.text) })] : []),
      ]));
      children.push(ui.element("h2", { id: "agent-evidence", text: "Selected-agent evidence" }), list);
    }

    const picks = Array.isArray(report.picks) ? report.picks.filter((value) => roleIds.has(value?.role)) : [];
    if (picks.length) children.push(
      ui.element("h2", { id: "agent-picks", text: "Agent candidate picks" }),
      table(["Role", "Candidate"], picks.map((value) => [
        roles.find(([id]) => id === value.role)?.[1] || value.role, text(value?.candidate),
      ]), "Candidate selected by each agent role"),
    );

    const references = Array.isArray(report.references) ? report.references : [];
    if (references.length) {
      const list = ui.element("ul");
      for (const reference of references) {
        let link = null;
        try {
          const url = new URL(reference?.url, globalThis.location.origin);
          if (["http:", "https:"].includes(url.protocol)) link = ui.element("a", { href: url.href, text: text(reference?.title, "Reference"), rel: "noreferrer" });
        } catch { /* Invalid reference URLs remain plain text. */ }
        list.append(ui.element("li", {}, [link || globalThis.document.createTextNode(text(reference?.title, "Reference")),
          globalThis.document.createTextNode(` — ${text(reference?.note, "No note")}`)]));
      }
      children.push(ui.element("h2", { text: "References" }), list);
    }
    target.append(...children);
  }

  function renderRun(target, run) {
    const state = text(run?.state, "unknown");
    target.replaceChildren(
      ui.element("h2", { text: "Research run" }),
      ui.element("p", { text: `State: ${state}. Selected roles: ${Array.isArray(run?.roles) && run.roles.length ? run.roles.join(", ") : "none (offline backtest)"}.` }),
    );
    if (state === "completed") renderReport(target, run.report);
    else if (state === "failed" && run?.report) {
      renderReport(target, run.report);
      target.prepend(ui.element("p", { class: "error", text: text(run?.error, "Some agent steps failed; the local backtest and role notes are still shown below.") }));
    } else if (state === "failed") target.append(ui.element("p", { class: "error", text: text(run?.error, "The run failed without a public diagnostic.") }));
  }

  async function poll(id, target, generation, ticket) {
    try {
      const run = await ui.api(`/api/v1/agent-research/runs/${encodeURIComponent(id)}`);
      if (!ui.isCurrentView(generation) || generation !== request || ticket !== runTicket) return;
      renderRun(target, run);
      if (["queued", "running"].includes(run?.state)) timer = globalThis.setTimeout(() => poll(id, target, generation, ticket), 1000);
      else ui.setStatus("");
    } catch (problem) {
      if (!ui.isCurrentView(generation) || generation !== request || ticket !== runTicket) return;
      ui.setStatus("");
      target.replaceChildren(ui.element("p", { class: "error", text: problem?.message || "Research run status is unavailable." }));
    }
  }

  async function load() {
    dispose();
    const generation = ui.beginView();
    request = generation;
    ui.setNavigation("research");
    ui.setStatus("Loading agent research status.");
    try {
      const status = await ui.api("/api/v1/agent-research/status");
      if (!ui.isCurrentView(generation) || generation !== request) return;
      const checks = new Map();
      const roleBox = ui.element("fieldset", { class: "agent-research-roles" }, [ui.element("legend", { text: "Optional research agents" })]);
      for (const [id, fallback] of roles) {
        const serverRole = Array.isArray(status?.roles) ? status.roles.find((value) => value?.id === id) : null;
        const input = ui.element("input", { type: "checkbox", value: id });
        input.checked = false;
        checks.set(id, input);
        roleBox.append(ui.element("label", {}, [input, globalThis.document.createTextNode(` ${text(serverRole?.label, fallback)}`)]));
      }
      const notice = ui.element("p", { class: "muted", text: status?.enabled === true
        ? `${text(status?.provider_label, "Research provider configured")}. Select roles to send up to seven requests for this run.`
        : `${text(status?.provider_label, "OpenAI agents off")}. The fixed synthetic backtest remains available locally.` });
      const results = ui.element("section", { class: "agent-research-results", "aria-live": "polite" });
      const experiment = ui.element("select", { id: "agent-research-experiment" }, [
        ui.element("option", { value: "options_master", text: "Options master · failure-driven research" }),
        ui.element("option", { value: "weekly_reversal", text: "Daily-band reversal · American weekly options" }),
        ui.element("option", { value: "straddle", text: "Long straddle · confirmation and sizing" }),
      ]);
      experiment.value = "weekly_reversal";
      const offline = ui.element("button", { type: "button", text: "Run fixed synthetic backtest" });
      const selected = ui.element("button", { type: "button", text: "Run selected agents and backtest" });
      selected.disabled = true;
      const update = () => { selected.disabled = status?.enabled !== true || ![...checks.values()].some((input) => input.checked); };
      for (const input of checks.values()) input.addEventListener("change", update);
      async function run(chosen) {
        if (timer !== null) globalThis.clearTimeout(timer);
        timer = null;
        const ticket = ++runTicket;
        offline.disabled = true; selected.disabled = true;
        ui.setStatus(chosen.length ? "Starting selected agents and fixed backtest." : "Starting fixed synthetic backtest.");
        try {
          const result = await ui.api("/api/v1/agent-research/run", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ schema_version: 1, roles: chosen, experiment: experiment.value }),
          });
          if (!ui.isCurrentView(generation) || generation !== request || ticket !== runTicket) return;
          if (typeof result?.id !== "string" || !result.id) throw new Error("The run response did not include an identifier.");
          renderRun(results, result);
          poll(result.id, results, generation, ticket);
        } catch (problem) {
          if (ui.isCurrentView(generation) && generation === request) results.replaceChildren(ui.element("p", { class: "error", text: problem?.message || "The research run was not accepted." }));
        } finally {
          if (ui.isCurrentView(generation) && generation === request) { offline.disabled = false; update(); }
        }
      }
      offline.addEventListener("click", () => run([]));
      selected.addEventListener("click", () => run([...checks].filter(([, input]) => input.checked).map(([id]) => id)));
      ui.replaceView(
        ui.element("p", { class: "muted" }, [ui.textLink("/research", "Research")]),
        ui.element("h1", { text: "Five-agent research" }),
        ui.element("p", { class: "lede", text: "Inspect failure-driven master revisions or a fixed options replay. Optional model specialists review design evidence; QTE owns the calculations." }),
        ui.element("label", { for: "agent-research-experiment", text: "Experiment" }), experiment, notice, roleBox,
        ui.element("div", { class: "agent-research-actions" }, [offline, selected]), results,
      );
      ui.setStatus("");
      const pinned = new URLSearchParams(globalThis.location?.search || "").get("run");
      const initial = pinned && /^[0-9a-f]{32}$/.test(pinned) ? pinned : status?.active_run_id || status?.latest_run_id;
      if (typeof initial === "string" && initial) poll(initial, results, generation, ++runTicket);
    } catch (problem) {
      if (!ui.isCurrentView(generation) || generation !== request) return;
      ui.setStatus("");
      ui.replaceView(ui.element("h1", { text: "Five-agent research unavailable" }),
        ui.element("p", { class: "error", text: problem?.message || "Agent research status could not be loaded." }));
    }
  }

  return { load, dispose };
}
