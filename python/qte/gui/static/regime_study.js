/* Observed candles and causal regime explanations. No external chart service. */
import {renderReversalTail} from "./reversal_tail.js";
const COLORS = {unclassified: "#eee", stress_selloff: "#d5a9a9", recovery: "#bccfc1", high_vol_down: "#c7b4b4", high_vol_range: "#c7c3bd", low_vol_trend: "#e0e8e1", trend_up: "#c5d2cb", trend_down: "#c5c3ca", range: "#e2dfd7"};
const money = x => x == null ? "—" : Number(x).toLocaleString(undefined, {maximumFractionDigits: 2});
const percent = x => x == null ? "—" : `${(x*100).toFixed(2)}%`;
const title = x => x.replaceAll("_", " ");

export function createRegimeStudy({api, element: el, replaceView, beginView, isCurrentView, setNavigation, setStatus}) {
  let request = 0, serial = 0;
  function table(headers, rows) {
    return el("div", {class: "table-wrap"}, [el("table", {}, [
      el("thead", {}, [el("tr", {}, headers.map(text => el("th", {scope: "col", text})))]),
      el("tbody", {}, rows.map(row => el("tr", {}, row.map(value => el("td", {text: String(value)}))))),
    ])]);
  }
  function select(label, values, selected) {
    const input = el("select", {"aria-label": label}, values.map(([value, text]) => el("option", {value, text})));
    input.value = selected;
    return input;
  }
  function draw(canvas, rows, minute, daily, trades, onPick) {
    const w = Math.max(330, canvas.parentElement.clientWidth || 1100), h = 420, left = 60, right = 15, top = 20, bottom = 70;
    canvas.width = w*2; canvas.height = h*2;
    canvas.style.width = "100%"; canvas.style.height = "auto";
    const c = canvas.getContext("2d"); c.scale(2, 2);
    c.fillStyle = "#fafaf8"; c.fillRect(0, 0, w, h);
    if (!rows.length) return;
    const prices = rows.flatMap(r => [r.low, r.high, minute ? daily.sma300 : r.sma300,
      minute ? daily.lower2 : r.lower2, minute ? daily.upper2 : r.upper2]).filter(x => Number.isFinite(x));
    // Off-screen distant daily SMA should not flatten an intraday candle chart.
    const candleMin = Math.min(...rows.map(r=>r.low)), candleMax = Math.max(...rows.map(r=>r.high));
    const min = minute ? candleMin : Math.min(...prices), max = minute ? candleMax : Math.max(...prices);
    const pad = Math.max((max-min)*.08, .1), lo = min-pad, hi = max+pad;
    const pw = w-left-right, ph = h-top-bottom, step = pw/rows.length;
    const x = i => left+(i+.5)*step, y = p => top+(hi-p)/(hi-lo)*ph;
    const line = (value, color, dashed=false) => {
      c.strokeStyle = color; c.lineWidth = 1.2; c.setLineDash(dashed ? [5,4] : []); c.beginPath();
      let active = false;
      rows.forEach((r,i) => {const v=value(r); if (v==null || v<lo || v>hi) {active=false; return;}
        if (active) c.lineTo(x(i), y(v)); else c.moveTo(x(i), y(v)); active=true;}); c.stroke(); c.setLineDash([]);
    };
    rows.forEach((r,i) => { c.fillStyle=COLORS[r.regime]||"#eee"; c.globalAlpha=.55; c.fillRect(left+i*step, top, step+.5, ph); }); c.globalAlpha=1;
    c.font="12px sans-serif";
    for (let i=0;i<5;i++) { const p=lo+(hi-lo)*i/4; c.strokeStyle="#ddd"; c.beginPath(); c.moveTo(left,y(p)); c.lineTo(w-right,y(p)); c.stroke(); c.fillStyle="#444"; c.fillText(p.toFixed(1),2,y(p)+4); }
    rows.forEach((r,i) => {
      const color = r.close >= r.open ? "#344c40" : "#694340";
      c.strokeStyle=color; c.fillStyle=color;
      c.beginPath(); c.moveTo(x(i),y(r.high)); c.lineTo(x(i),y(r.low)); c.stroke();
      const body=Math.max(1,Math.abs(y(r.open)-y(r.close)));
      c.fillRect(x(i)-Math.max(1,step*.5)/2, Math.min(y(r.open),y(r.close)), Math.max(1,step*.5),body);
    });
    line(r=>minute?daily.sma300:r.sma300,"#161616",true);
    line(r=>minute?daily.lower2:r.lower2,"#897765",true); line(r=>minute?daily.upper2:r.upper2,"#897765",true);
    if(minute) {line(()=>daily.anchor-daily.expected_move,"#897765",true);line(()=>daily.anchor+daily.expected_move,"#897765",true);}
    if (minute) {line(r=>r.bb_lower,"#888");line(r=>r.bb_upper,"#888");line(r=>r.ema20,"#354f6a");line(r=>r.adjusted_plus,"#826824");}
    for (const t of trades) {
      for (const [timestamp,price,mark] of [[t.entry_time,t.entry_spot,"E"],[t.exit_time,t.exit_spot,"X"]]) {
        const i=rows.findIndex(r=>r.time===timestamp); if(i<0 || price==null)continue;
        c.fillStyle="#111";c.font="bold 12px sans-serif";c.fillText(mark,x(i),y(price)-7);
      }
    }
    c.font="12px sans-serif";c.fillStyle="#444";
    rows.forEach((r,i)=>{if(i%Math.max(1,Math.ceil(rows.length/(w<600?3:7)))===0){const text=minute?new Date(r.time*1000).toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"2-digit",minute:"2-digit"}):r.date;c.fillText(text,x(i)-20,h-48);}});
    if (!minute) {
      const v=rows.filter(r=>r.vix!=null); if(v.length){const vmax=Math.max(...v.map(r=>r.vix));c.strokeStyle="#555";c.beginPath();let active=false;rows.forEach((r,i)=>{if(r.vix==null)return;const vy=h-4-r.vix/vmax*32;if(active)c.lineTo(x(i),vy);else c.moveTo(x(i),vy);active=true;});c.stroke();c.fillText(`Prior VIX · max ${vmax.toFixed(1)}`,left,h-32);}
    }
    canvas.onclick = event => {const rect=canvas.getBoundingClientRect();const i=Math.min(rows.length-1,Math.max(0,Math.floor(((event.clientX-rect.left)/rect.width*w-left)/step)));onPick(rows[i]);};
  }

  async function load() {
    request=beginView(); setNavigation("research");setStatus("Loading regime study…");
    try {
      const [summary, boundaries]=await Promise.all([api("/api/v1/regime-study/summary"),api("/api/v1/regime-study/intervals")]);
      if(!isCurrentView(request))return;
      const year=select("Year",summary.years.map(y=>[y,y]),"2025");
      const month=select("Month",[["all","Whole year"],...Array.from({length:12},(_,i)=>[String(i+1).padStart(2,"0"),new Date(2025,i,1).toLocaleString("en-US",{month:"long"})])],"04");
      const mode=select("Chart interval",[["daily","Daily"],["minute","One minute"]],"daily");
      const day=select("Captured minute session",summary.minute_days.map(d=>[d,d]),summary.minute_days.includes("2025-04-03")?"2025-04-03":summary.minute_days[0]);day.hidden=true;
      const family=select("Minute strategy",[["minute_reentry","Bollinger re-entry"],["minute_sma300","SMA300 reclaim"],["minute_stress","Stress continuation"],["minute_ema","EMA baseline"],["minute_ema_proxy_coverage","EMA · matched volume coverage"],["minute_flow","Volume-adjusted EMA"]],"minute_reentry");family.hidden=true;
      const windowSelect=select("Minute window",[["all","Full session"],["0","09:30–11:00 ET"],["90","11:00–12:30 ET"],["180","12:30–14:00 ET"],["270","14:00–16:00 ET"]],"all");windowSelect.hidden=true;
      const vehicle=select("Minute vehicle",[["long","Long call / put"],["credit","Credit vertical"]],"long");vehicle.hidden=true;
      const canvas=el("canvas", {"aria-label":"SPX candlesticks with regime shading; click a candle for its reason",role:"img"});
      const details=el("p",{text:"Click a candle to inspect the inputs and regime explanation."});
      const periods=el("div"), tradeTable=el("div"), regimeLegend=el("div",{class:"run-nav"}), eventNote=el("p",{class:"muted"});
      const legend=el("p",{class:"muted",text:"Candles: observed SPX. Shading: regime. Dashed black: prior SMA300. Dashed brown: ±2 daily sigma. Minute: gray Bollinger bands, blue EMA, gold volume-adjusted EMA; E entry, X exit. Distant levels outside the minute price range are listed below."});
      const chartControls=el("div",{class:"run-nav"},[mode,year,month,day,windowSelect,family,vehicle]);
      const results=el("div"), wf=el("div"), tailPanel=el("section",{id:"reversal-tail"});
      const regimeNames=[...new Set(summary.daily_comparisons.map(r=>r.regime))].sort();
      const regime=select("Results regime",regimeNames.map(r=>[r,title(r)]),"all");
      const split=select("Chronological split",[["holdout","2021 onward · chronological holdout"],["validation","2016–2020 · validation"],["development","Before 2016 · development"]],"holdout");
      const scenario=select("Cost and IV scenario",[["base_frozen_iv","Base costs · frozen IV"],["wide_frozen_iv","Wide costs · frozen IV"],["vix_repriced","Daily VIX exit repricing"]],"base_frozen_iv");
      const frequency=select("Strategy frequency",[["daily","Daily holds"],["minute","Five-minute holds · selected windows"]],"daily");
      const rules=el("details",{},[el("summary",{text:"Rules, data coverage and limitations"}),...Object.entries(summary.rules).map(([key,value])=>el("p",{text:`${title(key)}: ${value}`})),...summary.limitations.map(text=>el("p",{text}))]);
      const coverage=Object.entries(summary.coverage).map(([s,v])=>`${s}: ${v.first}–${v.last} (${v.bars.toLocaleString()} daily bars)`).join(" · ");
      replaceView(el("p",{},[el("a",{href:"/research",text:"Research"})]),el("h1",{text:"SPX regimes"}),
        el("p",{text:"Observed prices · modeled options · retrospective research"}),el("p",{class:"muted",text:coverage}),chartControls,el("div",{class:"table-wrap"},[canvas]),regimeLegend,eventNote,legend,details,tradeTable,
        el("details",{},[el("summary",{text:"Regime start / end and reason (full daily boundaries)"}),periods]),
        el("h2",{text:"Strategy comparison"}),el("p",{text:"One contract per independent $1m sleeve. Ranked by net P&L / cumulative maximum loss, with ≥30 trades first. This is not an annualized return or risk-matched portfolio comparison. Cash is the benchmark when results are negative."}),
        el("div",{class:"run-nav"},[frequency,regime,split,scenario]),results,
        el("h2",{text:"Walk-forward selection"}),el("p",{text:"Previous five years select a capped-risk strategy for each regime; the following year scores it. Positive training score and ≥30 trades required, otherwise cash. Base model only; no claim of live profitability."}),wf,rules,tailPanel);
      const tailRequest=request;
      void renderReversalTail({api,el,panel:tailPanel,table,draw,valid:()=>isCurrentView(tailRequest)});
      let currentRows=[],currentDaily=null,currentTrades=[];
      function renderChart(){
        const minute=mode.value==="minute";
        regimeLegend.replaceChildren(...[...new Set(currentRows.map(r=>r.regime))].map(r=>{const node=el("span",{text:title(r)});node.style.backgroundColor=COLORS[r]||"#eee";node.style.padding="3px 6px";return node;}));
        eventNote.textContent=(summary.event_annotations||[]).filter(e=>currentRows.length&&e.date>=currentRows[0].date&&e.date<=currentRows.at(-1).date).map(e=>`${e.date}: ${e.label}`).join(" · ");
        const selected=currentTrades.filter(t=>t.family===family.value&&t.vehicle===vehicle.value&&t.scenario===scenario.value);
        draw(canvas,currentRows,minute,currentDaily,selected,row=>{
          const d=minute?currentDaily:row;
          details.textContent=`${row.date}${minute?" "+new Date(row.time*1000).toLocaleTimeString("en-US",{timeZone:"America/New_York"}):""} · ${title(row.regime)} · O ${money(row.open)} H ${money(row.high)} L ${money(row.low)} C ${money(row.close)}. ${row.regime!==d.regime?"Intraday downside shock triggered the session stress latch. ":""}${d.reason}. Inputs through ${d.asof||"warm-up"}; VIX ${money(d.vix)}; SMA300 ${money(d.sma300)}; -2σ ${money(d.lower2)}, +2σ ${money(d.upper2)}.`;
        });
        tradeTable.replaceChildren(...(minute?[table(["Entry ET","Direction","P&L (model)","Max loss","Gamma / point"],selected.map(t=>[new Date(t.entry_time*1000).toLocaleTimeString("en-US",{timeZone:"America/New_York"}),t.direction>0?"Bullish":"Bearish",money(t.pnl),money(t.max_loss),money(t.gamma)]))]:[]));
      }
      async function chart(){const seq=++serial;const minute=mode.value==="minute";year.hidden=minute;month.hidden=minute;day.hidden=!minute;family.hidden=!minute;vehicle.hidden=!minute;windowSelect.hidden=!minute;
        try{const body=await api(`/api/v1/regime-study/${minute?"minute-"+day.value:"year-"+year.value}`);if(seq!==serial||!isCurrentView(request))return;
          currentRows=minute?body.bars.filter(r=>windowSelect.value==="all"||(r.time-body.bars[0].time>=Number(windowSelect.value)*60&&r.time-body.bars[0].time<(Number(windowSelect.value)+(windowSelect.value==="270"?120:90))*60)):body.rows.filter(r=>month.value==="all"||r.date.slice(5,7)===month.value);currentDaily=body.daily;currentTrades=body.trades||[];renderChart();
          const spans=[];for(const row of currentRows){const label=minute?new Date(row.time*1000).toLocaleTimeString("en-US",{timeZone:"America/New_York"}):row.date;const last=spans.at(-1);if(last&&last.regime===row.regime){last.end=label;}else spans.push({regime:row.regime,start:label,end:label,reason:row.reason||(row.regime!==body.daily?.regime?"Observed downside shock; stress latched through session end.":body.daily?.reason)});}
          if(!minute && currentRows.length){spans.splice(0,spans.length,...boundaries.intervals.filter(s=>s.end>=currentRows[0].date&&s.start<=currentRows.at(-1).date));}
          periods.replaceChildren(table(["Start","End (inclusive)","Regime","Why"],spans.map(s=>[s.start,s.end,title(s.regime),s.reason])));
          details.textContent=minute?`${day.value} · prior SMA300 ${money(body.daily.sma300)} · daily -2σ ${money(body.daily.lower2)}, +2σ ${money(body.daily.upper2)}. Click a candle for details.`:"Click a candle for its dated regime explanation. Regimes use earlier sessions only.";
        }catch(error){if(seq===serial&&isCurrentView(request))details.textContent=error.message;}}
      function compare(){const source=frequency.value==="daily"?summary.daily_comparisons:summary.minute_comparisons;
        const rows=source.filter(r=>r.regime===regime.value&&r.split===split.value&&r.scenario===scenario.value).sort((a,b)=>(b.trades>=30)-(a.trades>=30)||(b.return_on_risk??-Infinity)-(a.return_on_risk??-Infinity));
        results.replaceChildren(rows.length?table(["Strategy","Vehicle","Trades","Net P&L","P&L / risk","Win %","Closed DD","Worst trade","Peak collateral"],rows.map(r=>[title(r.family),title(r.vehicle),`${r.trades}${r.trades<30?" · small sample":""}`,money(r.pnl),percent(r.return_on_risk),percent(r.win_rate),money(r.drawdown),money(r.worst_trade),money(r.max_collateral)])):el("p",{text:"No observations for this selection."}));
        const selected=summary.walk_forward.filter(r=>regime.value==="all"||r.regime===regime.value);
        const pnl=selected.reduce((s,r)=>s+r.pnl,0);
        wf.replaceChildren(el("p",{text:`Next-year modeled net P&L: $${money(pnl)} base; $${money(selected.reduce((s,r)=>s+(r.wide_pnl||0),0))} wide costs; $${money(selected.reduce((s,r)=>s+(r.repriced_pnl||0),0))} VIX repricing. Same prior-data selections. Selected regime/year rows: ${selected.length}.`}),el("details",{},[el("summary",{text:"Show annual selections"}),table(["Year","Regime","Selected from earlier data","Training trades","Test trades","Test P&L"],selected.map(r=>[r.year,title(r.regime),r.selected,r.training_trades,r.trades,money(r.pnl)]))]));
      }
      [year,month,mode,day,windowSelect].forEach(input=>input.addEventListener("change",chart));[family,vehicle].forEach(input=>input.addEventListener("change",renderChart));
      [regime,split,scenario,frequency].forEach(input=>input.addEventListener("change",()=>{compare();renderChart();}));
      compare();await chart();if(isCurrentView(request))setStatus("");
    }catch(error){if(isCurrentView(request)){replaceView(el("h1",{text:"SPX regimes"}),el("p",{text:error.message}));setStatus("");}}
  }
  return {load,dispose(){serial++;}};
}
