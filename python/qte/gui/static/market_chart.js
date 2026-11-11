// Presentation only: verified recorded candles, decisions, fills and equity.
// Absolute nanoseconds stay BigInt; only relative screen coordinates use Number.
export function timestampX(timestamp, start, end, left = 65, width = 890) {
  const span = BigInt(end) - BigInt(start);
  return left + (span === 0n ? 0 : Number(BigInt(timestamp) - BigInt(start)) / Number(span)) * width;
}

export function regimeAt(regimes, timestamp) {
  let found = null;
  for (const item of regimes) {
    if (BigInt(item.timestamp_ns) > BigInt(timestamp)) break;
    found = item;
  }
  return found;
}

export function regimeLabel(item) {
  if (!item) return "Not recorded / not yet available";
  return [item.macro_regime, item.volatility_regime, item.active_family, item.phase]
    .filter(value => value !== null && value !== undefined && value !== "").join(" / ") || "Recorded decision";
}

function utc(timestamp) {
  const value = new Date(Number(BigInt(timestamp) / 1000000n));
  return Number.isNaN(value.getTime()) ? String(timestamp) : value.toISOString().replace(".000Z", "Z");
}

function svgNode(name, attributes = {}, title) {
  const node = globalThis.document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (title) {
    const child = svgNode("title"); child.textContent = title; node.append(child);
  }
  return node;
}

function textNode(x, y, text, attributes = {}) {
  const node = svgNode("text", { x, y, class: "market-axis-label", ...attributes });
  node.textContent = text; return node;
}

function range(values) {
  let low = Infinity, high = -Infinity;
  for (const value of values) {
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error("Nonfinite chart value.");
    low = Math.min(low, number); high = Math.max(high, number);
  }
  if (!Number.isFinite(low)) return [0, 1];
  const pad = high === low ? Math.max(Math.abs(high) * 0.01, 1) : (high - low) * 0.08;
  return [low - pad, high + pad];
}

function numberLabel(value) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

