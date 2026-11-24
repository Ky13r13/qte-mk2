"""Frozen timing screen on observed SPX paths, with modeled option prices."""
import argparse
from collections import Counter
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
from statistics import median

from qte.spx_0dte_bankroll import rolling_windows
from qte.spx_0dte_timing import FAMILIES, HOLDS, SCENARIOS, SLOTS, STOP, TARGETS, decode_capture, simulate

BOUNDARY='2026-08-01'
MIN_TRADES=20


def save(path, obj):
    path.write_text(json.dumps(obj,allow_nan=False,indent=2)+'\n')


def load(directory,step):
    days, sources={}, {}
    for path in sorted(directory.glob('*.json')):
        raw=path.read_bytes()
        sources[str(path)]=sha256(raw).hexdigest()
        for day, rows in decode_capture(json.loads(raw),step).items():
            dest=days.setdefault(day,{})
            for minute,b in rows.items():
                if minute in dest and dest[minute]!=b:
                    raise ValueError('Conflicting index observation')
                dest[minute]=b
    return days,sources


def eligible(session,bars,step):
    day=session['date']
    cutoff=int(session['close_et'][:2])*60
    rows=bars.get(day,{})
    grid=set(range(570,cutoff,step))
    regular={k:v for k,v in rows.items() if k in grid}
    prior=session.get('prior_known_regime')
    if set(regular)!=grid or not prior or prior['asof']>=day:
        return None
    return regular,cutoff,prior


def study(sessions,bars,step,config,scenario,with_trades=False):
    family,slot,target,hold=config
    counts=Counter()
    trades=[]
    for session in sessions:
        inputs=eligible(session,bars,step)
        if inputs is None:
            counts['missing_grid_or_prior_vix']+=1
            continue
        status,trade=simulate(inputs[0],step,inputs[1],inputs[2],family,slot,target,hold,scenario)
        counts[status]+=1
        if trade is not None:
            trades.append(dict(trade,date=session['date'],prior_regime=inputs[2]['regime']))
    gains=[t['pnl'] for t in trades]
    net=sum(gains)
    sums=dict(net_pnl=net,per_session=net/len(sessions) if sessions else 0.,
              median_trade=median(gains) if gains else None, trades=len(trades),
              positive=sum(x>0 for x in gains), hit_target=sum(t['net_return']>=target for t in trades),
              ex_best=net-max([0.,*gains]),worst=min(gains) if gains else None, counts=dict(counts))
    if with_trades:
        sums['ledger']=trades
    return sums


def key(config):
    f,t,target,hold=config
    return f'{f}-{t//60:02d}{t%60:02d}-{int(target*100)}-{hold}m'


def screen(rows):
    return all(m['net_pnl']>0 and m['ex_best']>0 and m['trades']>=MIN_TRADES for m in rows.values())


def score(rows):
    return min(r['per_session'] for r in rows.values())


