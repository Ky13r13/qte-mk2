// Local validation artifacts only. Navigation never starts provider or compute work.
export function createDatasetViews(ui) {
  async function list(offset = 0) {
    const request = ui.beginView();
    ui.setNavigation("data"); ui.setStatus("Loading registered catalog.");
    try {
      const payload = await ui.api(`/api/v1/artifacts?offset=${offset}&limit=25`);
      if (!ui.isCurrentView(request)) return;
      const table = ui.element("table");
      table.append(ui.element("caption", { text: "Shared artifact catalog — validation and research evidence" }),
        ui.element("thead", {}, [ui.element("tr", {}, ["Artifact", "Kind", "Status"].map(text => ui.element("th", { scope: "col", text })))]));
      const body = ui.element("tbody");
      for (const item of payload.items || []) {
        body.append(ui.element("tr", {}, [
          ui.element("td", {}, [ui.textLink(`/${item.kind === "alpaca_download_v1" ? "data/downloads" : item.kind === "dataset_validation_v1" ? "data" : "research"}/${encodeURIComponent(item.id)}`, item.name)]),
          ui.element("td", { text: item.kind }), ui.element("td", { text: item.status?.combined_status || "Unknown" }),
        ]));
      }
      table.append(body);
      const paging = ui.element("nav", { class: "pagination", "aria-label": "Artifact pages" });
      for (const [text, next, disabled] of [["Previous", offset - 25, offset === 0], ["Next", offset + 25, offset + 25 >= payload.total]]) {
        const button = ui.element("button", { type: "button", text });
        button.disabled = disabled; button.addEventListener("click", () => list(next)); paging.append(button);
      }
      paging.append(ui.element("span", { text: `${payload.total} registered artifacts` }));
      ui.replaceView(ui.element("h1", { text: "Data" }),
        ui.element("p", { class: "lede", text: "Validate CSV or saved Alpaca inputs. Fixed UTC intervals; no exchange-calendar validation, bar filling or provider requests." }),
        ui.textLink("/data/new", "Validate local dataset"),
        ui.element("p", {}, [ui.textLink("/data/download/alpaca", "Prepare Alpaca download")]),
        ui.element("p", {}, [ui.textLink("/research", "Recent jobs and explicit artifact registration")]),
        ui.element("div", { class: "table-wrap" }, [table]), paging);
      ui.setStatus("");
    } catch (error) { showError(request, error); }
  }

  async function detail(id) {
    const request = ui.beginView();
    ui.setNavigation("data"); ui.setStatus("Loading verified dataset.");
    try {
      const payload = await ui.api(`/api/v1/datasets/${encodeURIComponent(id)}`);
      if (!ui.isCurrentView(request)) return;
      const dataset = payload.dataset;
      const source = dataset.source;
      ui.replaceView(ui.element("p", {}, [ui.textLink("/data", "Data")]),
        ui.element("h1", { text: dataset.name }),
        ui.element("p", { class: "lede", text: "Adapter contract verified · research evidence unknown · exchange calendar not validated" }),
        ui.element("p", { text: source.reason }),
        ...(source.state === "available" ? [ui.textLink(`/research/new/run?dataset=${encodeURIComponent(id)}`, "Use matching source in run")] : []),
        ui.element("details", {}, [ui.element("summary", { text: "Dataset summary and assumptions" }),
          ui.element("pre", { text: JSON.stringify(dataset.summary, null, 2) })]),
        ui.element("details", {}, [ui.element("summary", { text: "Provenance" }),
          ui.element("pre", { text: JSON.stringify(dataset.manifest, null, 2) })]),
        ui.element("p", { class: "muted", text: "The validation artifact is retained. Reusing its source requires matching bytes at the original path; moved or deleted sources must be validated again." }),
        ui.textLink(`/research/${encodeURIComponent(id)}?view=catalog`, "Files, integrity and disclosure controls"));
      ui.setStatus("");
    } catch (error) { showError(request, error, id); }
  }

  function showError(request, error, id = null) {
    if (!ui.isCurrentView(request)) return;
    ui.setStatus("");
    ui.replaceView(ui.element("h1", { text: "Dataset unavailable" }),
      ui.element("p", { class: "error", text: error.message || "Dataset could not be read." }),
      ...(id ? [ui.textLink(`/research/${encodeURIComponent(id)}?view=catalog`, "Inspect integrity and disclosure controls")] : []),
      ui.textLink("/data", "Return to Data"));
  }
  async function download(id) {
    const request = ui.beginView();
    ui.setNavigation("data"); ui.setStatus("Verifying download cache.");
    try {
      const payload = await ui.api(`/api/v1/downloads/${encodeURIComponent(id)}`);
      if (!ui.isCurrentView(request)) return;
      const value = payload.download;
      ui.replaceView(ui.element("p", {}, [ui.textLink("/data", "Data")]),
        ui.element("h1", { text: "Historical download" }),
        ui.element("p", { class: "lede", text: "Verified cache lineage · not research evidence · corporate actions declared, not verified" }),
        ui.element("pre", { text: JSON.stringify(value.configuration, null, 2) }),
        ui.element("details", {}, [ui.element("summary", { text: "Cache lineage and assumptions" }),
          ui.element("pre", { text: JSON.stringify(value.summary, null, 2) })]),
        ui.element("details", {}, [ui.element("summary", { text: "Source identity" }),
          ui.element("pre", { text: JSON.stringify(value.manifest, null, 2) })]),
        ui.element("p", { class: "muted", text: "No refresh runs here. Copy bars.json into an allowed repository data directory and validate it before research; reserved job paths are not general inputs." }),
        ui.textLink(`/research/${encodeURIComponent(id)}?view=catalog`, "Files, checksums and disclosure controls"));
      ui.setStatus("");
    } catch (error) { showError(request, error, id); }
  }
  return { list, detail, download };
}
