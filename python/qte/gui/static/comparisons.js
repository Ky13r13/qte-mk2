// Read-only, safe-role comparison tables. No raw files or jobs are requested.
export function createComparisonViews(ui) {
  const tables = ["candidates", "windows", "comparison"];
  const columns = {
    candidates: ["candidate", "validation_score", "exclusions", "configured_eligible"],
    windows: ["name", "role", "regime", "interval_ns"],
    comparison: ["candidate", "base_return", "stress_return", "result"],
  };
  const text = value => ui.displayValue(value);
  const safeWindows = detail => (detail.windows || []).filter(row =>
    row && ["train", "validation"].includes(row.role) && typeof row.name === "string");

  function appendField(list, field, value) {
    const cell = ui.element("dd", { class: "source-path" });
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const nested = ui.element("dl", { class: "record-details" });
      for (const [key, child] of Object.entries(value)) appendField(nested, key, child);
      cell.append(nested);
    } else cell.textContent = field === "eligible" && value === null ? "Withheld" : text(value);
    list.append(ui.element("dt", { text: ui.humanize(field) }), cell);
  }

  function header(id, detail, selected) {
    const nav = ui.element("nav", { class: "experiment-nav", "aria-label": "Comparison views" });
    for (const table of tables) {
      const query = new URLSearchParams({ comparison_table: table });
      const link = ui.textLink(`/research/${encodeURIComponent(id)}?${query}`, ui.humanize(table));
      if (table === selected) link.setAttribute("aria-current", "page");
      nav.append(link);
    }
    nav.append(ui.textLink(`/research/${encodeURIComponent(id)}?view=catalog`, "Catalog files"));
    const warnings = ui.element("details");
    warnings.append(ui.element("summary", { text: "Interpretation and limitations" }));
    for (const value of detail.warnings || []) warnings.append(ui.element("p", { text: String(value) }));
    return [ui.textLink("/research", "Research"), ui.element("h1", { text: detail.name || "Comparison" }),
      ui.element("p", { class: "muted", text: `${ui.humanize(detail.workflow)}. Training and validation only. Evidence: unknown; final-test provenance and promotion eligibility withheld.` }), nav, warnings];
  }

  function selector(id, detail, windowName, request) {
    const form = ui.element("form", { class: "experiment-window-selector" });
    const label = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Training or validation window" })]);
    const select = ui.element("select", { name: "window", required: "" });
    select.append(ui.element("option", { value: "", text: "Select a recorded window" }));
    for (const row of safeWindows(detail)) select.append(ui.element("option", {
      value: row.name, text: `${row.name} (${row.role}, ${row.regime})`,
    }));
    select.value = windowName || "";
    label.append(select);
    form.append(label, ui.element("button", { type: "submit", text: "View window" }));
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (ui.isCurrentView(request) && safeWindows(detail).some(row => row.name === select.value)) {
        return load(id, "comparison", select.value);
      }
    });
    return form;
  }

  function render(id, detail, payload, table, windowName, offset, request) {
    const headings = columns[table].filter(key => payload.columns.includes(key));
    const tableNode = ui.element("table");
    tableNode.append(ui.element("caption", { class: "visually-hidden", text: `${ui.humanize(table)} records` }),
      ui.element("thead", {}, [ui.element("tr", {}, headings.map(key =>
        ui.element("th", { scope: "col", text: ui.humanize(key) })))]));
    const body = ui.element("tbody");
    for (const [index, row] of payload.rows.entries()) {
      const cells = headings.map((key, columnIndex) => {
        const cell = ui.element("td", { class: /_ns$/.test(key) ? "source-path" : "" });
        if (columnIndex !== 0) cell.textContent = text(row[key]);
        else {
          const open = ui.element("button", { type: "button", class: "record-open", text: text(row[key]),
            "aria-label": `Open complete record ${offset + index + 1}` });
          open.addEventListener("click", () => {
            if (!ui.isCurrentView(request)) return;
            const fields = ui.element("dl", { class: "record-details" });
            for (const field of payload.columns) appendField(fields, field, row[field]);
            const back = ui.element("button", { type: "button", text: "Back to records" });
            back.addEventListener("click", () => {
              if (ui.isCurrentView(request)) render(id, detail, payload, table, windowName, offset, request);
            });
            ui.replaceView(...header(id, detail, table), back, fields);
          });
          cell.append(open);
        }
        return cell;
      });
      body.append(ui.element("tr", {}, cells));
    }
    tableNode.append(body);
    const previous = ui.element("button", { type: "button", text: "Previous" });
    const next = ui.element("button", { type: "button", text: "Next" });
    previous.disabled = offset === 0;
    next.disabled = offset + payload.limit >= payload.total;
    previous.addEventListener("click", () => {
      if (ui.isCurrentView(request) && !previous.disabled) return load(id, table, windowName, Math.max(0, offset - 25));
    });
    next.addEventListener("click", () => {
      if (ui.isCurrentView(request) && !next.disabled) return load(id, table, windowName, offset + 25);
    });
    const paging = ui.element("nav", { class: "pagination", "aria-label": `${table} pages` }, [
      ui.element("p", { text: `${payload.rows.length} of ${payload.total} records` }), previous, next,
    ]);
    const selection = table === "comparison" ? [selector(id, detail, windowName, request)] : [];
    ui.replaceView(...header(id, detail, table), ...selection,
      ui.element("p", { class: "muted", text: "Open a record for all recorded fields." }),
      ...(payload.rows.length ? [] : [ui.element("p", { text: "No recorded rows." })]),
      ui.element("div", { class: "table-wrap" }, [tableNode]), paging);
  }

  async function load(id, table = "candidates", windowName = null, offset = 0) {
    const request = ui.beginView();
    ui.setNavigation("research"); ui.setStatus("Loading comparison.");
    try {
      if (!tables.includes(table) || !Number.isSafeInteger(offset) || offset < 0) throw new Error("Invalid comparison view.");
      const { comparison: detail } = await ui.api(`/api/v1/comparisons/${encodeURIComponent(id)}`);
      if (!ui.isCurrentView(request)) return;
      if (table === "comparison" && !safeWindows(detail).some(row => row.name === windowName)) {
        ui.replaceView(...header(id, detail, table),
          ui.element("p", { text: "Select a training or validation window before viewing results." }),
          selector(id, detail, null, request));
        ui.setStatus(""); return;
      }
      const query = new URLSearchParams({ offset: String(offset), limit: "25" });
      if (table === "comparison") query.set("window", windowName);
      const payload = await ui.api(`/api/v1/comparisons/${encodeURIComponent(id)}/tables/${table}?${query}`);
      if (!ui.isCurrentView(request)) return;
      if (!Array.isArray(payload.columns) || !Array.isArray(payload.rows) || payload.rows.length > 25 ||
          !Number.isSafeInteger(payload.total) || payload.total < 0 || payload.offset !== offset || payload.limit !== 25) {
        throw new Error("Invalid comparison page.");
      }
      render(id, detail, payload, table, windowName, offset, request);
      ui.setStatus("");
    } catch (error) {
      if (ui.isCurrentView(request)) await ui.loadArtifact(id, 0, true, "overview", error.message);
    }
  }
  return { load };
}
