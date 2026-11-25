"""Post-selection attribution for the previously observed positive 10:30 put."""
from pathlib import Path
import json

from spx_0dte_timing_study import BOUNDARY, load, study, time
from qte.spx_0dte_timing import SCENARIOS

ARCHIVE=Path('build/spx-0dte-timing')
OUT=ARCHIVE/'exploratory-attribution'


def main():
    sessions=json.loads(Path('build/daily-regimes/year-20261120/sessions.json').read_bytes())
    sessions=[r for r in sessions if r['status']=='completed_cash_session']
    splits={k:[r for r in sessions if (r['date']>=BOUNDARY)==(k=='later')] for k in ('development','later')}
    bars, sources=load(ARCHIVE/'raw',5)
    minute, fine_sources=load(ARCHIVE/'minute',1)
    choice=('confirmed_put',630,.30,10)
    OUT.mkdir(exist_ok=False)
    outputs={label:{cost:study(rows,bars,5,choice,cost,True) for cost in SCENARIOS}
             for label,rows in splits.items()}
    observed_later=outputs['later']['wide']['ledger']
    selected_dates={t['date'] for t in observed_later}
    pool=[r for r in splits['later'] if r['date'] in selected_dates]
    alternate_times={time(t):study(pool,bars,5,('confirmed_put',t,.30,10),'wide',True)
                     for t in (615,625,630,635,645)}
    # Ungated same direction is an explicit negative control.
    ungated=study(splits['later'],bars,5,('unconditional_put',630,.30,10),'wide',True)
    fine=study(splits['later'],minute,1,choice,'wide',True)
    attribution=[]
    for t in observed_later:
        e=t['entry_minute']; x=t['exit_minute']; d=t['date']
        start=bars[d][e].open
        end=bars[d][x].open
        attribution.append(dict(t,underlying_move_bps=10000*(end/start-1)))
    risk=sum(t['debit_dollars'] for t in observed_later)
    (OUT/'details.json').write_text(json.dumps(dict(choice=choice,outputs=outputs,ungated=ungated,
        adjacent_times=alternate_times,fine=fine,attribution=attribution,sources={**sources,**fine_sources}),
        indent=2,allow_nan=False)+'\n')
    lines=['# SPXW 10:30 ET bearish-continuation attribution','',
        'Exploratory and selected after reviewing later outcomes. The fills below are option model outputs on observed SPX paths. Equivalent live premiums and fillability are unknown.',
        '', '| Scenario | Earlier P&L | Later P&L | Later ex best | Later trades |',
        '|---|---:|---:|---:|---:|']
    for c in SCENARIOS:
        a,b=outputs['development'][c],outputs['later'][c]
        lines.append(f"| {c} | {a['net_pnl']:.2f} | {b['net_pnl']:.2f} | {b['ex_best']:.2f} | {b['trades']} |")
    lines += ['', f"Later wide case reserved ${risk:,.2f} cumulative debits for five separate one-contract trades. P&L / total debit was {outputs['later']['wide']['net_pnl']/risk:.1%}; this is not an account return.",
        '', '| Date | Prior regime | SPX move to exit (bps) | Debit | P&L | Exit trigger |',
        '|---|---|---:|---:|---:|---|']
    for t in attribution:
        lines.append(f"| {t['date']} | {t['prior_regime']} | {t['underlying_move_bps']:.1f} | {t['debit_dollars']:.2f} | {t['pnl']:.2f} | {t['exit_reason']} |")
    lines+=['',f"Buying the same OTM put at 10:30 with no directional or regime filter returned ${ungated['net_pnl']:,.2f} across {ungated['trades']} later opportunities.",
            f"Available later one-minute panel, same filtered rule and wide costs: ${fine['net_pnl']:,.2f} on {fine['trades']} trades. This overlaps the five-minute sample.",
            '', 'Nearby time controls on the same five selected dates. Gates are reevaluated each time, so both the trade count and entry path may change.',
            '', '| Time (ET) | Trades | Wide P&L |','|---|---:|---:|']
    for clock,row in alternate_times.items():
        lines.append(f"| {clock} | {row['trades']} | {row['net_pnl']:.2f} |")
    lines+=['','Rule: prior downtrend or stress, plus declining completed SPX closes over the last 15 and 30 minutes. Buy the 0.2%-or-further OTM same-day put. Reject an already-beyond-2-sigma entry unless prior regime is stress. Net 30% target, 35% stop trigger, and up to 10 minutes. Bid gap and cost assumptions are identical to the timing study.',
        '', 'Treat this as a hypothesis for fresh bid/ask sessions. A sparse winner selected after inspection does not establish repeatability.']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':
    main()