export function renderMarketChart(ui, market) {
  const section = ui.element("section", { class: "market-chart", "aria-label": "Strategy price and regime chart" });
  if (market.status !== "recorded" || !market.candles?.length) {
    section.append(ui.element("p", { text: market.reason || "Candles were not recorded in this export. Create a new version-2 run to record them." }));
    return section;
  }
  const candles = market.candles;
  const regimes = market.regimes || [];
  const runs = market.runs || [];
  let first = 0, count = 128, selected = 0;
  const controls = ui.element("div", { class: "market-controls" });
  const previous = ui.element("button", { type: "button", text: "Earlier candles" });
  const next = ui.element("button", { type: "button", text: "Later candles" });
  const size = ui.element("select", { "aria-label": "Visible candles" });
  for (const value of [64, 128, 256]) size.append(ui.element("option", { value: String(value), text: `${value} candles` }));
  size.value = "128";
  const index = ui.element("input", { type: "range", min: "0", max: String(candles.length - 1), value: "0", step: "1", "aria-label": "Inspect recorded candle" });
  const position = ui.element("p", { class: "muted" });
  const chart = ui.element("div");
  const inspection = ui.element("p", { class: "market-inspection", "aria-live": "polite" });
  const facts = ui.element("details", { class: "market-facts" });
  const exact = ui.element("pre");
  facts.append(ui.element("summary", { text: "Exact candle, decision and execution facts" }), exact);
  controls.append(previous, next, size);
  section.append(ui.element("h2", { text: `${market.symbol || "Instrument"} · price, regimes and strategy equity` }),
    ui.element("p", { class: "muted", text: "Hollow candles: close ≥ open. Filled: close < open. ▲ buy fill, ▼ sell fill. ◇ order submitted (when recorded), not an execution. Solid equity: first candidate; dashed: comparison." }),
    controls, position, chart, index, inspection, facts);
  const context = ui.element("details");
  context.append(ui.element("summary", { text: "Chart provenance and limitations" }), ui.element("pre", { text: JSON.stringify({
    window: market.window, cost_scenario: market.cost_scenario, costs: market.costs,
    regime_source: market.regime_source, candlestick_policy: market.candlestick_policy,
    warnings: market.warnings,
  }, null, 2) }));
  section.append(context);
  for (const run of runs) {
    section.append(ui.element("p", { class: "market-legend", text: `${run.candidate}: ${run.status}${run.status !== "ok" ? ` — ${run.error || "No successful recorded curve"}` : ""}` }));
  }

  function inspect() {
    const candle = candles[selected];
    const start = BigInt(candle.start_ns), end = BigInt(candle.end_ns);
    const atOpen = regimeAt(regimes, start), atClose = regimeAt(regimes, end);
    const within = timestamp => BigInt(timestamp) >= start && (BigInt(timestamp) < end || selected === candles.length - 1 && BigInt(timestamp) === end);
    const executions = runs.map(run => ({ candidate: run.candidate,
      fills: (run.fills || []).filter(fill => within(fill.timestamp_ns)),
      order_submissions: (run.fills || []).filter(fill => fill.signal_ns != null && within(fill.signal_ns))
        .map(fill => ({ order_id: fill.order_id, submitted_ns: fill.signal_ns })),
    }));
    inspection.textContent = `${utc(candle.start_ns)} · O ${candle.open} H ${candle.high} L ${candle.low} C ${candle.close} · At open: ${regimeLabel(atOpen)} · At close: ${regimeLabel(atClose)}`;
    exact.textContent = JSON.stringify({ symbol: market.symbol, candle, regime_at_open: atOpen,
      regime_at_close: atClose, executions,
      note: "Decisions apply from their recorded timestamp, not retroactively to the candle open. Fill intervals are [start, end); the final boundary is included. Order submission is not necessarily signal-generation time.",
    }, null, 2);
    index.value = String(selected);
    const cursor = chart.querySelector(".market-cursor");
    if (cursor) {
      const x = timestampX(candle.start_ns, candles[first].start_ns, candles[Math.min(candles.length, first + count) - 1].end_ns);
      cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
    }
  }

  function draw() {
    const visible = candles.slice(first, first + count);
    const start = BigInt(visible[0].start_ns), end = BigInt(visible.at(-1).end_ns);
    const inside = timestamp => BigInt(timestamp) >= start && BigInt(timestamp) <= end;
    const x = timestamp => timestampX(timestamp, start, end);
    const fillValues = runs.flatMap(run => (run.fills || []).filter(fill => inside(fill.timestamp_ns)).map(fill => fill.price));
    const [low, high] = range([...visible.flatMap(candle => [candle.low, candle.high]), ...fillValues]);
    const y = value => 70 + (high - Number(value)) / (high - low) * 210;
    const equityValues = runs.flatMap(run => (run.equity || []).filter(point => inside(point.timestamp_ns)).map(point => point.equity));
    const [equityLow, equityHigh] = range(equityValues);
    const ey = value => 355 + (equityHigh - Number(value)) / (equityHigh - equityLow) * 135;
    const svg = svgNode("svg", { viewBox: "0 0 1000 535", tabindex: "0", role: "img",
      "aria-label": "Recorded candlesticks, timestamped regimes, actual fills and aligned equity. Use left and right arrows to inspect candles; Home and End select endpoints." });
    svg.append(textNode(65, 16, "REGIME · effective from decision timestamp"),
      textNode(65, 62, "PRICE"), textNode(65, 342, "STRATEGY EQUITY · identical window and costs"));
    // Interval bands use as-of decisions; a close-time observation never colors
    // that just-completed candle as though the strategy knew it at the open.
    const changes = [];
    for (const item of regimes) {
      if (changes.length === 0 || regimeLabel(item) !== regimeLabel(changes.at(-1))) changes.push(item);
    }
    for (let i = 0; i < changes.length; i += 1) {
      const item = changes[i];
      const a = BigInt(item.timestamp_ns) < start ? start : BigInt(item.timestamp_ns);
      const b = i + 1 < changes.length && BigInt(changes[i + 1].timestamp_ns) < end ? BigInt(changes[i + 1].timestamp_ns) : end;
      if (a >= b || a >= end || b <= start) continue;
      const title = `${regimeLabel(item)} · effective ${item.timestamp_ns} ns`;
      svg.append(svgNode("rect", { x: x(a), y: 23, width: x(b) - x(a), height: 22,
        class: `market-regime market-regime-${i % 2}` }, title));
      if (x(b) - x(a) > 120) svg.append(textNode(x(a) + 3, 38, regimeLabel(item).slice(0, Math.floor((x(b) - x(a)) / 7))));
      if (inside(item.timestamp_ns)) svg.append(svgNode("line", { x1: x(item.timestamp_ns), x2: x(item.timestamp_ns), y1: 45, y2: 490, class: "market-transition" }, title));
    }
    if (!regimes.length) svg.append(textNode(65, 38, "Regime decisions not recorded — none inferred from price"));
    for (let i = 0; i < visible.length; i += 1) {
      const candle = visible[i];
      const a = x(candle.start_ns), b = x(candle.end_ns), cx = (a + b) / 2;
      const width = Math.max(0.5, Math.min(14, (b - a) * 0.65));
      const title = `${utc(candle.start_ns)} · ${candle.start_ns}–${candle.end_ns} ns · O ${candle.open} H ${candle.high} L ${candle.low} C ${candle.close}`;
      const group = svgNode("g", { class: "market-candle", "data-candle-index": first + i }, title);
      group.append(svgNode("line", { x1: cx, x2: cx, y1: y(candle.high), y2: y(candle.low), class: "market-wick" }),
        svgNode("rect", { x: cx - width / 2, width, y: Math.min(y(candle.open), y(candle.close)),
          height: Math.max(1, Math.abs(y(candle.open) - y(candle.close))),
          class: Number(candle.close) >= Number(candle.open) ? "market-up" : "market-down" }));
      group.addEventListener("pointerenter", () => { selected = first + i; inspect(); });
      group.addEventListener("click", () => { selected = first + i; inspect(); });
      svg.append(group);
    }
    for (const [runIndex, run] of runs.entries()) {
      const signaled = new Set();
      for (const fill of run.fills || []) {
        const title = `${run.candidate} · ${fill.side} ${fill.quantity} @ ${fill.price} · fill ${fill.timestamp_ns} ns · order ${fill.order_id} · commission ${fill.commission} · submitted ${fill.signal_ns ?? "not recorded"}`;
        if (inside(fill.timestamp_ns)) {
          const px = x(fill.timestamp_ns), py = y(fill.price);
          const direction = String(fill.side).toUpperCase() === "BUY" ? -1 : 1;
          svg.append(svgNode("path", { d: `M ${px} ${py + direction * 5} L ${px - 4} ${py - direction * 4} L ${px + 4} ${py - direction * 4} Z`,
            class: `market-fill market-run-${runIndex}` }, title));
        }
        if (fill.signal_ns != null && inside(fill.signal_ns) && !signaled.has(fill.order_id)) {
          signaled.add(fill.order_id);
          const px = x(fill.signal_ns);
          svg.append(svgNode("path", { d: `M ${px} 299 l 4 4 l -4 4 l -4 -4 Z`, class: `market-submission market-run-${runIndex}` }, `Order submitted · ${title}`));
        }
      }
      let path = "", prior = null;
      const interval = BigInt(visible[0].end_ns) - BigInt(visible[0].start_ns);
      for (const point of run.equity || []) {
        if (!inside(point.timestamp_ns)) continue;
        const timestamp = BigInt(point.timestamp_ns);
        const gap = prior !== null && timestamp - prior > interval;
        path += `${prior === null || gap ? "M" : "L"} ${x(timestamp)} ${ey(point.equity)} `;
        prior = timestamp;
      }
      svg.append(svgNode("path", { d: path, class: `market-equity market-run-${runIndex}` }, run.candidate));
    }
    for (const [top, bottom, min, max] of [[70, 280, low, high], [355, 490, equityLow, equityHigh]]) {
      svg.append(svgNode("line", { x1: 65, x2: 955, y1: bottom, y2: bottom, class: "market-axis" }),
        textNode(60, top + 5, numberLabel(max), { "text-anchor": "end" }),
        textNode(60, bottom, numberLabel(min), { "text-anchor": "end" }));
    }
    svg.append(textNode(65, 322, "◇ order-submission time; signal price is not inferred"),
      textNode(65, 512, utc(start)), textNode(955, 512, utc(end), { "text-anchor": "end" }),
      svgNode("line", { class: "market-cursor", x1: 65, x2: 65, y1: 23, y2: 490 }));
    svg.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      selected = event.key === "Home" ? 0 : event.key === "End" ? candles.length - 1
        : Math.min(candles.length - 1, Math.max(0, selected + (event.key === "ArrowRight" ? 1 : -1)));
      if (selected < first || selected >= first + count) {
        first = Math.floor(selected / count) * count; draw();
        chart.querySelector("svg").focus();
      } else inspect();
    });
    chart.replaceChildren(svg);
    position.textContent = `Candles ${first + 1}–${first + visible.length} of ${candles.length}. Original OHLC, no aggregation; elapsed-time gaps stay visible. Arrow keys or the slider inspect exact records.`;
    previous.disabled = first === 0; next.disabled = first + count >= candles.length;
    inspect();
  }
  previous.addEventListener("click", () => { first = Math.max(0, first - count); selected = first; draw(); });
  next.addEventListener("click", () => { first = Math.min(candles.length - 1, first + count); selected = first; draw(); });
  size.addEventListener("change", () => { count = Number(size.value); first = Math.floor(selected / count) * count; draw(); });
  index.addEventListener("input", () => {
    selected = Number(index.value);
    if (selected < first || selected >= first + count) { first = Math.floor(selected / count) * count; draw(); }
    else inspect();
  });
  draw();
  return section;
}
