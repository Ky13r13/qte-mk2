// Semantic, dependency-free view of the frozen weekly reversal research report.
export function renderWeeklyReversal(ui, backtest) {
  const node = ui.element;

  function text(value, fallback = "—") {
    return typeof value === "string" && value ? value.slice(0, 240) : fallback;
  }

  function number(value, digits = 4) {
    if (typeof value === "string" && /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(value)) return value;
    return typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";
  }

  function table(headers, rows, caption) {
    const result = node("table", { class: "agent-research-table" });
    result.append(node("caption", { class: "visually-hidden", text: caption }));
    result.append(node("thead", {}, [node("tr", {}, headers.map((label) => node("th", { scope: "col", text: label })))]));
    result.append(node("tbody", {}, rows.map((row) => node("tr", {}, row.map((value) => node("td", { text: value }))))));
    return result;
  }

  function candidates(scenario) {
    return Array.isArray(backtest?.runs?.[scenario]?.candidates) ? backtest.runs[scenario].candidates : [];
  }

  const base = candidates("base");
  const stressById = new Map(candidates("stress").map((value) => [value?.id, value]));
  const comparisonRows = [];
  for (const candidate of base) {
    if (typeof candidate?.id !== "string") continue;
    for (const [scenario, value] of [["Base", candidate], ["Stress", stressById.get(candidate.id)]]) {
      if (!value) continue;
      const all = value.slices?.all || {};
      const evaluation = value.slices?.evaluation || {};
      comparisonRows.push([candidate.id, scenario, number(all.net_pnl, 2), number(all.risk_deployed, 2),
        number(evaluation.net_pnl, 2), number(evaluation.risk_deployed, 2), number(all.entries, 0),
        number(all.mean_entries, 2), number(all.median_entries, 2), number(all.unresolved, 0)]);
    }
  }

  const evidence = backtest?.evidence === "synthetic_mechanics_only"
    ? "Synthetic mechanics fixture only. Evaluation covers the final eight designed sessions."
    : "User-supplied capture replay; merit and representativeness are not established.";
  const roots = [
    node("section", { class: "weekly-reversal-report", "aria-labelledby": "weekly-reversal-heading" }, [
      node("h2", { id: "weekly-reversal-heading", text: "Weekly reversal suite" }),
      node("p", { class: "muted", text: evidence }),
      node("h3", { text: "Strategy comparison under base and stress costs" }),
      table(["Strategy ID", "Costs", "All net P&L", "All risk", "Evaluation net P&L", "Evaluation risk",
        "Entries", "Mean/session", "Median/session", "Unresolved"], comparisonRows,
      "Weekly reversal strategy comparison with trade cadence"),
    ]),
  ];
  const root = roots[0];
  const assumptions = backtest?.runs?.base?.assumptions;
  if (Array.isArray(assumptions)) {
    root.append(node("details", {}, [
      node("summary", { text: "Replay and execution assumptions" }),
      node("ul", {}, assumptions.map(value => node("li", { text: text(value) }))),
    ]));
  }

  if (!base.length) {
    root.append(node("p", { class: "muted", text: "No weekly reversal candidate results were recorded." }));
    return roots;
  }

  const metadata = backtest?.session_metadata && typeof backtest.session_metadata === "object" ? backtest.session_metadata : {};
  const cases = [...new Set(Object.values(metadata).map((value) => value?.case).filter((value) => typeof value === "string"))].sort();
  const controls = node("div", { class: "gamma-selectors" });
  const select = node("select", { "aria-label": "Inspect weekly reversal strategy" });
  for (const candidate of base) select.append(node("option", { value: candidate.id, text: candidate.id }));
  controls.append(node("label", { text: "Inspect strategy" }), select);
  const caseHost = node("div", { "aria-live": "polite" });
  const detailHost = node("div");

  function selectedPair() {
    const selected = base.find((value) => value?.id === select.value) || base[0];
    return [selected, stressById.get(selected.id)];
  }

  function renderCases() {
    const [selected, stress] = selectedPair();
    const rows = [];
    for (const caseName of cases) {
      for (const [scenario, value] of [["Base", selected], ["Stress", stress]]) {
        const slice = value?.slices?.[caseName];
        if (slice) rows.push([caseName, scenario, number(slice.net_pnl, 2), number(slice.risk_deployed, 2),
          number(slice.entries, 0), number(slice.mean_entries, 2), number(slice.median_entries, 2), number(slice.unresolved, 0)]);
      }
    }
    caseHost.replaceChildren(node("h3", { text: `Cases · ${selected.id}` }),
      ...(rows.length ? [table(["Case", "Costs", "Net P&L", "Risk", "Entries", "Mean/session", "Median/session", "Unresolved"],
        rows, `Case results for ${selected.id}`)] : [node("p", { class: "muted", text: "No labeled case slices are available." })]));
  }

  function compactSkips(values) {
    const counts = new Map();
    for (const value of values) {
      const key = `${text(value?.session)}\u0000${text(value?.reason)}`;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts].map(([key, count]) => {
      const [session, reason] = key.split("\u0000");
      return [session, reason, String(count)];
    });
  }

  function renderDetails() {
    const [selected, stress] = selectedPair();
    const tradeRows = [];
    const unresolvedRows = [];
    const skipRows = [];
    for (const [scenario, value] of [["Base", selected], ["Stress", stress]]) {
      for (const trade of Array.isArray(value?.trades) ? value.trades : []) tradeRows.push([
        scenario, text(trade.session), text(trade.observable_regime), number(trade.excursion_peak_sigma, 2),
        String(trade.signal_asof_ns ?? "—"), String(trade.decision_available_ns ?? "—"),
        String(trade.entry_ns ?? "—"), String(trade.exit_ns ?? "—"), number(trade.risk_deployed, 2), number(trade.net_pnl, 2),
      ]);
      for (const item of Array.isArray(value?.unresolved) ? value.unresolved : []) unresolvedRows.push([
        scenario, text(item.session), String(item.signal_asof_ns ?? "—"), String(item.decision_available_ns ?? "—"),
        String(item.entry_ns ?? "—"), String(item.deadline_ns ?? "—"), number(item.risk_deployed, 2), text(item.reason),
      ]);
      for (const row of compactSkips(Array.isArray(value?.skipped) ? value.skipped : [])) skipRows.push([scenario, ...row]);
    }
    const details = node("details", {}, [node("summary", { text: `Trade and skip inspection · ${selected.id}` })]);
    if (tradeRows.length) details.append(node("h4", { text: "Closed trades" }), table(
      ["Costs", "Session", "Observed regime", "Peak σ", "Signal as-of ns", "Decision available ns", "Entry ns", "Exit ns", "Risk", "Net P&L"],
      tradeRows, `Closed trades for ${selected.id}`));
    if (unresolvedRows.length) details.append(node("h4", { text: "Unresolved entries" }), table(
      ["Costs", "Session", "Signal as-of ns", "Decision available ns", "Entry ns", "Deadline ns", "Risk", "Reason"],
      unresolvedRows, `Unresolved entries for ${selected.id}`));
    if (skipRows.length) details.append(node("h4", { text: "Skip reasons (compacted)" }), table(
      ["Costs", "Session", "Reason", "Count"], skipRows, `Compacted skip reasons for ${selected.id}`));
    if (!tradeRows.length && !unresolvedRows.length && !skipRows.length) details.append(node("p", { class: "muted", text: "No trade, unresolved, or skip records for this strategy." }));
    detailHost.replaceChildren(details);
  }

  select.addEventListener("change", () => { renderCases(); renderDetails(); });
  root.append(controls, caseHost, detailHost);
  renderCases();
  renderDetails();

  const gamma = backtest?.gamma;
  if (gamma && Array.isArray(gamma.spots) && Array.isArray(gamma.days) && Array.isArray(gamma.gamma)) {
    const rows = gamma.days.map((day, index) => [number(day, 4), ...gamma.spots.map((_spot, column) => number(gamma.gamma[index]?.[column], 9))]);
    root.append(node("h3", { text: "American put gamma scenarios" }),
      node("p", { class: "muted", text: `Model: ${text(gamma.model)}. Values are per-share model gamma, not dealer positioning.` }),
      table(["Days / spot", ...gamma.spots.map((spot) => number(spot, 2))], rows, "American put gamma scenario grid"));
  }
  return roots;
}
