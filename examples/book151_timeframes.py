"""Chronological 20-rule comparison on Schwab candles, using QTE fills."""
from argparse import ArgumentParser
from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
from types import SimpleNamespace

import qte
from book151_historical import make_bars
from book151_schwab_screen import RULES, signals
from qte.adapters.schwab_research import NY, read_full_minute_sessions
from qte.minute_replay import COSTS

SYMBOLS = ('SPY','QQQ')
CLOCKS = (1,5,10,15,30,'daily','weekly','monthly')
HOLD = 5
SLEEVE = 10_000
CASH = 100_000


def read_daily(path, symbol):
    raw = path.read_bytes()
    doc = json.loads(raw)
    response = doc['response']
    canonical = json.dumps(response,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if (doc['provider'] != 'schwab' or response['symbol'] != symbol or response['empty']
            or doc['request']['query_parameters']['frequencyType'] != 'daily'
            or sha256(canonical).hexdigest() != doc['response_canonical_json_sha256']):
        raise ValueError(f'Invalid Schwab daily capture: {symbol}')
    rows = []
    previous = ''
    for row in response['candles']:
        d = datetime.fromtimestamp(row['datetime']/1000, NY).date().isoformat()
        if d <= previous:
            raise ValueError('Daily sessions are not strictly ordered')
        previous = d
        if not '2015-01-01' <= d <= '2026-11-20':
            continue
        o,h,l,c,v=(row[k] for k in ('open','high','low','close','volume'))
        if not all(math.isfinite(x) for x in (o,h,l,c,v)) or not 0 < l <= min(o,c) <= max(o,c) <= h or v < 0:
            raise ValueError('Invalid daily OHLCV')
        rows.append(dict(row,date=d))
    return make_bars(symbol,rows),sha256(raw).hexdigest()


def day(bar):
    return datetime.fromtimestamp(bar.start_ns/1e9,NY).date().isoformat()


def aggregate(bars, clock):
    groups = defaultdict(list)
    for i,b in enumerate(bars):
        d=day(b)
        if type(clock) is int:
            local=datetime.fromtimestamp(b.start_ns/1e9,NY)
            key=d,(local.hour*60+local.minute-570)//clock
        elif clock == 'daily':
            key=d
        elif clock == 'weekly':
            key=tuple(date.fromisoformat(d).isocalendar()[:2])
        else:
            key=d[:7]
        groups[key].append(i)
    result=[]
    for key, ids in groups.items():
        if clock == 'monthly' and key == '2026-11':
            continue  # Current month is incomplete at the capture cutoff.
        first,last=ids[0],ids[-1]
        rows=bars[first:last+1]
        if type(clock) is int and len(rows)!=clock:
            raise ValueError('Incomplete intraday aggregate')
        result.append(SimpleNamespace(first=first,last=last,day=day(rows[-1]),
            open=rows[0].open,high=max(b.high for b in rows),low=min(b.low for b in rows),
            close=rows[-1].close,volume=sum(b.volume for b in rows)))
    return result


class Orders(qte.Strategy):
    def __init__(self, events):
        super().__init__()
        self.events=events

    def on_bar(self, ctx, b):
        if ctx.now_ns in self.events:
            sign,quantity=self.events[ctx.now_ns]
            ctx.submit_order(qte.market_order(b.symbol,qte.OrderSide.BUY if sign>0 else qte.OrderSide.SELL,quantity))


def compute(bars, groups, slate, data, section, first, last, clock, cost):
    delay,costs=COSTS[cost]
    events={}
    trades=[]
    busy=0
    counts=defaultdict(int)
    for i in range(60,len(groups)-HOLD-1):
        bar=groups[i]
        if not first <= bar.day < last or i<busy or counts[bar.day]>=5:
            continue
        sign=slate[i][section]
        if not sign:
            continue
        ent,ex=groups[i+1].first+delay,groups[i+1+HOLD].first+delay
        if ex>=len(bars) or day(bars[ex])>=last or (type(clock) is int and day(bars[ex])!=bar.day):
            continue
        quantity=math.floor(SLEEVE/bar.close)
        if quantity==0:
            continue
        events[bars[ent-1].end_ns]=sign,quantity
        events[bars[ex-1].end_ns]=-sign,quantity
        trades.append((ent,ex,sign,quantity))
        busy=i+1+HOLD
        counts[bar.day]+=1
    r=qte.BacktestEngine(qte.BacktestConfig(CASH,risk_limits=qte.RiskLimits(allow_short=True),
        execution_costs=qte.ExecutionCosts(**costs),history_capacity=1,build_identity='book151-schwab-clocks')).run(data,Orders(events))
    if len(r.fills)!=2*len(trades) or r.open_trades or any(o.status==qte.OrderStatus.REJECTED for o in r.orders):
        raise AssertionError('Order schedule did not reconcile')
    ledger=[]
    for (ent,ex,sign,q),a,b in zip(trades,r.fills[::2],r.fills[1::2],strict=True):
        if (a.effective_ns,b.effective_ns)!=(bars[ent].start_ns,bars[ex].start_ns) or a.quantity!=q or b.quantity!=q:
            raise AssertionError('Unexpected fill clock or quantity')
        gross=sign*q*(bars[ex].open-bars[ent].open)
        net=sign*q*(b.executed_price-a.executed_price)-a.commission-b.commission
        ledger.append(dict(symbol=bars[ent].symbol,exit_date=day(bars[ex]),entry_ns=a.effective_ns,exit_ns=b.effective_ns,
                           direction=sign,quantity=q,gross=gross,net=net))
    if abs(sum(t['net'] for t in ledger)-(r.equity_curve[-1].equity-CASH))>1e-6:
        raise AssertionError('Ledger and equity disagree')
    return ledger


def metrics(rows):
    pnl=sum(x['net'] for x in rows)
    gross=sum(x['gross'] for x in rows)
    by_date=defaultdict(float)
    for t in rows:
        by_date[t['exit_date']]+=t['net']
    return dict(pnl=pnl,gross=gross,drag=gross-pnl,trades=len(rows),
                ex_best=pnl-max([0,*by_date.values()]),active_exit_dates=len(by_date),
                positive_gross_lost_to_cost=sum(t['gross']>0>=t['net'] for t in rows))


def main():
    p=ArgumentParser(description=__doc__)
    p.add_argument('--daily',type=Path,required=True)
    p.add_argument('--minute',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    days={s:read_daily(a.daily/f'{s}.json',s) for s in SYMBOLS}
    minute={s:read_full_minute_sessions(a.minute/f'{s}-minute-20261012-20261120.json',s) for s in SYMBOLS}
    names={x['section']:x['title'] for x in json.loads(Path('docs/strategies/book151-catalog.json').read_text())['entries']}
    protocol=dict(clocks=CLOCKS,hold_bars=HOLD,sleeve=SLEEVE,rules=RULES,costs=COSTS,
                  daily_hashes={s:x[1] for s,x in days.items()},minute_hashes={s:x[1] for s,x in minute.items()},
                  intraday_windows=[['2026-10-19','2026-11-09'],['2026-11-09','2026-11-21']],
                  calendar_windows=[['2020-03-01','2024-01-01'],['2024-01-01','2026-11-21']])
    (a.output/'protocol.json').write_text(json.dumps(protocol,indent=2))
    data_runs=0
    results={}
    for clock in CLOCKS:
        intraday=type(clock) is int
        bars={s:[b for rows in minute[s][0].values() for b in rows] if intraday else days[s][0] for s in SYMBOLS}
        groups={s:aggregate(bars[s],clock) for s in SYMBOLS}
        if [g.day for g in groups['SPY']] != [g.day for g in groups['QQQ']]:
            raise ValueError('Unmatched Schwab sessions')
        slate={s:{i:signals(groups[s],groups[peer],i) for i in range(60,len(groups[s]))}
               for s,peer in (SYMBOLS,SYMBOLS[::-1])}
        data={s:qte.Dataset.from_bars(bars[s],interval_ns=bars[s][0].end_ns-bars[s][0].start_ns,
                    tick_size=.01,source_id='schwab_observed_price_proxy') for s in SYMBOLS}
        key=f'{clock}m' if intraday else clock
        results[key]={}
        source=protocol['intraday_windows' if intraday else 'calendar_windows']
        for section in RULES:
            record={}
            for split,(first,last) in zip(('development','later'),source,strict=True):
                record[split]={}
                for cost in COSTS:
                    rows=[t for s in SYMBOLS for t in compute(bars[s],groups[s],slate[s],data[s],section,first,last,clock,cost)]
                    data_runs+=len(SYMBOLS)
                    record[split][cost]=dict(metrics=metrics(rows),ledger=rows)
            results[key][section]=record
        print(f'{key} complete, {data_runs} native runs',flush=True)
    (a.output/'results.json').write_text(json.dumps(results,allow_nan=False,separators=(',',':')))
    lines=['# Schwab: 20 book rules across eight candle sizes','',
           f'{data_runs:,} native runs. All rule and candle-size comparisons are fixed, with no tuning on later results. Two correlated ETF sleeves, $10,000 target each, five candles held per trade. The short benchmark excludes locate and borrow. This screen uses observed underlying OHLC and modeled costs. Historical option quotes remain unavailable.','',
           'Both arms are retrospective. Daily bars use New York session dates, with 16:00 availability, including early closes. Weekly and monthly signals use completed groups and execute on the underlying daily clock. The partial November month is excluded. An extra execution bar means one minute intraday and one session for calendar candles. Five-candle holds are intentionally different horizons.','',
           'The older vendor history is a price proxy. Dividend cash flows and adjustment vintages have not been verified. P&L is not an audited total return. The report ranks only these 20 adaptations.','']
    qualified=[]
    for key, rules in results.items():
        lines += [f'## {key}','', '| PDF section | Rule | Development worst $ | Later base $ | Later delayed stress $ | Gross $ (delayed) | Trades | Diagnosis |','|---|---|---:|---:|---:|---:|---:|---|']
        order=sorted(rules,key=lambda s:min(c['metrics']['pnl'] for c in rules[s]['development'].values()),reverse=True)
        for s in order:
            val=rules[s]['later']
            dev=min(c['metrics']['pnl'] for c in rules[s]['development'].values())
            delayed=val['stress_delayed']['metrics']
            later_pass = all(c['metrics']['pnl']>0 and c['metrics']['trades']>=30 and c['metrics']['ex_best']>0 for c in val.values())
            design_pass = all(c['metrics']['pnl']>0 and c['metrics']['trades']>=30 and c['metrics']['ex_best']>0 for c in rules[s]['development'].values())
            if design_pass and later_pass:
                qualified.append((key,s,delayed['pnl'],delayed['trades']))
            why=('No trades' if not delayed['trades'] else 'Negative gross' if delayed['gross']<=0 else 'Costs erase gross gain' if delayed['pnl']<=0 else
                 'Sparse positive' if delayed['trades']<30 else 'Positive under delay')
            lines.append(f"| {s} | {names[s]} | {dev:.2f} | {val['base']['metrics']['pnl']:.2f} | {delayed['pnl']:.2f} | {delayed['gross']:.2f} | {delayed['trades']} | {why} |")
        lines.append('')
    lines[2:2]=[f'**{len(qualified)} of 160 rule and timeframe pairs passed the retrospective screen.**',
                 'Every cost case in both windows had to be positive, positive without its best exit date, and have at least 30 trades. The screen does not establish a live edge.', '']
    lines += ['## Rules','','The exact intraday adapters are also applied on larger bars. The futures and broad-universe rules use two-ETF adaptations. The test does not measure the original futures or sector portfolios.','']
    lines += [f'- {s} {r}.' for s,r in RULES.items()]
    lines += ['', '## Screen passers','']+[f'- {clock} · {s} {names[s]} · delayed stressed P&L ${pnl:,.2f} · {trades} trades' for clock,s,pnl,trades in qualified]
    (a.output/'report.md').write_text('\n'.join(lines)+'\n')
    (a.output/'passing.json').write_text(json.dumps(qualified,indent=2))
    code=[Path(__file__),Path(__file__).with_name('book151_schwab_screen.py'),Path(__file__).with_name('book151_historical.py'),Path(qte._core.__file__)]
    (a.output/'manifest.json').write_text(json.dumps({'code':{str(p):sha256(p.read_bytes()).hexdigest() for p in code},
        'outputs':{p.name:sha256(p.read_bytes()).hexdigest() for p in a.output.iterdir()}},indent=2))

if __name__=='__main__':
    main()
