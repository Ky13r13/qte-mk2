"""Self-contained report for frozen minute-rule comparisons."""
import html
import json


def render_report(protocol, frozen, report, sessions):
    parts = ['<!doctype html><meta charset="utf-8"><title>QTE Minute Refinement</title>',
             '<style>body{font:15px system-ui;max-width:1100px;margin:32px;color:#222;background:#fafafa}table{border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ccc;text-align:right}td:first-child,th:first-child{text-align:left}canvas{border:1px solid #aaa;width:100%;height:auto}select{padding:7px;border-radius:0}p{max-width:950px}</style>',
             '<h1>Schwab minute research</h1>',
             f"<p>{frozen['trial_count']} development configurations. {len(protocol['development_dates'])} development dates and {len(protocol['evaluation_dates'])} later dates. Fixed five-minute holds; cash remains the operational policy.</p>",
             '<h2>Frozen rules on later dates</h2><table><tr><th>Rule</th><th>Variant</th><th>Threshold</th><th>Regime filter</th><th>Base $</th><th>Stress $</th><th>Delayed stress $</th><th>Trades</th><th>Failed checks</th></tr>']
    cases = []
    for family, comparisons in report.items():
        for name, item in comparisons.items():
            a = item['evaluation']
            p = item['policy']
            label = f'{family} {name}'
            values = [family, name, f"{p['threshold']:.3f}", 'On' if p['gated'] else 'Off',
                      *(f"{a[c]['combined']['net_pnl']:.2f}" for c in ('base','stress','stress_delayed')),
                      str(a['stress_delayed']['combined']['trades']),
                      ', '.join(f.replace('_',' ') for f in item['failures']) or 'None']
            parts.append('<tr>'+''.join(f'<td>{html.escape(v)}</td>' for v in values)+'</tr>')
            if name == 'refined':
                for symbol, result in item['_trades']['stress_delayed']['symbols'].items():
                    dates = sorted({row['day'] for row in result['trades']})
                    for day in dates:
                        bars = sessions[symbol][day]
                        clock = {b.start_ns:i for i,b in enumerate(bars)}
                        trades = [row for row in result['trades'] if row['day']==day]
                        cases.append(dict(label=f'{label} · {symbol} · {day}',
                            bars=[[b.open,b.high,b.low,b.close] for b in bars],
                            fills=[[clock[t['entry_ns']],clock[t['exit_ns']],t['sign'],round(t['net_pnl'],2)] for t in trades]))
    parts += ['</table><p>ETF figures combine independent $10,000 sleeves on a shared calendar. Short benchmark fills omit borrow availability and financing. Fixed rules use the declared starting thresholds.</p>',
        '<h2>Modeled American option sensitivity</h2><table><tr><th>Rule</th><th>Variant</th><th>Scenario</th><th>Model P&amp;L $</th><th>Contracts</th><th>Debit-cap skips</th></tr>']
    for family, comparisons in report.items():
        for name, item in comparisons.items():
            models = tuple(item['option_models'].values())
            for scenario in protocol['option_scenarios']:
                pnl = sum(p[scenario]['model_pnl'] for p in models)
                count = sum(p[scenario]['trades'] for p in models)
                skipped = sum(p[scenario]['rejected_by_debit_cap'] for p in models)
                parts.append(f'<tr><td>{html.escape(family)}</td><td>{name}</td><td>{html.escape(scenario)}</td><td>{pnl:.2f}</td><td>{count}</td><td>{skipped}</td></tr>')
    parts += ['</table><p>Hypothetical seven-day calls and puts on the delayed ETF clock. Historical option quotes and contemporaneous implied volatility were unavailable. Model P&amp;L cannot validate execution.</p>']
    if cases:
        parts += ['<h2>Review recorded trades</h2><label>Session <select id="session"></select></label>',
                  '<p id="results"></p><canvas id="chart" width="1040" height="300" role="img" aria-label="Intraday candlesticks and trade intervals"></canvas>',
                  '<script>const cases='+json.dumps(cases,allow_nan=False)+';', r'''
const select=document.getElementById('session'), canvas=document.getElementById('chart'), ctx=canvas.getContext('2d');
cases.forEach((item,i)=>select.add(new Option(item.label,i)));
function draw(){
 const item=cases[Number(select.value)], bars=item.bars;
 const low=Math.min(...bars.map(b=>b[2])), high=Math.max(...bars.map(b=>b[1]));
 const x=i=>52+i*967/(bars.length-1), y=v=>267-(v-low)*232/(high-low||1);
 ctx.clearRect(0,0,1040,300);ctx.font='12px system-ui';ctx.fillStyle='#333';
 ctx.fillText(high.toFixed(2),5,35);ctx.fillText(low.toFixed(2),5,270);
 ctx.fillText('09:30 ET',52,289);ctx.fillText('15:59 ET',960,289);
 for(const [a,b,sign,pnl] of item.fills){ctx.fillStyle=pnl>=0?'#c5d8d5':'#ead0c7';ctx.fillRect(x(a),27,Math.max(2,x(b)-x(a)),245);}
 bars.forEach(([o,h,l,c],i)=>{ctx.strokeStyle='#444';ctx.beginPath();ctx.moveTo(x(i),y(h));ctx.lineTo(x(i),y(l));ctx.stroke();ctx.fillStyle=c>=o?'#aaa':'#222';ctx.fillRect(x(i)-1,Math.min(y(o),y(c)),2,Math.max(1,Math.abs(y(o)-y(c))));});
 document.getElementById('results').textContent=item.fills.map(([a,b,s,p])=>`${s>0?'Long':'Short'} $${p.toFixed(2)}`).join(' · ');
}
select.addEventListener('change',draw);draw();</script>''']
    return '\n'.join(parts)
