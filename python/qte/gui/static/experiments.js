// Role-filtered strategy-lab views. This module never reads raw artifact files.
export function createExperimentViews(ui) {
  const views = new Set(["overview", "windows", "comparison", "macro", "regime_decisions", "catalog"]);
  const defaultColumns = {
    candidates: ["candidate", "eligible", "validation_score", "exclusions"],
    windows: ["name", "role", "regime", "interval_ns"],
    comparison: ["candidate", "base_return", "stress_return", "result"],
    macro: ["feature", "value", "reference_ns", "available_ns"],
    regime_decisions: ["timestamp_ns", "macro_regime", "volatility_regime", "permitted_families"],
  };
  const labels = { overview: "Candidates", windows: "Windows", comparison: "Comparison", macro: "Macro inputs", regime_decisions: "Regime decisions", catalog: "Catalog record" };

  function href(id, view, timeframe = "", windowName = "") {
    const query = new URLSearchParams({ lab_view: view });
    if (timeframe) query.set("timeframe", timeframe);
    if (windowName) query.set("window", windowName);
    return `/research/${encodeURIComponent(id)}?${query}`;
  }

  function navigate(id, view, timeframe, windowName = "") {
    window.history.pushState({}, "", href(id, view, timeframe, windowName));
    load(id, { view, timeframe, windowName });
  }

  function text(value, fallback = "Not recorded") { return ui.displayValue(value, fallback); }

  function safeWindows(experiment) {
    return Array.isArray(experiment.windows) ? experiment.windows.filter((item) =>
      item && (item.role === "train" || item.role === "validation") && typeof item.name === "string") : [];
  }

  function header(id, experiment, selected, timeframe, windowName) {
    const breadcrumb = ui.element("p", { class: "muted" }, [ui.textLink("/research", "Research"), globalThis.document.createTextNode(" / "), globalThis.document.createTextNode(experiment.name || "Strategy lab")]);
    const status = experiment.status && typeof experiment.status === "object" ? experiment.status : {};
    const holdout = experiment.holdout && typeof experiment.holdout === "object" ? experiment.holdout : {};
    const selection = experiment.selection && typeof experiment.selection === "object" ? experiment.selection : {};
    const candidate = typeof selection.selected_candidate === "string" && selection.selected_candidate ? selection.selected_candidate : null;
    const selectedText = candidate ? `selected candidate ${candidate}; recorded validation screening` : "no selection; recorded validation screening";
    const lede = ui.element("p", { class: "lede", text: `Evidence: ${ui.humanize(status.evidence)}. Selection: ${selectedText}. Holdout evaluation: ${ui.humanize(holdout.evaluation)}; structured contents withheld.` });
    const nav = ui.element("nav", { class: "experiment-nav", "aria-label": "Strategy-lab views" });
    for (const [view, label] of Object.entries(labels)) {
      const link = ui.textLink(href(id, view, timeframe, windowName), label);
      if (view === selected) link.setAttribute("aria-current", "page");
      nav.append(link);
    }
    const choices = Array.isArray(experiment.timeframes) ? experiment.timeframes.filter((value) => typeof value === "string") : [];
    const selector = ui.element("form", { class: "experiment-timeframe-selector" });
    const label = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Recorded timeframe" })]);
    const select = ui.element("select", { name: "timeframe" });
    for (const value of choices) select.append(ui.element("option", { value, text: value === "daily_24h" ? "daily 24h (synthetic clock)" : value }));
    select.value = timeframe;
    label.append(select); selector.append(label, ui.element("button", { type: "submit", text: "Change timeframe" }));
    selector.addEventListener("submit", (event) => {
      event.preventDefault();
      if (select.value) navigate(id, selected, select.value, "");
    });
    return [breadcrumb, ui.element("h1", { text: experiment.name || "Strategy lab" }), lede, nav, selector];
  }

  function contextDetails(experiment) {
    const details = ui.element("details", { class: "experiment-context" });
    details.append(ui.element("summary", { text: "Recorded context" }));
    const context = experiment.context && typeof experiment.context === "object" ? experiment.context : {};
    const values = [["Timeframe", experiment.timeframe], ["Protocol", experiment.protocol_id], ["Source identity", experiment.source_identity], ["Currency", context.currency], ["Interval (ns)", context.interval_ns], ["Initial cash", context.initial_cash], ["Base costs", context.base_costs], ["Stressed costs", context.stressed_costs]];
    const list = ui.element("dl");
    for (const [name, value] of values) {
      if (value && typeof value === "object" && !Array.isArray(value)) appendRecordValue(list, name, value);
      else list.append(ui.element("dt", { text: name }), ui.element("dd", { class: /identity|Protocol/.test(name) ? "hash" : "", text: text(value) }));
    }
    details.append(list);
    return details;
  }

  function warnings(experiment) {
    const values = Array.isArray(experiment.warnings) ? experiment.warnings : [];
    return values.length ? [ui.element("p", { class: "error", text: values.map((warning) => String(warning)).join(" ") })] : [];
  }

  function windowSelector(id, experiment, selected, timeframe, windowName) {
    const form = ui.element("form", { class: "experiment-window-selector" });
    const label = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Safe train or validation window" })]);
    const select = ui.element("select", { name: "window", required: "" });
    select.append(ui.element("option", { value: "", text: "Select a recorded window" }));
    for (const window of safeWindows(experiment)) select.append(ui.element("option", { value: window.name, text: `${window.name} (${window.role}, ${window.regime})` }));
    select.value = windowName;
    label.append(select);
    form.append(label, ui.element("button", { type: "submit", text: "View window" }));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (select.value) navigate(id, selected, timeframe, select.value);
    });
    return form;
  }

  function appendRecordValue(list, name, value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const nested = ui.element("dl", { class: "record-details" });
      for (const [key, child] of Object.entries(value)) appendRecordValue(nested, key, child);
      list.append(ui.element("dt", { text: ui.humanize(name) }), ui.element("dd", {}, [nested]));
      return;
    }
    list.append(ui.element("dt", { text: ui.humanize(name) }), ui.element("dd", { class: /(?:_ns|_id|sequence|quantity)$/.test(name) ? "source-path" : "", text: text(value, "") }));
  }

  function recordView(id, experiment, payload, table, row, rowNumber, selected, timeframe, windowName, offset) {
    const fields = [...new Set([...(Array.isArray(payload.columns) ? payload.columns : []), ...Object.keys(row || {})])];
    const details = ui.element("dl", { class: "record-details" });
    for (const field of fields) appendRecordValue(details, field, row[field]);
    const back = ui.element("button", { type: "button", text: "Back to records" });
    back.addEventListener("click", () => renderTable(id, experiment, payload, table, selected, timeframe, windowName, offset));
    ui.replaceView(...header(id, experiment, selected, timeframe, windowName), ...warnings(experiment), ui.element("h2", { text: `${ui.humanize(table)} record ${rowNumber}` }), back, details);
  }

  function renderTable(id, experiment, payload, table, selected, timeframe, windowName, offset) {
    const columns = Array.isArray(payload.columns) ? payload.columns : [];
    const preferred = (defaultColumns[table] || []).filter((column) => columns.includes(column));
    const visible = preferred.length ? preferred : columns.slice(0, 4);
    const tableNode = ui.element("table");
    tableNode.append(ui.element("caption", { class: "visually-hidden", text: `${ui.humanize(table)} records` }));
    tableNode.append(ui.element("thead", {}, [ui.element("tr", {}, visible.map((column) => ui.element("th", { scope: "col", text: ui.humanize(column) })))]));
    const body = ui.element("tbody");
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    for (const [index, row] of rows.entries()) {
      const cells = visible.map((column, columnIndex) => {
        const cell = ui.element("td", { class: /(?:_ns|_id|sequence|quantity)$/.test(column) ? "source-path" : "" });
        if (columnIndex === 0) {
          const open = ui.element("button", { type: "button", class: "record-open", text: text(row[column], "Open record"), "aria-label": `Open complete record ${offset + index + 1}` });
          open.addEventListener("click", () => recordView(id, experiment, payload, table, row, offset + index + 1, selected, timeframe, windowName, offset));
          cell.append(open);
        } else cell.textContent = text(row[column], "");
        return cell;
      });
      body.append(ui.element("tr", {}, cells));
    }
    tableNode.append(body);
    const section = ui.element("section", { class: "experiment-table" });
    section.append(ui.element("h2", { text: ui.humanize(table) }));
    if (columns.length > visible.length) section.append(ui.element("p", { class: "muted", text: `Showing ${visible.length} of ${columns.length} recorded columns. Open a record for all recorded fields.` }));
    if (!rows.length) section.append(ui.element("p", { text: "No recorded rows are available for this safe view." }));
    section.append(ui.element("div", { class: "table-wrap" }, [tableNode]));
    const nav = ui.element("nav", { class: "pagination", "aria-label": `${table} pages` });
    const total = Number.isSafeInteger(payload.total) ? payload.total : 0;
    nav.append(ui.element("p", { class: "muted", text: `${rows.length} of ${total} records` }));
    const previous = ui.element("button", { type: "button", text: "Previous" });
    previous.disabled = offset <= 0;
    previous.addEventListener("click", () => load(id, { view: selected, timeframe, windowName, offset: Math.max(0, offset - payload.limit) }));
    const next = ui.element("button", { type: "button", text: "Next" });
    next.disabled = offset + payload.limit >= total;
    next.addEventListener("click", () => load(id, { view: selected, timeframe, windowName, offset: offset + payload.limit }));
    nav.append(previous, next); section.append(nav);
    if (Array.isArray(payload.warnings)) for (const warning of payload.warnings) section.append(ui.element("p", { class: "error", text: String(warning) }));
    const selector = ["comparison", "macro", "regime_decisions"].includes(table)
      ? [windowSelector(id, experiment, selected, timeframe, windowName)] : [];
    ui.replaceView(...header(id, experiment, selected, timeframe, windowName), contextDetails(experiment), ...warnings(experiment), ...selector, section);
  }

  async function load(id, options = {}) {
    const selected = views.has(options.view) ? options.view : "overview";
    const timeframe = typeof options.timeframe === "string" ? options.timeframe : "";
    const windowName = typeof options.windowName === "string" ? options.windowName : "";
    const offset = Number.isSafeInteger(options.offset) && options.offset >= 0 ? options.offset : 0;
    const request = ui.beginView();
    ui.setNavigation("research"); ui.setStatus("Loading strategy lab.");
    try {
      const params = new URLSearchParams();
      if (timeframe) params.set("timeframe", timeframe);
      const detail = await ui.api(`/api/v1/experiments/${encodeURIComponent(id)}${params.size ? `?${params}` : ""}`);
      if (!ui.isCurrentView(request)) return;
      const experiment = detail.experiment || detail;
      const recordedTimeframe = typeof experiment.timeframe === "string" ? experiment.timeframe : timeframe;
      if (selected === "catalog") { await ui.loadArtifact(id, 0, true); return; }
      const table = selected === "overview" ? "candidates" : selected;
      const needsWindow = ["comparison", "macro", "regime_decisions"].includes(table);
      if (needsWindow && !windowName) {
        ui.replaceView(...header(id, experiment, selected, recordedTimeframe, ""), contextDetails(experiment), ui.element("p", { class: "error", text: "Select a recorded train or validation window before viewing this table." }), windowSelector(id, experiment, selected, recordedTimeframe, ""), ...warnings(experiment));
        ui.setStatus("");
        return;
      }
      const tableParams = new URLSearchParams({ timeframe: recordedTimeframe, offset: String(offset), limit: "25" });
      if (needsWindow) tableParams.set("window", windowName);
      const payload = await ui.api(`/api/v1/experiments/${encodeURIComponent(id)}/tables/${encodeURIComponent(table)}?${tableParams}`);
      if (!ui.isCurrentView(request)) return;
      renderTable(id, experiment, payload, table, selected, recordedTimeframe, windowName, offset);
      ui.setStatus("");
    } catch (error) {
      if (ui.isCurrentView(request)) await ui.loadArtifact(id, 0, true, "overview", error.message);
    }
  }

  return { load, isView: (value) => views.has(value) };
}
