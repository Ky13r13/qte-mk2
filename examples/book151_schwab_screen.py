"""Fixed two-ETF intraday adaptations of 20 named PDF sections."""
from argparse import ArgumentParser
from pathlib import Path
from statistics import fmean, pstdev, correlation
import json
import math

from qte.adapters.schwab_research import read_full_minute_sessions
from qte.minute_replay import COSTS, dataset, replay, summarize
from qte.strategies.minute_signals import features

RULES = {
 '3.1': 'Follow own 20-bar return',
 '3.4': 'Buy the lower realized volatility ETF over 20 returns',
 '3.7': 'Follow 20-bar residual momentum using beta fitted to prior 40 paired returns',
 '3.8': 'Fade a 2-sigma log-price ratio deviation against its previous 20 readings',
 '3.9': 'Fade the 20-bar return relative to the two-ETF cluster mean',
 '3.11': 'Follow close versus preceding SMA21',
 '3.12': 'Follow SMA5 versus SMA20',
 '3.13': 'Trade ordered SMA3, SMA10 and SMA21 in either direction',
 '3.14': 'Fade touches of preceding 20-bar support or resistance with candle confirmation',
 '3.15': 'Fade closes breaking the prior 20-close channel',
 '4.1': 'Buy the ETF with stronger 20-bar momentum',
 '4.1.1': 'Buy the 20-bar momentum leader above SMA50',
 '4.1.2': 'Buy the 60-bar momentum leader only if its absolute return is positive',
 '4.2': 'Buy the ETF with greater 20-bar return divided by 20-bar volatility',
 '4.3': 'Buy the ETF with the stronger positively sloped linear fit over 20 closes',
 '4.4': 'Fade internal bar strength below 0.2 or above 0.8',
 '4.6': 'Follow aligned 20- and 60-bar returns independently',
 '10.3': 'Fade cross-sectional relative momentum over 60 bars',
 '10.3.1': 'Fade relative 60-bar momentum when volume exceeds 1.5 times prior 20-bar mean',
 '10.4': 'Follow own 60-bar return with a 10 bp deadband',
}

def sign(x):
    return (x > 0) - (x < 0)

def returns(p):
    return [math.log(b/a) for a,b in zip(p,p[1:])]

def fit(prices):
    if min(prices) == max(prices):
        return 0
    r = correlation(range(len(prices)),prices)
    return r*r if r>0 else 0

def signals(bars, peer, i):
    current = bars[i]
    p = [b.close for b in bars[i-60:i+1]]
    q = [b.close for b in peer[i-60:i+1]]
    a,b = returns(p),returns(q)
    m,n = sum(a[-20:]),sum(b[-20:])
    long_m,long_n = sum(a),sum(b)
    av,bv = pstdev(a[-20:]),pstdev(b[-20:])
    prior_a,prior_b = a[:40],b[:40]
    delta = fmean(prior_b)
    variance = sum((x-delta)**2 for x in prior_b)
    beta = sum((x-fmean(prior_a))*(y-delta) for x,y in zip(prior_a,prior_b)) / variance if variance else 0
    ratios = [math.log(x/y) for x,y in zip(p[-21:],q[-21:])]
    d = ratios[-1]-fmean(ratios[:-1])
    z = pstdev(ratios[:-1])
    fast,middle,slow = (fmean(p[-j:]) for j in (3,10,21))
    prior = bars[i-20:i]
    ibs = (current.close-current.low)/(current.high-current.low) if current.high>current.low else .5
    support = current.low <= min(x.low for x in prior) and current.close>current.open
    resistance = current.high >= max(x.high for x in prior) and current.close<current.open
    return dict(zip(RULES, (
        sign(m), int(av<bv), sign(m-beta*n) if variance else 0,
        -sign(d) if z and abs(d)>=2*z else 0, -sign(m-n),
        sign(p[-1]-fmean(p[-22:-1])), sign(fmean(p[-5:])-fmean(p[-20:])),
        int(fast>middle>slow)-int(fast<middle<slow), int(support)-int(resistance),
        int(p[-1]<min(p[-21:-1]))-int(p[-1]>max(p[-21:-1])),
        int(m>n), int(m>n and p[-1]>fmean(p[-50:])), int(long_m>max(0,long_n)),
        int(av>0 and bv>0 and m/av>n/bv), int(fit(p[-20:])>fit(q[-20:])),
        int(ibs<=.2)-int(ibs>=.8), sign(m) if m*long_m>0 else 0,
        -sign(long_m-long_n),
        -sign(long_m-long_n) if current.volume>1.5*fmean(x.volume for x in prior) else 0,
        sign(long_m) if abs(long_m)>=.001 else 0,
    ), strict=True))