def time(slot):
    return f'{slot//60:02d}:{slot%60:02d}'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw',type=Path,required=True)
    p.add_argument('--minute',type=Path,required=True)
    p.add_argument('--calendar',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    out=args.output
    out.mkdir(parents=True,exist_ok=False)
    c_raw=args.calendar.read_bytes()
    calendar=json.loads(c_raw)
    all_sessions=[r for r in calendar if r['status']=='completed_cash_session']
    design=[r for r in all_sessions if r['date']<BOUNDARY]
    later=[r for r in all_sessions if r['date']>=BOUNDARY]
    bars, sources=load(args.raw,5)
    minute, fine_sources=load(args.minute,1)
    configs=list(product(FAMILIES,SLOTS,TARGETS,HOLDS))
    protocol=dict(boundary=BOUNDARY,design_dates=[r['date'] for r in design],later_dates=[r['date'] for r in later],
        configs=[list(x) for x in configs],cost_models=SCENARIOS,stop=STOP,min_trades=MIN_TRADES,
        calendar_sha256=sha256(c_raw).hexdigest(),sources={**sources,**fine_sources},
        note='OHLC-derived European model marks, not historical option prices. Reused market observations, retrospective chronology. All risk and thresholds fixed.')
    save(out/'protocol.json',protocol)
    dev={key(c):{s:study(design,bars,5,c,s) for s in SCENARIOS} for c in configs}
    possible=[c for c in configs if all(v['trades']>=MIN_TRADES for v in dev[key(c)].values())]
    if not possible:
        raise ValueError('No candidate has complete development coverage across stresses')
    winner=max(possible,key=lambda c:(screen(dev[key(c)]),score(dev[key(c)])))
    controls={slot:('call',slot,.50,5) for slot in SLOTS}
    fine_controls={slot:('low_vol_call',slot,.50,5) for slot in SLOTS}
    save(out/'frozen.json',dict(candidate=winner,passes_development=screen(dev[key(winner)]),
          purpose='fixed research candidate or least-bad reference; operational policy cash',
          evaluation_controls=[*controls.values(),*fine_controls.values()]))
    print('Frozen '+key(winner),flush=True)
    evaluation={key(c):{s:study(later,bars,5,c,s) for s in SCENARIOS} for c in configs}
    fine_requested={p.stem for p in args.minute.glob('*.json')}
    fine_days=[d for d in later if d['date'] in fine_requested]
    minute_check={key(c):{s:study(fine_days,minute,1,c,s) for s in SCENARIOS}
        for c in dict.fromkeys([winner,*controls.values(),*fine_controls.values()])}
    # Use one frozen slot, preserve calendar gaps and known no-trade dates, and
    # keep post-selection samples separate. A missing grid is deliberately absent.
    bankroll={}
    for s in ('base','wide','delayed'):
        rows=study(later,bars,5,winner,s,True)
        trades={t['date']:t for t in rows['ledger']}
        path={}
        for d in calendar:
            if d['date']<BOUNDARY:
                continue
            day=d['date']
            if d['status']=='closed':
                path[day]=None
            else:
                inp=eligible(d,bars,5)
                if inp is not None:
                    path[day]=trades.get(day)
        bankroll[s]=rolling_windows(BOUNDARY,calendar[-1]['date'],path)
    save(out/'results.json',dict(development=dev,later=evaluation,minute_later=minute_check,bankroll=bankroll))
    base=study(later,bars,5,winner,'base',True)
    save(out/'frozen-later-ledger.json',base)
    completed=lambda rows,feed,step:sum(eligible(r,feed,step) is not None for r in rows)
    lines=['# SPXW 0DTE buy-time and account study','',
           'The account target is $50,000 from $10,000 in 30 calendar days. The study compares opening times and execution sensitivity. It does not support a promise of that return.',
           '',f'{len(configs)} fixed configurations. Five-minute SPX grids with prior-known VIX: {completed(design,bars,5)}/{len(design)} development and {completed(later,bars,5)}/{len(later)} later sessions. Later targeted minute panel: {completed(fine_days,minute,1)}/{len(fine_days)} complete. Time zone ET. Cboe SPXW expiry cutoff 16:00, or 13:00 on scheduled early closes.',
           '',f'Frozen candidate: **{key(winner)}**. Development screen: **{screen(dev[key(winner)])}**. Later screen: **{screen(evaluation[key(winner)])}**. No further selection based on later results.',
           '', '## Half-hour starts and the 15:50 slot', '',
           'Fixed control: buy a call at least 0.2% OTM rounded away on a 5-point strike grid. Five-minute hold, +50% net target, −35% stop trigger. Both triggers use the previous completed bar and execute against the next modeled bid. Missing history and abstentions count as zero in per-session comparisons.',
           '', '| ET decision | Later 5m-grid trades | Base P&L | Wide P&L | Delayed P&L | Later 1m-grid trades | 1m wide P&L | 1m low-vol call trades / wide P&L |',
           '|---|---:|---:|---:|---:|---:|---:|---|']
    for slot,c in controls.items():
        a=evaluation[key(c)]; b=minute_check[key(c)]
        z=minute_check[key(fine_controls[slot])]['wide']
        late=a['delayed']
        lines.append(f"| {time(slot)} | {a['base']['trades']} | {a['base']['net_pnl']:.2f} | {a['wide']['net_pnl']:.2f} | {late['net_pnl']:.2f} ({late['trades']}) | {b['wide']['trades']} | {b['wide']['net_pnl']:.2f} | {z['trades']} / {z['net_pnl']:.2f} |")
    lines += ['', '09:30 has no completed same-session observation for causal strike selection. A five-minute delayed 15:50 entry cannot complete the full five-minute hold before expiry; zero trades in that cell is missing execution opportunity. The one-minute panel resolves that clock more finely but still uses model quotes.',
              '', '## Frozen candidate across execution assumptions','',
              '| Scenario | Development P&L | Later P&L | Later trades | Net target hits | Later without best day |',
              '|---|---:|---:|---:|---:|---:|']
    for s in SCENARIOS:
        u,v=dev[key(winner)][s],evaluation[key(winner)][s]
        lines.append(f"| {s} | {u['net_pnl']:.2f} | {v['net_pnl']:.2f} | {v['trades']} | {v['hit_target']} | {v['ex_best']:.2f} |")
    lines += ['', '## 30-calendar-day account paths', '',
        'One frozen slot per day; whole contracts, no leverage, initial $10,000 each overlapping window. Complete windows only. Drawdowns are closed-equity drawdowns; intratrade drawdown can be larger. Counts are descriptive, not probabilities.',
        '', '| Scenario | Debit budget | Complete windows | Hit $50k | Best ending equity | Worst closed drawdown |',
        '|---|---:|---:|---:|---:|---:|']
    for s,report in bankroll.items():
        for row in report['scenarios']:
            x=row['summary']
            lines.append(f"| {s} | {row['debit_fraction']:.0%} | {x['complete_window_count']} | {x['target_hit_windows']} | {x['best_terminal_equity_dollars']} | {x['worst_closed_equity_drawdown_fraction']} |")
    lines += ['', '## Limitations and decisions','',
        '- Pricing uses QTE Black-76, prior daily VIX as a proxy for intraday annual IV, zero carry and a remaining RTH variance clock. Higher-entry-IV and IV-crush scenarios probe misspecification. VIX is not the traded contract’s IV.',
        '- Modeled spreads are $0.10 or $0.25 adverse per side, $1.25 or $2.50 fees per side, conservative $0.10 price ticks and a $0.20 pre-spread minimum premium. Exit losses can exceed the modeled stop; an unresolved exit reserves the full entry debit.',
        '- Baseline orders use the next open at a completed-bar boundary. Delayed stress adds one full observed bar before entry. No favorable excursion inside an OHLC candle is credited.',
        '- The low-vol call rule uses prior VIX below 15; confirmation uses earlier 15- and 30-minute index moves and the prior-known regime. These are transparent hypotheses, not dealer inventory estimates.',
        '- The observed option quote pilot is separate. A single, truncated option day cannot establish the average return of a buy time. Historical bid/ask, size and publication/receipt clocks across representative options sessions remain the key missing evidence.',
        '- Existing strategies and results were preserved. The new timing study reuses the year archive, QTE pricing and native capture conventions. No route was removed.']
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    print(key(winner),screen(dev[key(winner)]),screen(evaluation[key(winner)]),flush=True)

if __name__=='__main__':
    main()
