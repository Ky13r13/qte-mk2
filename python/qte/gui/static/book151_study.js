const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const fmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric",
  hour: "2-digit", minute: "2-digit", hour12: false,
});
const URL_ROOT = "/api/v1/book151-study/";
const SIGNALS = { long: "#3ee0ae", short: "#ff7694", exit: "#f5bf62" };

function barIndex(bars, time) {
  let low = 0;
  let high = bars.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (bars[mid][1] <= time) low = mid + 1;
    else high = mid;
  }
  return bars[low] && bars[low][0] <= time ? low : -1;
}

function drawChart(canvas, candles, trades, first, count, hovered) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const left = 22, right = width - 88, top = 28, bottom = height - 43;
  const bars = candles.slice(first, first + count);
  ctx.clearRect(0, 0, width, height);
  if (!bars.length) return;
  const low = Math.min(...bars.map(b => b[4]));
  const high = Math.max(...bars.map(b => b[3]));
  const pad = Math.max((high - low) * .09, .05);
  const max = high + pad, min = low - pad;
  const step = (right - left) / bars.length;
  const x = i => left + (i + .5) * step;
  const y = p => top + (max - p) * (bottom - top) / (max - min);
  ctx.font = "13px system-ui";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 5; i++) {
    const price = min + (max - min) * i / 5;
    const yy = y(price);
    ctx.strokeStyle = "#2a374b";
    ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(right, yy); ctx.stroke();
    ctx.fillStyle = "#b9c7d8"; ctx.fillText(price.toFixed(2), right + 9, yy);
  }
  const showTimes = bars[0][6] === bars.at(-1)[6];
  for (let i = 0; i < 5; i++) {
    const index = Math.round(i * (bars.length - 1) / 5);
    ctx.fillStyle = "#b9c7d8";
    ctx.fillText(showTimes ? fmt.format(bars[index][0]).split(", ").at(-1) : bars[index][6], Math.max(left, x(index) - 18), bottom + 21);
  }
  for (let i = 0; i < bars.length; i++) {
    const [, , open, high, low, close] = bars[i];
    ctx.strokeStyle = ctx.fillStyle = close >= open ? "#50a99c" : "#bf7488";
    ctx.beginPath(); ctx.moveTo(x(i), y(high)); ctx.lineTo(x(i), y(low)); ctx.stroke();
    const w = Math.max(2, step * .62);
    ctx.fillRect(x(i) - w / 2, Math.min(y(open), y(close)), w, Math.max(1.6, Math.abs(y(open) - y(close))));
  }
  function marker(index, type) {
    const local = index - first;
    if (local < 0 || local >= bars.length) return;
    const b = bars[local], xx = x(local), yy = type === "long" ? y(b[4]) + 11 : y(b[3]) - 11;
    ctx.fillStyle = SIGNALS[type];
    ctx.beginPath();
    if (type === "exit") {
      ctx.moveTo(xx, yy - 5); ctx.lineTo(xx + 5, yy); ctx.lineTo(xx, yy + 5); ctx.lineTo(xx - 5, yy);
    } else {
      const side = type === "long" ? 1 : -1;
      ctx.moveTo(xx, yy - side * 7); ctx.lineTo(xx + 6, yy + side * 5); ctx.lineTo(xx - 6, yy + side * 5);
    }
    ctx.closePath(); ctx.fill();
  }
  for (const t of trades) {
    marker(barIndex(candles,t[1]), t[3] > 0 ? "long" : "short");
    marker(barIndex(candles,t[2]), "exit");
  }
  if (hovered >= 0 && hovered < bars.length) {
    ctx.strokeStyle = "#94a9ca";
    ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(x(hovered),top); ctx.lineTo(x(hovered),bottom); ctx.stroke();
    ctx.setLineDash([]);
  }
}