def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--captures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    book=json.loads(Path('docs/strategies/book151-catalog.json').read_text())
    names={x['section']:x['title'] for x in book['entries']}
    symbols=('SPY','QQQ')
    loaded={s:read_full_minute_sessions(args.captures/f'{s}-minute-20261012-20261120.json',s) for s in symbols}
    sessions={s:x[0] for s,x in loaded.items()}
    dates=tuple(sessions['SPY'])
    if len(dates)!=30 or tuple(sessions['QQQ'])!=dates:
        raise ValueError('Expected 30 matched full sessions')
    splits={'development':dates[5:20],'later':dates[20:]}
    out=args.output
    out.mkdir(parents=True,exist_ok=False)
    (out/'protocol.json').write_text(json.dumps(dict(rules=RULES,splits=splits,warmup=dates[:5],costs=COSTS,
        capture_sha256={s:loaded[s][1] for s in symbols},source_pdf_sha256=book['sha256']),indent=2))
    result={section:{} for section in RULES}
    for split, days in splits.items():
        cases={}
        for symbol, other in (symbols,symbols[::-1]):
            selected={d:sessions[symbol][d] for d in days}
            prefix={d:b for d,b in sessions[symbol].items() if d<=days[-1]}
            minutes={d:tuple(m for m in obs if m.index>=60) for d,obs in features(prefix).items() if d in selected}
            timeline={d:{m.index:signals(selected[d],sessions[other][d],m.index) for m in minutes[d]} for d in days}
            cases[symbol]=(selected,minutes,timeline,dataset(symbol,selected))
        for section in RULES:
            result[section][split]={}
            for cost in COSTS:
                trades=[]
                for symbol,(selected,minutes,timeline,data) in cases.items():
                    lookup={id(m):timeline[d][m.index][section] for d in days for m in minutes[d]}
                    rows,_=replay(data,selected,minutes,section,cost,signal=lambda minute,_:lookup[id(minute)])
                    trades.extend(dict(t,symbol=symbol) for t in rows)
                result[section][split][cost]=dict(metrics=summarize(days,trades),trades=trades)
    (out/'ledger.json').write_text(json.dumps(result,allow_nan=False,separators=(',',':')))
    def metric(section,split,cost):
        return result[section][split][cost]['metrics']
    order=sorted(RULES,key=lambda s:min(metric(s,'development',c)['net_pnl'] for c in COSTS),reverse=True)
    rows=['# 20 strategies from the book on Schwab minute data','',
          '120 native runs per split, 240 total. Fixed rules on 15 development dates and 10 later dates. Five leading sessions supply warmup. The table ranks candidates by worst development P&L across costs. Later results do not affect that ranking. Dates overlap earlier research.','',
          'These are intraday adaptations, including two-ETF substitutes for broad rotations and futures signals. Independent $10,000 target sleeves, one position per symbol, five-minute holds and five entries per symbol per day. Short borrow and locate are unmodeled. There is no historical options quote series here.','',
          '| Section | Rule | Dev worst $ | Later base $ | Later stressed + delay $ | Later gross $ | Trades | After-cost diagnosis |',
          '|---|---|---:|---:|---:|---:|---:|---|']
    for s in order:
        d=min(metric(s,'development',c)['net_pnl'] for c in COSTS)
        base=metric(s,'later','base')['net_pnl']
        x=metric(s,'later','stress_delayed')
        net,gross=x['net_pnl'],x['gross_open_pnl']
        reason=('positive after cost, further validation required' if net>0 else
                'no trades' if not x['trades'] else 'direction lost before costs' if gross<=0 else 'gross gain erased by costs')
        rows.append(f"| {s} | {names[s]} | {d:.2f} | {base:.2f} | {net:.2f} | {gross:.2f} | {x['trades']} | {reason} |")
    rows.extend(['','## Exact adaptations',''])
    rows.extend(f'- **{s}** {rule}.' for s,rule in RULES.items())
    (out/'report.md').write_text('\n'.join(rows)+'\n')

if __name__=='__main__':
    main()
