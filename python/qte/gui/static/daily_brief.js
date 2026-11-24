export function createDailyBrief({api, element: el, replaceView, beginView, isCurrentView, setNavigation}) {
  const text = (tag, value) => el(tag, {text: value});
  const number = (v, signed = false) => Number.isFinite(v)
    ? (signed && v > 0 ? "+" : "") + v.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})
    : "—";
  function pct(value) { return Number.isFinite(value) ? number(value, true) + "%" : "—"; }
  function rangeChart(sigma) {
    const {low, high} = sigma;
    const last = sigma.last ?? sigma.close;
    if (![low, high, last].every(Number.isFinite) || low > high) return [];
    const extent = Math.max(2, Math.ceil(Math.max(Math.abs(low), Math.abs(high), Math.abs(last))));
    const x = v => 280 + 246 * (v / extent);
    const svg = (tag, attrs) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,v);
      return node;
    };
    const caption = "SPX range " + number(low,true) + " to " + number(high,true) + "σ · Last " + number(last,true) + "σ";
    const chart = svg("svg", {viewBox:"0 0 560 55",width:"100%",height:55,role:"img","aria-label":caption});
    chart.append(svg("rect",{x:x(-1),y:2,width:x(1)-x(-1),height:28,fill:"#e7e7e3"}));
    for (const n of [-1, 0, 1]) {
      chart.append(svg("line",{x1:x(n),x2:x(n),y1:2,y2:32,stroke:"#999"}));
      const label = svg("text",{x:x(n),y:49,"text-anchor":"middle","font-size":12});
      label.textContent = n + "σ"; chart.append(label);
    }
    chart.append(svg("line",{x1:x(low),x2:x(high),y1:16,y2:16,stroke:"#475b69","stroke-width":5}),
      svg("circle",{cx:x(last),cy:16,r:5,fill:"#151719"}));
    return [el("figure",{},[text("figcaption",caption),chart])];
  }
  function marketTable(rows) {
    const header = el("tr", {}, ["Market", "Last", "Gap", "Return", "Intraday coverage"].map(x => text("th", x)));
    const body = rows.map(r => {
      const count = r.observed == null ? "—" : [r.observed, r.expected].filter(x => x != null).join("/") + " × " + r.interval_seconds + "s";
      return el("tr", {}, [r.symbol, number(r.last), pct(r.gap_pct), pct(r.return_pct), count].map(x => text("td", x)));
    });
    return el("div", {class: "table-scroll"}, [el("table", {}, [el("thead", {}, [header]), el("tbody", {}, body)])]);
  }
  function render(brief) {
    const news = brief.news.map(item => {
      const link = text("span", item.headline);
      const source = new URL(item.source);
      const nodes = [link, document.createTextNode(" · " + item.time + " · ")];
      if (source.protocol === "https:") nodes.push(el("a", {href:source.href, text:source.hostname, rel:"noreferrer"}));
      if (item.attribution) nodes.push(text("p", item.attribution));
      return el("li", {}, nodes);
    });
    return [
      text("h2", brief.outcome),
      text("h3", "Regime at the open · " + brief.regime.replaceAll("_", " ")),
      text("p", brief.reason + (brief.asof ? " · As of " + brief.asof : "")),
      text("h3", "News"), news.length ? el("ul", {}, news) : text("p", "No verified event recorded."),
      ...rangeChart(brief.sigma), marketTable(brief.markets), ...brief.coverage.map(n => text("p", n)),
      el("details", {}, [text("summary", "Data and provenance"), text("p", brief.evidence_path),
        text("p", "SHA-256 " + brief.source_sha256), text("pre", JSON.stringify(brief.data, null, 2))])
    ];
  }
  return async function load() {
    const view = beginView();
    setNavigation("research");
    const body = el("section", {"aria-live":"polite"}, [text("p","Loading brief…")]);
    const select = el("select", {"aria-label":"Session and capture"});
    replaceView(text("h1","Daily brief"), select, body);
    let sequence = 0;
    async function show() {
      const current = ++sequence;
      try {
        const brief = await api("/api/v1/daily-brief/" + encodeURIComponent(select.value));
        if (isCurrentView(view) && current === sequence) body.replaceChildren(...render(brief));
      } catch (err) {
        if (isCurrentView(view) && current === sequence) body.replaceChildren(text("p",err.message));
      }
    }
    try {
      const {entries} = await api("/api/v1/daily-brief/index");
      if (!isCurrentView(view)) return;
      const counts = new Map();
      for (const e of entries) counts.set(e.session,(counts.get(e.session) || 0) + 1);
      for (const e of entries) {
        const revisions = counts.get(e.session);
        select.append(el("option", {value:e.id, text:revisions > 1 ? e.session + " · revision " + revisions : e.session}));
        counts.set(e.session,revisions - 1);
      }
      select.addEventListener("change",show);
      if (entries.length) await show();
      else body.replaceChildren(text("p","No sessions published."));
    } catch (err) {
      if (isCurrentView(view)) body.replaceChildren(text("p",err.status === 404 ? "No daily briefs published yet." : err.message));
    }
  };
}