export function createBook151Study(ui) {
  const { element: el, api, beginView, isCurrentView, replaceView, setNavigation, textLink } = ui;
  const caches = new Map();
  async function resource(name) {
    if (!caches.has(name)) {
      const data = await api(URL_ROOT + name);
      caches.set(name, data);
    }
    return caches.get(name);
  }
  async function load() {
    const view = beginView();
    setNavigation("research");
    const back = textLink("/research", "Research");
    const title = el("h1", { text: "Book strategy explorer" });
    const loading = el("p", { role: "status", text: "Loading Schwab study…" });
    replaceView(back,title,loading);
    let index;
    try {
      index = await resource("index");
    } catch (error) {
      if (isCurrentView(view)) loading.textContent = error.message;
      return;
    }
    if (!isCurrentView(view)) return;

    function picker(label, options, selected) {
      const select = el("select", { "aria-label": label }, options.map(([v,t]) => el("option", { value:v,text:t })));
      select.value = selected;
      return [select,el("label",{},[el("span",{text:label}),select])];
    }
    const pair = (values) => values.map(v => [v,v]);
    const [clock, clockLabel] = picker("Candle",pair(index.clocks),"weekly");
    const [rule, ruleLabel] = picker("Strategy",index.rules.map(r => [r.id,`${r.id} · ${r.name}`]),"10.4");
    const [symbol, symbolLabel] = picker("Chart symbol",pair(["SPY","QQQ"]),"SPY");
    const [split, splitLabel] = picker("Period",[["development","Development"],["later","Later evaluation"]],"later");
    const [cost, costLabel] = picker("Execution",[["base","Base"],["stress","Stressed"],["stress_delayed","Stressed + delay"]],"stress_delayed");
    const definition = el("p", { class:"muted" });
    const note = el("p", { role:"status", class:"muted" });
    const metrics = el("dl", { class:"book151-metrics" });
    const canvas = el("canvas",{ width:1180,height:480,role:"img","aria-label":"Candles with colored long, short and exit fills" });
    const position = el("input",{type:"range",min:0,max:0,value:0,"aria-label":"Scroll chart"});
    const tooltip = el("p",{class:"book151-tooltip",text:"Point to a candle to see its prices."});
    const [zoom, zoomLabel] = picker("Visible candles",pair(["30","75","150","300"]),"75");
    const previous = el("button",{type:"button",text:"Earlier"});
    const next = el("button",{type:"button",text:"Later"});
    const legend = el("div",{class:"book151-legend"},[["long","▲ Long entry"],["short","▼ Short entry"],["exit","◆ Exit"]]
      .map(([side,label])=>el("span",{class:"book151-"+side,text:label})));
    const tradeList = el("div",{class:"book151-ledger"});
    const summary = el("details",{class:"book151-screen"},[
      el("summary",{text:`${index.passing.length} pairs passed the retrospective screen`}),
      el("ul",{},index.passing.map(([c,id,pnl,n])=>el("li",{text:`${c} · ${index.rules.find(r=>r.id===id).name} · ${money.format(pnl)} · ${n} trades`}))),
    ]);
    const controls = el("div",{class:"book151-filters"},[clockLabel,ruleLabel,symbolLabel,splitLabel,costLabel]);
    const chart = el("section",{class:"book151-chart"},[legend,canvas,tooltip,el("div",{class:"book151-window"},[previous,position,next,zoomLabel])]);
    replaceView(el("article",{class:"book151-view"},[back,title,controls,definition,note,metrics,chart,summary,el("h2",{text:"Trade ledger"}),tradeList,
      el("p",{class:"muted",text:"Schwab underlying bars. Simulated costs and fills. The strategy panel combines SPY and QQQ; the chart and ledger show the selected symbol."})]));

    let pending = 0, candles = [], trades = [], hovered = -1;
    function redraw() { drawChart(canvas,candles,trades,Number(position.value),Number(zoom.value),hovered); }
    function move(offset) {
      position.value = Math.min(Number(position.max),Math.max(0,Number(position.value)+offset));
      hovered = -1; redraw();
    }
    position.addEventListener("input",()=>{ hovered = -1; redraw(); });
    zoom.addEventListener("change",()=>{
      position.max = Math.max(0,candles.length - Number(zoom.value));
      move(0);
    });
    previous.addEventListener("click",()=>move(-Number(zoom.value)));
    next.addEventListener("click",()=>move(Number(zoom.value)));
    canvas.addEventListener("pointermove",(event)=>{
      const x = event.offsetX * canvas.width / canvas.clientWidth;
      const n = Math.min(Number(zoom.value),candles.length - Number(position.value));
      hovered = Math.floor((x-22)*n/(canvas.width-110));
      const b = hovered>=0 && hovered<n ? candles[Number(position.value)+hovered] : undefined;
      if (b) tooltip.textContent = `${fmt.format(b[0])} ET · O ${b[2].toFixed(2)} · H ${b[3].toFixed(2)} · L ${b[4].toFixed(2)} · C ${b[5].toFixed(2)}`;
      redraw();
    });
    canvas.addEventListener("pointerleave",()=>{ hovered=-1; tooltip.textContent="Point to a candle to see its prices."; redraw(); });

    async function refresh() {
      const ticket = ++pending;
      note.textContent = "Loading selection…";
      const c=clock.value, id=rule.value, s=symbol.value, period=split.value, costs=cost.value;
      try {
        const [b,r] = await Promise.all([resource(`candles-${c}-${s}`),resource(`run-${c}-${id}`)]);
        if (!isCurrentView(view) || ticket !== pending) return;
        const range = index.windows[c.endsWith("m") ? "minute" : "calendar"][period === "development" ? 0 : 1];
        const result=r[period][costs];
        candles=b.candles.filter(b => b[6]>=range[0] && b[6]<range[1]);
        trades=result.trades.filter(t=>t[0]===s);
        definition.textContent=index.rules.find(x=>x.id===id).definition;
        note.textContent=`${range[0]} to ${range[1]} (end exclusive) · Five-candle target hold · ${s}`;
        const stats=[["Net P&L",money.format(result.metrics.pnl)],["Gross P&L",money.format(result.metrics.gross)],
          ["Execution drag",money.format(result.metrics.drag)],["Closed trades",String(result.metrics.trades)]];
        metrics.replaceChildren(...stats.map(([label,value])=>el("div",{},[el("dt",{text:label}),el("dd",{text:value})])));
        position.max=Math.max(0,candles.length-Number(zoom.value));
        position.value=Number(position.max);
        const headings=["Entry (ET)","Exit (ET)","Side","Shares","Net P&L"];
        const rows=trades.map(t=>{
          const button=el("button",{type:"button",class:"book151-jump",text:fmt.format(t[1])});
          button.addEventListener("click",()=>{
            const i=barIndex(candles,t[1]); if (i>=0) { position.value=Math.min(Number(position.max),Math.max(0,i-12)); redraw(); }
          });
          return el("tr",{},[el("td",{},[button]),...[fmt.format(t[2]),t[3]>0?"Long":"Short",String(t[4]),money.format(t[5])]
            .map(text=>el("td",{text}))]);
        });
        tradeList.replaceChildren(trades.length ? el("table",{},[
          el("thead",{},[el("tr",{},headings.map(text=>el("th",{scope:"col",text})))]),el("tbody",{},rows)
        ]) : el("p",{text:"No trades for this symbol and selection."}));
        redraw();
      } catch (error) {
        if (isCurrentView(view) && ticket===pending) {
          note.textContent=error.message; candles=[]; trades=[]; metrics.replaceChildren(); tradeList.replaceChildren(); redraw();
        }
      }
    }
    for (const control of [clock,rule,symbol,split,cost]) control.addEventListener("change",refresh);
    await refresh();
  }
  return { load };
}
