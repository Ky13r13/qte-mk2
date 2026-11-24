/* Saved, offline evidence only. No market-data or brokerage connection. */
export async function renderReversalTail({api, el, panel, table, draw, valid}) {
  const fmt = x => x == null ? "—" : Number(x).toLocaleString(undefined, {maximumFractionDigits: 2});
  const clock = t => new Date(t*1000).toLocaleTimeString("en-US", {timeZone:"America/New_York", hour:"2-digit", minute:"2-digit"});
  let serial=0;
  try {
    const s=await api("/api/v1/regime-study/tail-summary");
    if(!valid()) return;
    const day=el("select", {"aria-label":"Reversal / tail session"}, s.days.map(d=>el("option",{value:d,text:d})));
    day.value=s.days.includes("2024-12-18")?"2024-12-18":s.days[0];
    const mode=el("select", {"aria-label":"Reversal / tail chart mode"}, [el("option",{value:"tail",text:"Last-hour put diagnostic"}),el("option",{value:"reversal",text:"Long reversal · full session"})]);
    const bound=el("select", {"aria-label":"Reversal expected-move bound"}, [el("option",{value:"1",text:"−1σ"}),el("option",{value:"2",text:"−2σ"})]);
    const canvas=el("canvas",{role:"img","aria-label":"Observed SPX candles, daily sigma bounds and hypothetical trade markers"});
    const premium=el("canvas",{role:"img","aria-label":"Modeled put proceeds divided by entry cost; hindsight diagnostic"});
    const explanation=el("p"), trades=el("div"), context=el("div");
    panel.replaceChildren(el("h2",{text:"Expected-move reversal and last-hour puts"}),
      el("p",{text:s.status}),
      el("p",{text:`Reversal replay: ${s.reversal_sessions} selected sessions. Tail screen: ${s.screen_sessions} sessions (${s.screen_start}–${s.screen_end}), 20 worst final hours selected retrospectively; ${s.candidate_count} modeled combinations including IV stresses. These are separate universes.`}),
      el("h3",{text:"Long reversal · one modeled seven-day call"}),
      table(["Bound","Selloff veto","Costs","Trades","Net call P&L ($)","SPX points","MES arithmetic ($)"],s.comparisons.map(r=>[`${r.bound}σ`,r.veto?"On":"Off",r.scenario.startsWith("wide")?"Wide":"Base",r.trades,fmt(r.call_pnl),fmt(r.points),fmt(r.mes_illustration)])),
      el("p",{class:"muted",text:"MES column only multiplies SPX points by $5 and subtracts $5/trade. No actual futures fills, basis, roll or margin are modeled. Zero-trade results are not evidence of safety."}),
      el("div",{class:"run-nav"},[day,mode,bound]),canvas,explanation,context,trades,premium,
      el("h3",{text:"Hindsight put candidates · NOT verified option returns"}),
      table(["Day","OTM strike","Entry ET","Model cost ($)","Peak multiple","15:59 multiple","Next-minute 10× exit","Choices / losers at close"],s.events.map(e=>[e.date,e.strike,clock(e.entry_time),fmt(e.entry_cost),`${fmt(e.peak_ratio)}×`,`${fmt(e.close_ratio)}×`,e.target_ratio==null?"No executable target mark":`${fmt(e.target_ratio)}×`,`${e.tested_base_choices} / ${e.negative_close_choices}`])),
      el("p",{text:"Each displayed contract/entry is the best base-model peak for that day, selected with hindsight. Large multiples can reflect underpriced model premiums. A peak above 10× does not mean an achievable 10× fill; negative proceeds include the closing fee."}),
      el("h3",{text:"Saved CME futures snapshots · delayed, no automatic refresh"}),
      table(["Contract","Last","Change","As of","Delay"],s.futures.map(q=>[q.contract,fmt(q.last),fmt(q.change),q.source_time,q.delay])),
      ...s.futures.map(q=>el("p",{},[el("a",{href:q.source,text:`${q.contract} · CME source`,target:"_blank",rel:"noopener noreferrer"})])),
      el("details",{},[el("summary",{text:"Exact rules and evidence limits"}),...s.rules.concat(s.limitations).map(text=>el("p",{text}))]));
    async function render() {
      const ticket=++serial;
      const d=await api(`/api/v1/regime-study/tail-day-${day.value}`);
      if(!valid()||ticket!==serial) return;
      const tail=mode.value==="tail", all=d.bars;
      const bars=tail?all.slice(-90):all;
      const chosen=tail?[]:d.trades.filter(t=>t.veto&&t.scenario==="base_frozen_iv"&&t.bound===Number(bound.value));
      const markers=tail&&d.event?[{entry_time:d.event.entry_time,entry_spot:d.event.entry_spot,exit_time:d.event.peak_time,exit_spot:d.event.exit_spot}]:chosen;
      draw(canvas,bars,true,d.daily,markers,b=>{explanation.textContent=`${clock(b.time)} ET · O ${fmt(b.open)} H ${fmt(b.high)} L ${fmt(b.low)} C ${fmt(b.close)} · ${b.regime}${b.stress?" · Intraday shock veto latched":" · Prior daily regime"}.`;});
      explanation.textContent=`Prior regime: ${d.daily.regime}. ${d.daily.reason}. Daily −1σ ${fmt(d.daily.anchor-d.daily.expected_move)}, −2σ ${fmt(d.daily.lower2)}. Gray: BB20. Brown dashed: sigma bounds. E/X: ${tail?"hindsight-selected modeled put entry / best exit":"guarded long-call entry / exit"}.`;
      context.replaceChildren(...s.context.filter(c=>c.date===day.value).map(c=>el("p",{},[el("span",{text:c.text+" "}),el("a",{href:c.source,text:"Primary source",target:"_blank",rel:"noopener noreferrer"})])));
      trades.replaceChildren(...(tail&&d.event?[table(["IV / prior VIX","Model entry cost ($)","Hindsight peak multiple"],d.event.sensitivity.map(r=>[`${r.iv_scale}×`,fmt(r.entry_cost),`${fmt(r.peak_ratio)}×`]))]:[table(["Entry ET","Exit ET","Net call P&L ($)"],chosen.map(t=>[clock(t.entry_time),clock(t.exit_time),fmt(t.pnl)]))]));
      premium.hidden=!tail||!d.put_path.length;
      if(!premium.hidden) {
        const w=Math.max(330,panel.clientWidth),h=200,pad=45,path=d.put_path;
        premium.width=w*2;premium.height=h*2;premium.style.width="100%";premium.style.height="auto";
        const c=premium.getContext("2d");c.scale(2,2);c.fillStyle="#fafaf8";c.fillRect(0,0,w,h);
        const hi=Math.max(12,...path.map(p=>p.ratio)),lo=Math.min(0,...path.map(p=>p.ratio));
        const y=v=>h-pad-(v-lo)/(hi-lo)*(h-2*pad),x=t=>pad+(t-path[0].time)/(path.at(-1).time-path[0].time||1)*(w-2*pad);
        c.font="12px sans-serif";c.fillStyle="#333";c.fillText("Modeled proceeds / cost · hindsight contract",pad,20);
        c.strokeStyle="#888";c.setLineDash([4,4]);c.beginPath();c.moveTo(pad,y(10));c.lineTo(w-pad,y(10));c.stroke();c.setLineDash([]);c.fillText("10×",5,y(10));c.fillText(`${fmt(hi)}×`,5,y(hi));
        c.strokeStyle="#343434";c.beginPath();path.forEach((p,i)=>{if(i)c.lineTo(x(p.time),y(p.ratio));else c.moveTo(x(p.time),y(p.ratio));});c.stroke();
        c.fillText(clock(path[0].time),pad,h-12);c.fillText(clock(path.at(-1).time),w-pad-65,h-12);
      }
    }
    const refresh=()=>render().catch(()=>{if(valid())explanation.textContent="Saved session could not be loaded.";});
    day.onchange=mode.onchange=bound.onchange=refresh;
    await render();
  } catch {if(valid())panel.replaceChildren(el("p",{text:"Expected-move/tail report unavailable. Generate qte.reversal_tail_report to view it here."}));}
}
