export function createLiveDesk({api, element: el, replaceView, beginView, isCurrentView, setNavigation, setStatus}) {
  let timer;
  const dispose = () => clearTimeout(timer);
  const text = (tag, value) => el(tag, {text: String(value)});
  const num = x => Number.isFinite(x) ? x.toLocaleString("en-US", {maximumFractionDigits: 4}) : "—";
  const stamp = ns => ns ? new Date(Number(ns) / 1e6).toISOString() : "—";
  function table(headers, rows) {
    const head = el("thead", {}, [el("tr", {}, headers.map(v => text("th", v)))]);
    const body = el("tbody", {}, rows.map(r => el("tr", {}, r.map(v => text("td", v ?? "—")))));
    return el("div", {class: "table-wrap"}, [el("table", {}, [head, body])]);
  }
  function capture(data) {
    const quotes = Array.isArray(data.quotes) ? data.quotes : [];
    return [text("p", `Last recorded state: ${data.connected ? "subscribed" : "disconnected"} · ${stamp(data.received_utc_ns)}`),
      table(["Contract","Bid × size","Ask × size","Quote age (ms)","State"], quotes.map(q => {
        const age = Date.now() - q.source_quote_ms;
        const fresh = data.connected && q.event === "quote" && age >= 0 && age <= data.max_age_ms;
        return [q.contract, q.bid != null ? `${q.bid} × ${q.bid_size}` : "—", q.ask != null ? `${q.ask} × ${q.ask_size}` : "—",
          q.source_quote_ms ? num(age) : "—", fresh ? "Fresh" : q.reason || "Stale"];
      })), text("p", `Subscription epoch ${data.epoch} · Quote updates ${data.counts?.quote ?? 0} · Unavailable ${data.counts?.unavailable ?? 0} · SPX last observation: ${num(data.underlying?.price)} · ${stamp(data.underlying?.received_utc_ns)}`)];
  }
  function quality(d) {
    const orders = d.orders || [];
    const evidence = orders.map(o => {
      const item = el("details", {}, [text("summary", `${o.id} · ${o.side} ${o.quantity} · ${o.status}`)]);
      const q = o.execution_quote;
      item.append(text("p", `${o.contract} · Decision ${stamp(o.decision_ns)} · Earliest modeled arrival ${stamp(o.eligible_ns)}`),
        table(["Quote","Journal line","Bid","Ask","Received (UTC)"], [["Decision",o.decision_quote],["Execution",q]].map(([label,v]) =>
          [label, v?.line, v?.bid, v?.ask, stamp(v?.received_utc_ns)])),
        text("p", `Midpoint ${num(o.decision_mid)} · Paper price ${num(o.price)} · Fees $${num(o.commission_usd)} · Shortfall $${num(o.shortfall_usd)}`),
        text("p", `Entry window at decision: ${o.session?.reason ?? "unavailable"} · Cutoff ${stamp(o.session?.cutoff_ns)}`));
      return item;
    });
    return [text("p", `${d.evidence} quotes · ${orders.length} independent probes · Assumed order delay ${d.assumptions.order_delay_ms} ms`),
      table(["Hour (ET)", "Decision spread", "Fills / probes", "Shortfall $/contract median · p95", "Source to receipt ms median · p95"],
        d.groups.map(g => [g.hour_et,g.decision_spread,`${g.fills} / ${g.probes}`,
          [g.shortfall_per_contract_usd.median,g.shortfall_per_contract_usd.p95].map(num).join(" · "),
          [g.source_to_receipt_ms.median,g.source_to_receipt_ms.p95].map(num).join(" · ")])), ...evidence];
  }
  function gammaGrid(scenarios) {
    const horizons = [...new Set(scenarios.map(s => s.elapsed_minutes))].sort((a,b) => a-b);
    const grids = [];
    for (const minutes of horizons) {
      const points = scenarios.filter(s => s.elapsed_minutes === minutes);
      const axis = k => [...new Set(points.map(s => s[k]))].sort((a,b) => a-b);
      const moves = axis("spot_change_pct");
      const shifts = axis("iv_change_points");
      const header = el("tr", {}, [text("th", "IV shift"), ...moves.map(m => text("th", `Spot ${m}%`))]);
      const body = el("tbody");
      for (const shift of shifts) {
        const row = el("tr", {}, [text("th", `${shift} pts`)]);
        for (const move of moves) {
          const point = points.find(p => p.spot_change_pct === move && p.iv_change_points === shift);
          const gamma = point?.gamma_usd_per_point2;
          const cell = text("td", num(gamma));
          if (Number.isFinite(gamma)) cell.className = gamma > 0 ? "desk-positive" : gamma < 0 ? "desk-negative" : "";
          if (point) cell.title = `${point.status} · Model P&L $${num(point.model_pnl_usd)}`;
          row.append(cell);
        }
        body.append(row);
      }
      grids.push(el("div", {class:"table-wrap"}, [el("table", {class:"desk-grid"}, [
        text("caption", `Portfolio gamma ($/point²) after ${minutes} min`), el("thead",{},[header]), body])]));
    }
    return grids;
  }
  function risk(d) {
    const p = d.portfolio;
    return [text("p", `${d.market_input} · ${d.capture_state || d.spot_status} · As of ${stamp(d.asof_ns)} · ${d.underlying} · Liquidation $${num(p.liquidation_usd)} · Model value $${num(p.model_value_usd)}`),
      text("p", `Delta $/point ${num(p.delta_usd_per_point)} · Gamma $/point² ${num(p.gamma_usd_per_point2)}`),
      table(["Contract","Quantity","Close side","Available size","Liquidation $","Quote state"], d.positions.map(r =>
        [r.contract,r.quantity,r.close_side,r.displayed_size,num(r.liquidation_usd),r.mark_status])),
      ...gammaGrid(d.scenarios),
      table(["Spot move %","IV shift (points)","Minutes elapsed","Model P&L $","Scenario state"], d.scenarios.map(s =>
        [s.spot_change_pct,s.iv_change_points,s.elapsed_minutes,num(s.model_pnl_usd),s.status]))];
  }
  async function load() {
    const params = new URLSearchParams(window.location.search);
    const demo = params.get("demo") === "1";
    const live = !demo && params.get("risk") !== "snapshot";
    const view = beginView();
    setNavigation("research"); setStatus("");
    const parts = {};
    for (const [key,title] of [["capture","Market quotes"],["session","Session clock"],["quality","Paper execution"],["risk","Position scenarios"]]) {
      parts[key] = el("section", {}, [text("h2", title), text("p", "Loading…")]);
    }
    const refresh = el("button", {type:"button",text:"Refresh reports"});
    const switchMode = el("a", {href:demo ? "/research/live-desk" : "/research/live-desk?demo=1",text:demo ? "Open live desk" : "Explore synthetic replay"});
    const nav = el("nav", {class:"run-nav"}, [switchMode, el("a", {href:"/library/live-desk",text:"Desk guide"})]);
    if (!demo) nav.append(el("a", {href:live ? "/research/live-desk?risk=snapshot" : "/research/live-desk",text:live ? "View saved risk snapshot" : "View live position risk"}));
    replaceView(text("h1", demo ? "Live desk · synthetic replay" : "Live desk"),nav,refresh,...Object.values(parts));
    const render = {capture,quality,risk,session: d => [text("p", `${d.admitted ? "Entries open" : "Entries closed"} · ${d.reason} · ${num(d.remaining_ns == null ? null : d.remaining_ns / 1e9)} seconds to cutoff · ${stamp(d.cutoff_ns)}`)]};
    async function update(key) {
      let nodes;
      const resource = key === "risk" && live ? "live-risk" : key;
      try { nodes = render[key](await api(`/api/v1/live-desk/${demo ? "demo/" : ""}${resource}`)); }
      catch (e) { nodes = [text("p", e.message)]; }
      if (isCurrentView(view)) parts[key].replaceChildren(parts[key].firstChild, ...nodes);
    }
    async function tick() {
      await Promise.all((live ? ["capture","session","risk"] : ["capture","session"]).map(update));
      if (!demo && isCurrentView(view)) timer = setTimeout(tick, 3000);
    }
    function reports() { return Promise.all((live ? ["quality"] : ["risk","quality"]).map(update)); }
    refresh.addEventListener("click", reports);
    await Promise.all([reports(),tick()]);
  }
  return {load,dispose};
}
