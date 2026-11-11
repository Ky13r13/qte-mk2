/* Manual-input model calculator. No quotes, invented stream or trading. */
export function createGammaLab(ui) {
  let timer = null, controller = null, observer = null, surfaceCleanup = null;
  let disposed = true, generation = 0;
  const fields = [
    ["forward", "Futures price", 20000, 100, 1000000, "any"],
    ["strike", "Strike", 20000, 100, 1000000, "any"],
    ["volatility_pct", "Implied volatility (%)", 20, 1, 200, "any"],
    ["days", "Calendar days to expiry", 7, 1 / 24, 365, "any"],
    ["rate_pct", "Annual rate (%)", 4, -10, 50, "any"],
    ["multiplier", "Multiplier (USD/point)", 20, 1, 1000, "1"],
    ["quantity", "Signed quantity", 1, -1000, 1000, "1"],
    ["span_pct", "Price span (±%)", 10, 1, 30, "any"],
  ];
  const node = (name, attrs = {}, kids = []) => ui.element(name, attrs, kids);
  const fmt = (v, digits = 6) => typeof v === "number" && Number.isFinite(v)
    ? (v !== 0 && Math.abs(v) < 0.0001 ? v.toExponential(4) : v.toLocaleString(undefined, { maximumFractionDigits: digits })) : "—";
  function dispose() {
    disposed = true; generation += 1;
    clearTimeout(timer); timer = null;
    controller?.abort(); controller = null;
    observer?.disconnect(); observer = null;
    surfaceCleanup?.(); surfaceCleanup = null;
  }
  function svg(name, attrs = {}) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, String(value));
    return n;
  }
  function label(chart, text, x, y, anchor = "start") {
    const n = svg("text", { x, y, "text-anchor": anchor, class: "gamma-label" });
    n.textContent = text; chart.append(n);
  }
  function axis(chart, x1, y1, x2, y2) {
    chart.append(svg("line", { x1, y1, x2, y2, class: "gamma-axis" }));
  }
  function chartWidth(host) { return Math.max(260, Math.floor(host.getBoundingClientRect().width)); }

  function drawSurface(host, data, inspect) {
    const { forwards: fs, days: ds, gamma: values } = data;
    const width = chartWidth(host), height = 300, left = 60, right = width - 16, top = 28, bottom = height - 46;
    const maxGamma = Math.max(...values.flat());
    const x = i => left + i * (right - left) / (fs.length - 1);
    const y = i => bottom - Math.log(ds[i] / ds[0]) / Math.log(ds.at(-1) / ds[0]) * (bottom - top);
    const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Model gamma surface: futures price versus calendar days, with a logarithmic time axis" });
    for (let r = 0; r < ds.length; r += 1) for (let c = 0; c < fs.length; c += 1) {
      const x0 = c === 0 ? left : (x(c - 1) + x(c)) / 2;
      const x1 = c === fs.length - 1 ? right : (x(c) + x(c + 1)) / 2;
      const y0 = r === ds.length - 1 ? top : (y(r) + y(r + 1)) / 2;
      const y1 = r === 0 ? bottom : (y(r - 1) + y(r)) / 2;
      const shade = Math.round(245 - (maxGamma ? 190 * values[r][c] / maxGamma : 0));
      const cell = svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0,
        fill: `rgb(${shade},${shade},${shade})`, class: "gamma-cell" });
      cell.addEventListener("pointerenter", () => inspect(r, c));
      cell.addEventListener("click", () => inspect(r, c));
      chart.append(cell);
    }
    axis(chart, left, top, left, bottom); axis(chart, left, bottom, right, bottom);
    for (const [i, anchor] of [[0, "start"], [Math.floor(fs.length / 2), "middle"], [fs.length - 1, "end"]]) label(chart, fmt(fs[i], 0), x(i), bottom + 19, anchor);
    for (const i of [0, Math.floor(ds.length / 2), ds.length - 1]) label(chart, fmt(ds[i], 3), left - 8, y(i) + 4, "end");
    label(chart, "Days to expiry · log scale", left, 16);
    label(chart, "Futures price (points)", (left + right) / 2, height - 5, "middle");
    host.replaceChildren(chart, node("p", { class: "gamma-legend", text: `Light → dark: γ 0 → ${fmt(maxGamma)} /point · linear shading` }));
  }

  function drawSurface3D(host, data, camera) {
    const { forwards, days, gamma } = data;
    const width = chartWidth(host), height = width < 500 ? 350 : 410;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = node("canvas", { width: Math.round(width * pixelRatio), height: Math.round(height * pixelRatio),
      class: "gamma-surface-3d", tabindex: "0", role: "img",
      "aria-label": "Rotatable 3D gamma surface. X: futures price in points. Y: calendar days, log scale. Z: gamma per point. Drag or use arrow keys to rotate; Home resets." });
    const peak = Math.max(...gamma.flat()), denominator = peak || 1;
    const vertices = days.map((day, r) => forwards.map((forward, c) => ({
      x: 2 * (forward - forwards[0]) / (forwards.at(-1) - forwards[0]) - 1,
      y: 2 * Math.log(day / days[0]) / Math.log(days.at(-1) / days[0]) - 1,
      z: 1.3 * gamma[r][c] / denominator,
    })));
    host.replaceChildren(canvas, node("p", { class: "gamma-legend", text:
      `Drag / arrow keys to rotate · Days: log scale · Height: 0–${fmt(peak)} /point, linear and auto-scaled` }));
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      host.replaceChildren(node("p", { text: "3D drawing is unavailable in this browser. Select Heatmap." }));
      return () => {};
    }
    const palette = getComputedStyle(host);
    const ink = palette.getPropertyValue("--ink").trim() || "#202020";
    const rule = palette.getPropertyValue("--rule").trim() || "#D6D6D2";
    let frame = null, pointer = null, previous = null, stopped = false;
    function draw() {
      frame = null;
      if (stopped) return;
      canvas.dataset.yaw = String(camera.yaw); canvas.dataset.pitch = String(camera.pitch);
      ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const cy = Math.cos(camera.yaw), sy = Math.sin(camera.yaw);
      const cp = Math.cos(camera.pitch), sp = Math.sin(camera.pitch);
      // Orthographic camera above the negative rotated-Y side. Greater depth
      // is nearer the viewer; the same transform drives projection and sorting.
      function rotate(p) {
        const u = cy * p.x - sy * p.y, v = sy * p.x + cy * p.y;
        return { u, v: -sp * v - cp * p.z, depth: -cp * v + sp * p.z };
      }
      const corners = [];
      for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [0, 1.3]) corners.push(rotate({ x, y, z }));
      const minU = Math.min(...corners.map(p => p.u)), maxU = Math.max(...corners.map(p => p.u));
      const minV = Math.min(...corners.map(p => p.v)), maxV = Math.max(...corners.map(p => p.v));
      const scale = Math.min((width - 108) / (maxU - minU), (height - 100) / (maxV - minV));
      function project(p) {
        const q = rotate(p);
        return { x: width / 2 + (q.u - (minU + maxU) / 2) * scale,
          y: (height - 12) / 2 + (q.v - (minV + maxV) / 2) * scale, depth: q.depth };
      }
      function path(points) {
        ctx.beginPath(); ctx.moveTo(points[0].x, points[0].y);
        for (const p of points.slice(1)) ctx.lineTo(p.x, p.y);
      }
      ctx.strokeStyle = rule; ctx.lineWidth = 0.7;
      for (const offset of [-1, -0.5, 0, 0.5, 1]) {
        path([project({ x: offset, y: -1, z: 0 }), project({ x: offset, y: 1, z: 0 })]); ctx.stroke();
        path([project({ x: -1, y: offset, z: 0 }), project({ x: 1, y: offset, z: 0 })]); ctx.stroke();
      }
      const points = vertices.map(row => row.map(project));
      const faces = [];
      for (let r = 0; r < days.length - 1; r += 1) for (let c = 0; c < forwards.length - 1; c += 1) {
        // Triangles avoid nonplanar-quad distortion in a steep expiry ridge.
        for (const indices of [[[r, c], [r + 1, c], [r + 1, c + 1]], [[r, c], [r + 1, c + 1], [r, c + 1]]]) {
          const face = indices.map(([row, col]) => points[row][col]);
          faces.push({ points: face, depth: face.reduce((s, p) => s + p.depth, 0) / 3,
            level: indices.reduce((s, [row, col]) => s + gamma[row][col] / denominator, 0) / 3 });
        }
      }
      faces.sort((a, b) => a.depth - b.depth);
      for (const face of faces) {
        const shade = Math.round(239 - 105 * face.level);
        ctx.fillStyle = `rgb(${shade},${shade},${shade})`;
        ctx.strokeStyle = "rgba(32,32,32,0.22)"; ctx.lineWidth = 0.45;
        path(face.points); ctx.closePath(); ctx.fill(); ctx.stroke();
      }
      ctx.font = "12px system-ui, sans-serif"; ctx.fillStyle = ink; ctx.strokeStyle = ink; ctx.lineWidth = 1;
      function text(value, p, dx = 0, dy = 0) {
        const length = ctx.measureText(value).width;
        const x = Math.max(length / 2 + 3, Math.min(width - length / 2 - 3, p.x + dx));
        ctx.textAlign = "center"; ctx.fillText(value, x, Math.max(14, Math.min(height - 5, p.y + dy)));
      }
      const xf = sy >= 0 ? -1 : 1, yf = cy >= 0 ? -1 : 1;
      const x0 = project({ x: -1, y: yf, z: 0 }), x1 = project({ x: 1, y: yf, z: 0 });
      const y0 = project({ x: xf, y: -1, z: 0 }), y1 = project({ x: xf, y: 1, z: 0 });
      const z0 = project({ x: xf, y: yf, z: 0 }), z1 = project({ x: xf, y: yf, z: 1.3 });
      for (const edge of [[x0, x1], [y0, y1], [z0, z1]]) { path(edge); ctx.stroke(); }
      // Separate axis names from endpoint values; use explicit units in captions.
      text("Futures price (points)", project({ x: 0, y: yf, z: 0 }), 0, 38);
      text("Days (log)", project({ x: xf, y: 0, z: 0 }), -28, -12);
      text("Gamma (1/point)", z1, 0, -14);
      text(fmt(forwards[0], 0), x0, 0, 18); text(fmt(forwards.at(-1), 0), x1, 0, 18);
      text(`${fmt(days[0], 3)}d`, y0, -28, -2); text(`${fmt(days.at(-1), 2)}d`, y1, -28, -2);
      // Zero is already explicit in the height legend. Avoid piling a third
      // label onto the shared minimum-price/minimum-expiry corner.
      for (const fraction of peak ? [0.5, 1] : []) {
        const tick = project({ x: xf, y: yf, z: 1.3 * fraction });
        path([{ x: tick.x - 3, y: tick.y }, { x: tick.x + 3, y: tick.y }]); ctx.stroke();
        text(fmt(peak * fraction), tick, -37, 4);
      }
    }
    function redrawCamera() { if (frame === null) frame = requestAnimationFrame(draw); }
    function turn(yaw, pitch) {
      camera.yaw = (camera.yaw + yaw) % (2 * Math.PI);
      camera.pitch = Math.max(0.3, Math.min(1.35, camera.pitch + pitch));
      redrawCamera();
    }
    function stopDrag() {
      if (pointer !== null && canvas.hasPointerCapture(pointer)) canvas.releasePointerCapture(pointer);
      pointer = null; previous = null;
    }
    canvas.addEventListener("pointerdown", e => {
      if (pointer !== null || !e.isPrimary || e.button !== 0) return;
      pointer = e.pointerId; previous = [e.clientX, e.clientY]; canvas.setPointerCapture(pointer); canvas.focus();
    });
    canvas.addEventListener("pointermove", e => {
      if (e.pointerId !== pointer || !previous) return;
      turn((e.clientX - previous[0]) * 0.008, (e.clientY - previous[1]) * 0.006);
      previous = [e.clientX, e.clientY];
    });
    canvas.addEventListener("pointerup", stopDrag);
    canvas.addEventListener("pointercancel", stopDrag);
    canvas.addEventListener("lostpointercapture", () => { pointer = null; previous = null; });
    canvas.addEventListener("keydown", e => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home"].includes(e.key)) return;
      e.preventDefault();
      if (e.key === "Home") { camera.yaw = 0.65; camera.pitch = 0.55; redrawCamera(); }
      else turn(e.key === "ArrowLeft" ? -0.12 : e.key === "ArrowRight" ? 0.12 : 0,
        e.key === "ArrowUp" ? 0.08 : e.key === "ArrowDown" ? -0.08 : 0);
    });
    draw();
    return () => { stopped = true; stopDrag(); if (frame !== null) cancelAnimationFrame(frame); };
  }

  function drawSlice(host, rows) {
    const width = chartWidth(host), height = 210, left = 80, right = width - 16, top = 28, bottom = height - 46;
    const peak = Math.max(...rows.map(r => r.gamma)), scale = peak || 1;
    const x = r => left + (r.forward - rows[0].forward) / (rows.at(-1).forward - rows[0].forward) * (right - left);
    const y = r => bottom - r.gamma / scale * (bottom - top);
    const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Gamma by futures price at the entered expiry, with numeric gamma axis" });
    axis(chart, left, top, left, bottom); axis(chart, left, bottom, right, bottom);
    for (const value of [0, scale / 2, scale]) label(chart, fmt(value), left - 8, bottom - value / scale * (bottom - top) + 4, "end");
    chart.append(svg("polyline", { points: rows.map(r => `${x(r)},${y(r)}`).join(" "), class: "gamma-slice-line" }));
    const center = rows[Math.floor(rows.length / 2)];
    chart.append(svg("circle", { cx: x(center), cy: y(center), r: 3, class: "gamma-selected" }));
    for (const [row, anchor] of [[rows[0], "start"], [center, "middle"], [rows.at(-1), "end"]]) label(chart, fmt(row.forward, 0), x(row), bottom + 19, anchor);
    label(chart, "Gamma (1/point)", left, 16);
    label(chart, "Futures price (points)", (left + right) / 2, height - 5, "middle");
    host.replaceChildren(chart);
  }

  function load() {
    const request = ui.beginView(); // beginView disposes every old view, including this one.
    disposed = false;
    ui.setNavigation("gamma"); ui.setStatus("");
    let result = null;
    let surfaceMode = "3d";
    const camera = { yaw: 0.65, pitch: 0.55 };
    const root = node("section", { class: "gamma-lab", "aria-labelledby": "gamma-heading" });
    const form = node("form", { class: "gamma-form", novalidate: "" });
    const primary = node("fieldset", {}, [node("legend", { class: "visually-hidden", text: "Manual model inputs" })]);
    const advanced = node("details", {}, [node("summary", { text: "Contract & display settings" })]);
    const extras = node("fieldset", {}, [node("legend", { class: "visually-hidden", text: "Contract and display inputs" })]);
    for (const [index, [key, title, initial, min, max, step]] of fields.entries()) {
      const field = node("label", { class: "gamma-field", for: `gamma-${key}` }, [node("span", { text: title })]);
      field.append(node("input", { id: `gamma-${key}`, name: key, type: "number", value: initial, min, max, step, required: "",
        title: `${fmt(min)} to ${fmt(max)}${step === "1" ? "; whole numbers" : ""}` }));
      (index < 4 ? primary : extras).append(field);
    }
    const kind = node("label", { class: "gamma-field", for: "gamma-option-type" }, [node("span", { text: "Option type" })]);
    kind.append(node("select", { id: "gamma-option-type", name: "option_type" }, [node("option", { value: "call", text: "Call" }), node("option", { value: "put", text: "Put" })]));
    extras.append(kind); advanced.append(extras); form.append(primary, advanced);
    const error = node("p", { class: "gamma-validation", role: "alert", hidden: "" });
    const headline = node("p", { class: "gamma-headline", "aria-live": "polite", text: "Calculating…" });
    const surface = node("div", { class: "gamma-chart" }), slice = node("div", { class: "gamma-chart" });
    const viewControls = node("div", { class: "gamma-view-controls", role: "group", "aria-label": "Surface view" });
    const threeD = node("button", { type: "button", text: "3D surface", "aria-pressed": "true" });
    const heatmap = node("button", { type: "button", text: "Heatmap", "aria-pressed": "false" });
    const resetView = node("button", { type: "button", text: "Reset view" });
    viewControls.append(threeD, heatmap, resetView);
    const sliceHeading = node("h2", { text: "Expiry slice" });
    const inspection = node("p", { class: "gamma-inspection", text: "Model values pending." });
    const valuesPanel = node("details", {}, [node("summary", { text: "Inspect surface values" })]);
    const selectors = node("div", { class: "gamma-selectors" });
    const priceSelect = node("select", { id: "gamma-inspect-price", "aria-label": "Inspect futures price", disabled: "" });
    const timeSelect = node("select", { id: "gamma-inspect-time", "aria-label": "Inspect expiry days", disabled: "" });
    selectors.append(node("label", { for: "gamma-inspect-price", text: "Futures price" }), priceSelect,
      node("label", { for: "gamma-inspect-time", text: "Days to expiry" }), timeSelect);
    valuesPanel.append(selectors);
    const details = node("details", { class: "gamma-details" }, [node("summary", { text: "Calculation details" }), node("dl", { class: "gamma-numbers" })]);
    const warnings = node("p", { class: "muted" });
    details.append(warnings, ui.textLink("/library/gamma-lab", "Model, units and limitations"));
    root.append(node("h1", { id: "gamma-heading", text: "Gamma Lab" }), node("p", { class: "lede", text: "Model only · no live feed · Black-76 European options" }),
      form, error, headline, node("h2", { text: "Gamma surface" }), viewControls, surface, inspection, valuesPanel, sliceHeading, slice, details);
    ui.replaceView(root);

    function inspect(r, c) {
      if (!result) return;
      priceSelect.value = String(c); timeSelect.value = String(r);
      const s = result.surface;
      inspection.textContent = `F ${fmt(s.forwards[c], 2)} · ${fmt(s.days[r], 4)} days · γ ${fmt(s.gamma[r][c], 9)} /point`;
    }
    function redraw() {
      if (!result || disposed || !ui.isCurrentView(request)) return;
      surfaceCleanup?.(); surfaceCleanup = null;
      if (surfaceMode === "3d") surfaceCleanup = drawSurface3D(surface, result.surface, camera);
      else drawSurface(surface, result.surface, inspect);
      drawSlice(slice, result.slice);
    }
    function changeMode(mode) {
      surfaceMode = mode;
      threeD.setAttribute("aria-pressed", String(mode === "3d"));
      heatmap.setAttribute("aria-pressed", String(mode === "heatmap"));
      resetView.hidden = mode !== "3d";
      redraw();
    }
    threeD.addEventListener("click", () => changeMode("3d"));
    heatmap.addEventListener("click", () => changeMode("heatmap"));
    resetView.addEventListener("click", () => { camera.yaw = 0.65; camera.pitch = 0.55; redraw(); });
    const selectCell = () => inspect(Number(timeSelect.value), Number(priceSelect.value));
    priceSelect.addEventListener("change", selectCell); timeSelect.addEventListener("change", selectCell);
    function invalidate(message) {
      result = null; headline.textContent = message;
      surfaceCleanup?.(); surfaceCleanup = null;
      surface.replaceChildren(); slice.replaceChildren();
      details.querySelector("dl").replaceChildren(); warnings.textContent = "";
      inspection.textContent = "Model values pending.";
      priceSelect.replaceChildren(); timeSelect.replaceChildren(); priceSelect.disabled = true; timeSelect.disabled = true;
      sliceHeading.textContent = "Expiry slice";
    }
    function read() {
      const value = { schema_version: 1, option_type: form.elements.option_type.value };
      for (const [key, title, , min, max, step] of fields) {
        const raw = form.elements[key].value, n = Number(raw);
        if (!raw.trim() || !Number.isFinite(n) || n < min || n > max || (step === "1" && !Number.isInteger(n)))
          throw new Error(`${title}: enter ${fmt(min)} to ${fmt(max)}${step === "1" ? " as a whole number" : ""}.`);
        value[key] = n;
      }
      return value;
    }
    function schedule() {
      const ticket = ++generation;
      clearTimeout(timer); controller?.abort(); controller = null;
      invalidate("Recalculating…"); error.hidden = true;
      let inputs;
      try { inputs = read(); }
      catch (e) { headline.textContent = "Correct the input to calculate."; error.textContent = e.message; error.hidden = false; return; }
      timer = setTimeout(async () => {
        controller = new AbortController();
        try {
          const payload = await ui.api("/api/v1/gamma/surface", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(inputs), signal: controller.signal });
          if (disposed || !ui.isCurrentView(request) || ticket !== generation) return;
          if (payload.schema_version !== 1 || payload.mode !== "model_only") throw new Error("Unsupported calculator response.");
          result = payload;
          const p = result.point;
          headline.textContent = `γ ${fmt(p.gamma, 9)} /point · Position γ ${fmt(p.position_gamma, 9)} USD/point²`;
          sliceHeading.textContent = `${fmt(inputs.days, 4)}-day gamma slice`;
          priceSelect.replaceChildren(...result.surface.forwards.map((f, i) => node("option", { value: i, text: fmt(f, 2) })));
          timeSelect.replaceChildren(...result.surface.days.map((d, i) => node("option", { value: i, text: `${fmt(d, 4)} days` })));
          priceSelect.disabled = false; timeSelect.disabled = false;
          const nearest = result.surface.days.reduce((best, d, i, all) => Math.abs(d - inputs.days) < Math.abs(all[best] - inputs.days) ? i : best, 0);
          inspect(nearest, Math.floor(result.surface.forwards.length / 2)); redraw();
          const pairs = [["Premium (points)", p.price], ["Delta", p.delta], ["d1", p.d1], ["Discount factor", p.discount],
            ["T (years, Calendar365)", p.time_years], ["Analytic gamma (1/point)", p.gamma],
            ["Numerical gamma (1/point)", p.finite_difference_gamma], ["Absolute discrepancy", p.finite_difference_absolute_error],
            ["Numerical step (points)", p.finite_difference_step], ["Numerical check status", p.finite_difference_status],
            ["Analytic formula", "exp(−rT) × φ(d1) / (F × σ × √T)"],
            ["Numerical formula", "[P(F+h) − 2P(F) + P(F−h)] / h²"],
            ["Position scaling", `${inputs.quantity} × ${inputs.multiplier} × gamma`]];
          const dl = details.querySelector("dl");
          for (const [key, value] of pairs) dl.append(node("dt", { text: key }), node("dd", { text: typeof value === "number" ? fmt(value, 12) : value }));
          warnings.textContent = [...payload.warnings, "Constant IV; no smile/skew, early exercise, live option chain or dealer inventory. VX is not automatically treated as Nasdaq IV."].join(" ");
        } catch (e) {
          if (e.name === "AbortError" || disposed || !ui.isCurrentView(request) || ticket !== generation) return;
          invalidate("Calculation unavailable."); error.textContent = e.message; error.hidden = false;
        }
      }, 250);
    }
    form.addEventListener("input", schedule);
    form.addEventListener("change", schedule);
    form.addEventListener("submit", e => { e.preventDefault(); schedule(); });
    observer = new ResizeObserver(redraw); observer.observe(surface); observer.observe(slice);
    schedule();
  }
  return { load, dispose };
}
