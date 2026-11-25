"""Run four existing Book151 daily implementations on every captured ETF."""
from argparse import ArgumentParser
from pathlib import Path
import json
from book151_historical import BOOK_FAMILIES, COSTS, WINDOWS, day, run
from book151_timeframes import read_daily

def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--captures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    target=args.output
    target.mkdir(exist_ok=False)
    data=args.captures
    results={}
    # The source audit identified OHLC bound violations in these exact captures.
    excluded={'IWM':['2023-06-05'],'XLB':['2018-11-15'],
              'XLE':['2018-11-15','2023-06-05'],'XLF':['2023-06-05'],'XLI':['2023-06-05']}
    (target/'excluded.json').write_text(json.dumps(excluded,indent=2))
    for path in sorted(data.glob('*.json')):
        if path.stem in excluded:
            continue
        bars,digest=read_daily(path,path.stem)
        results[path.stem]={'source_sha256':digest,'windows':{}}
        for split,first,last in [*WINDOWS[:2],('recent_retrospective','2024-01-01','2026-11-21')]:
            days=[b for b in bars if first<=day(b.end_ns)<last]
            warm=[b for b in bars if day(b.end_ns)<first][-50:]
            if len(warm)!=50:
                raise ValueError('Insufficient warmup')
            cases={}
            for family in (*BOOK_FAMILIES,'buy_hold'):
                cases[family]={cost:run(warm+days,days[0].end_ns,days[-2].end_ns,family,'ungated',costs,{})
                               for cost,costs in COSTS.items()}
            results[path.stem]['windows'][split]=cases
        print(path.stem,flush=True)
    (target/'results.json').write_text(json.dumps(results,allow_nan=False,separators=(',',':')))
    lines=['# Four Book151 daily implementations on Schwab ETF history','',
           f'{30*len(results)} native runs, {len(results)} complete ETF histories, three chronological windows, base and stressed costs, and matched 10% buy-and-hold. The inputs are Schwab observed daily prices. Dividends and historical adjustment vintages are unverified. All three windows are retrospective.','',
           '| ETF | Rule | Dev stress $ | Validation stress $ | Recent stress $ | Recent hold $ | Trades |',
           '|---|---|---:|---:|---:|---:|---:|']
    passing=[]
    for symbol, entry in results.items():
        windows=list(entry['windows'].values())
        for f in BOOK_FAMILIES:
            d,v,r=[w[f]['stress']['summary'] for w in windows]
            hold=windows[-1]['buy_hold']['stress']['summary']['pnl']
            lines.append(f"| {symbol} | {f} | {d['pnl']:.2f} | {v['pnl']:.2f} | {r['pnl']:.2f} | {hold:.2f} | {r['closed_trades']} |")
            if v['pnl']>0 and r['pnl']>hold and r['pnl']>0 and r['closed_trades']>=30 and r['pnl_without_best_exit_day']>0 and windows[-1][f]['base']['summary']['pnl']>0:
                passing.append((symbol,f,r['pnl'],hold,r['closed_trades']))
    lines[2:2]=[f'**{len(passing)} of {4*len(results)} pairs passed the daily screen.**','']
    lines+=['','Excluded entire IWM, XLB, XLE, XLF and XLI histories due to inconsistent source OHLC. No bars were repaired.','', 'Screen: positive stressed validation, positive recent base/stress and ex-best exit P&L, ≥30 recent closed trades, and recent stressed P&L above buy-and-hold. Passing is a research screen.']
    (target/'report.md').write_text('\n'.join(lines)+'\n')
    (target/'passing.json').write_text(json.dumps(passing,indent=2))

if __name__=='__main__':
    main()
