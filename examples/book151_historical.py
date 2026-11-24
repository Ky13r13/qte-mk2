"""Fixed historical screen using existing local captures; no downloads or trading.

Run from repository root. The protocol is saved before any outcomes are computed.
Adjusted OHLC histories are explicitly proxy instruments, not executable ETF prices.
"""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, time
from hashlib import sha256
import html
import json
import math
from pathlib import Path
import random
import sys
from statistics import fmean
from zoneinfo import ZoneInfo

import qte
from qte.adapters.robinhood_research import load_captures
from qte.regime_study import features, REGIMES
from qte.strategies.book151 import BOOK_FAMILIES, BookConfig, BookStrategy

NY = ZoneInfo('America/New_York')
NS = 10**9
CASH = 100000
BLOCKED = {'unclassified', 'stress_selloff', 'high_vol_down', 'trend_down'}
WINDOWS = [('development', '2016-01-01', '2021-01-01'),
           ('validation', '2021-01-01', '2024-01-01'),
           ('recent_retrospective', '2024-01-01', '2026-11-17')]
COSTS = {'base': dict(commission_bps=1, spread_bps=2, slippage_bps=1),
         'stress': dict(commission_bps=2, spread_bps=6, slippage_bps=4)}


def day(ns):
    return datetime.fromtimestamp(ns / NS, NY).date().isoformat()


def dump(path, value):
    path.write_text(json.dumps(value, allow_nan=False, indent=2) + '\n')


def gate(now, start, stop, variant, regimes):
    if not start <= now < stop:
        return False
    if variant == 'ungated':
        return True
    if variant != 'regime_veto':
        raise ValueError('unknown variant')
    row = regimes.get(day(now))
    # Require a prior-session feature vintage, not an end-of-day outcome label.
    return bool(row and row.get('asof', day(now)) < day(now)
                and row['regime'] in REGIMES and row['regime'] not in BLOCKED)


def utc_time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('UTC'))


def load_daily(root):
    panel, sources, excluded = defaultdict(dict), {}, Counter()
    for path in sorted(root.glob('day-all-20*.json')):
        sources[str(path)] = sha256(path.read_bytes()).hexdigest()
        doc = json.loads(path.read_text())
        req = doc['request']
        if (req.get('interval'), req.get('bounds'), req.get('adjustment_type')) != ('day', 'regular', 'all'):
            raise ValueError('regular-session all-adjusted daily captures required')
        first = utc_time(req['start_time'])
        last = utc_time(req['end_time'])
        for entry in doc['response']['data']['results']:
            for b in entry['bars']:
                stamp = utc_time(b['begins_at'])
                if not first <= stamp < last or b.get('interpolated'):
                    excluded['outside_request_or_interpolated'] += 1
                    continue
                row = {k: float(b[k+'_price']) for k in ('open', 'high', 'low', 'close')}
                if not all(math.isfinite(v) and v > 0 for v in row.values()):
                    raise ValueError('invalid price')
                if row['high'] < max(row.values()) or row['low'] > min(row.values()):
                    raise ValueError('invalid OHLC')
                row.update(date=stamp.date().isoformat(), volume=b['volume'])
                if type(row['volume']) not in (int, float) or not math.isfinite(row['volume']) or row['volume'] < 0:
                    raise ValueError('invalid volume')
                old = panel[entry['symbol']].get(row['date'])
                if old is not None and old != row:
                    raise ValueError('conflicting capture')
                panel[entry['symbol']][row['date']] = row
    if not sources:
        raise ValueError('no captured daily histories')
    return panel, sources, dict(excluded)


def make_bars(symbol, rows):
    out = []
    for row in rows:
        d = datetime.fromisoformat(row['date']).date()
        start = int(datetime.combine(d, time(9, 30), NY).timestamp())*NS
        end = int(datetime.combine(d, time(16), NY).timestamp())*NS
        out.append(qte.Bar(symbol+'_ADJ_PROXY', start, end,
                           *[row[k] for k in ('open', 'high', 'low', 'close', 'volume')]))
    return out


class Hold(qte.Strategy):
    def __init__(self, start, stop):
        super().__init__()
        self.start, self.stop, self.entered = start, stop, False

    def on_bar(self, ctx, bar):
        if not self.entered and self.start <= ctx.now_ns < self.stop:
            self.entered = True
            ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.BUY,
                                             math.floor(CASH*.1/bar.close)))
        elif ctx.now_ns == self.stop:
            p = ctx.position(bar.symbol)
            if p and p.quantity:
                ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.SELL, p.quantity))


def run(bars, start, stop, family, variant, costs, regimes):
    strategy = (Hold(start, stop) if family == 'buy_hold' else
                BookStrategy(bars[0].symbol, BookConfig(kind=family),
                             lambda now, _: gate(now, start, stop, variant, regimes)))
    data = qte.Dataset.from_bars(bars, interval_ns=bars[0].end_ns-bars[0].start_ns,
                               source_id='book151_adjusted_proxy_screen', tick_size=.01)
    result = qte.BacktestEngine(qte.BacktestConfig(CASH, history_capacity=256,
        execution_costs=qte.ExecutionCosts(**costs), build_identity='book151-historical-v1')).run(data, strategy)
    if result.open_trades or any(p.quantity for p in result.positions):
        raise AssertionError('terminal liquidation failed')
    if any(o.status == qte.OrderStatus.REJECTED for o in result.orders):
        raise AssertionError('unexpected order rejection')
    pnl = result.equity_curve[-1].equity-CASH
    ledger, quantity = CASH, 0
    fills = []
    for f in result.fills:
        sign = 1 if f.side == qte.OrderSide.BUY else -1
        quantity += sign*f.quantity
        ledger -= sign*f.quantity*f.executed_price + f.commission
        fills.append(dict(time_ns=f.effective_ns, side=f.side.name, quantity=f.quantity,
                          price=f.executed_price, commission=f.commission))
    if quantity != 0 or abs(ledger-CASH-pnl) > 1e-6 or abs(sum(t.net_realized_pnl for t in result.trades)-pnl) > 1e-6:
        raise AssertionError('independent cash ledger / closed trade reconciliation failed')
    peak, dd = CASH, 0
    for p in result.equity_curve:
        peak = max(peak, p.equity)
        dd = max(dd, (peak-p.equity)/peak)
    trades = [dict(opened_ns=t.opened_ns, closed_ns=t.closed_ns, pnl=t.net_realized_pnl,
                   entry_regime=regimes.get(day(t.opened_ns), {}).get('regime', 'unclassified')) for t in result.trades]
    exit_days = defaultdict(float)
    for t in trades:
        exit_days[day(t['closed_ns'])] += t['pnl']
    stats = dict(pnl=pnl, return_pct=pnl/1000, closed_trades=len(trades), max_drawdown_pct=dd*100,
                 pnl_without_best_exit_day=pnl-max([0, *exit_days.values()]),
                 commissions=sum(f['commission'] for f in fills))
    return dict(summary=stats, fills=fills, trades=trades,
                equity=[dict(date=day(p.timestamp_ns), time_ns=p.timestamp_ns, equity=p.equity) for p in result.equity_curve])


def daily_changes(detail):
    closes = {p['date']: p['equity'] for p in detail['equity']}
    prev, out = CASH, {}
    for d, value in sorted(closes.items()):
        if d >= '2024-01-01':
            out[d] = value-prev
        prev = value
    return out


def block_ci(values):
    n, block = len(values), 20
    rng = random.Random(20261120)
    full, remain = divmod(n, block)
    sums = [sum(values[(i+j)%n] for j in range(block)) for i in range(n)]
    tails = [sum(values[(i+j)%n] for j in range(remain)) for i in range(n)]
    samples = sorted((sum(sums[rng.randrange(n)] for _ in range(full)) +
                      (tails[rng.randrange(n)] if remain else 0))/n for _ in range(2000))
    return [samples[49], samples[1949]]


def report(output, records, details, diagnostics):
    parts = ['<!doctype html><meta charset="utf-8"><title>QTE historical strategy screen</title>',
        '<style>body{font:15px system-ui;margin:30px;color:#222;background:#fafafa}table{border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #ccc;text-align:right}td:first-child{text-align:left}svg{width:100%;max-width:1100px;border:1px solid #bbb}select{padding:8px;border-radius:0}p{max-width:1000px}</style>',
        '<h1>Historical strategy screen</h1><p>Actual QTE engine runs on adjusted ETF proxies. $100,000 account, 10% target allocation. Fixed defaults; 2016–2020 development, 2021–2023 chronological validation, 2024–Nov 16 2026 retrospective comparison. These are previously inspected datasets, not untouched out-of-sample evidence. No options or futures execution claim.</p>',
        '<p>Regime veto blocks entries and requests next-open exits in stress selloff, high-volatility downtrend, downtrend, or missing/unclassified conditions. Labels use prior-session data. End-of-window liquidation uses the final observed open.</p>',
        '<label>Recent stressed equity curve <select id="pick"></select></label><div id="chart"></div>',
        '<p>Black: selected candidate. Gray: same-symbol 10% buy-and-hold. Both curves use the same dollar scale. Dropdown includes every candidate, including losses.</p>',
        '<h2>Recent stressed outcomes</h2><table><tr><th>Candidate</th><th>Variant</th><th>PnL $</th><th>Validation PnL $</th><th>Trades</th><th>Drawdown %</th><th>Passes screen</th></tr>']
    charts = []
    for d in diagnostics:
        key = d['key']; r = records[key]; baseline = details[f"{r['symbol']}-recent_retrospective-buy_hold-stress-hold"]
        points = {p['date']: p['equity'] for p in details[key]['equity'] if p['date'] >= '2024-01-01'}
        ref = {p['date']: p['equity'] for p in baseline['equity'] if p['date'] >= '2024-01-01'}
        charts.append(dict(label=f"{r['symbol']} {r['family']} {r['variant']}", dates=list(points), values=list(points.values()), baseline=[ref[x] for x in points]))
        parts.append(f"<tr><td>{html.escape(r['symbol']+' '+r['family'])}</td><td>{r['variant']}</td><td>{r['pnl']:.2f}</td><td>{d['validation_pnl']:.2f}</td><td>{r['closed_trades']}</td><td>{r['max_drawdown_pct']:.2f}</td><td>{d['passes_screen']}</td></tr>")
    parts += ['</table><p>Screen: positive base/stress recent and stressed validation PnL, 30 recent trades, positive PnL after removing best exit day, recent stressed PnL above the buy-and-hold reference. Passing does not establish statistical significance. Daily-dollar block-bootstrap diagnostics are unadjusted for multiple comparisons. Full failures, regime attribution, fills and event equity are retained in JSON.</p>',
              '<p>All-adjusted prices are constructed proxies, not point-in-time executable shares or audited total returns. Costs per side: base commission 1bp/spread 2bp/slippage 1bp; stress 2/6/4bp under QTE spread semantics. Close-detected ATR exits; next observed open execution; no financing income, taxes, or options quotes.</p>']
    parts.append('<script>const series='+json.dumps(charts, allow_nan=False)+';'+r'''
const pick=document.getElementById('pick'), chart=document.getElementById('chart');
series.forEach((s,i)=>{const o=document.createElement('option');o.value=i;o.textContent=s.label;pick.appendChild(o)});
function draw(){const s=series[+pick.value],all=s.values.concat(s.baseline),lo=Math.min(...all)-100,hi=Math.max(...all)+100;
const xy=(a)=>a.map((v,i)=>`${60+i*960/(a.length-1)},${270-(v-lo)*240/(hi-lo)}`).join(' ');
chart.innerHTML=`<svg viewBox="0 0 1100 310" role="img" aria-label="Candidate and buy-and-hold equity"><text x="5" y="20">$${hi.toFixed(0)}</text><text x="5" y="275">$${lo.toFixed(0)}</text><polyline fill="none" stroke="#aaa" stroke-width="2" points="${xy(s.baseline)}"/><polyline fill="none" stroke="#111" stroke-width="2" points="${xy(s.values)}"/><text x="60" y="300">${s.dates[0]}</text><text x="920" y="300">${s.dates.at(-1)}</text></svg>`;}
pick.onchange=draw;draw();</script>''')
    (output/'report.html').write_text('\n'.join(parts))


def main(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    protocol = dict(frozen_before_run=True, prior_data_inspected=True, optimization=False,
        symbols=['SPY','QQQ','IWM'], parameters={k: asdict(BookConfig(kind=k)) for k in BOOK_FAMILIES},
        windows=WINDOWS, costs=COSTS, variants=['ungated','regime_veto'], veto_regimes=sorted(BLOCKED),
        veto_behavior='block entry and request next-open exit; missing regime fails closed',
        gates=['recent base/stress positive','validation stress positive','30 recent closed trades',
               'positive excluding best exit day','beat recent 10pct buy_hold stressed PnL'],
        bootstrap='2000 circular 20-session blocks, seed 20261120; exploratory unadjusted paired daily dollar excess',
        assumptions='Daily all-adjusted proxy, UTC ns, 09:30–16:00 NY conservative availability incl early closes; whole units, tick .01, zero cash interest; penultimate close requests terminal exit. No options/futures simulation.')
    dump(output/'protocol.json', protocol)
    panel, sources, exclusions = load_daily(args.captures)
    historical, _, provenance = load_captures(args.regimes)
    regimes = {r['date']: r for r in features(historical)}
    for s in provenance['sources']:
        sources[str(args.regimes/s['file'])] = s['sha256']
    records, details, coverage = {}, {}, {}
    for symbol in protocol['symbols']:
        rows = sorted(panel[symbol].values(), key=lambda r:r['date'])
        for split, first, last in WINDOWS:
            selected = [r for r in rows if first <= r['date'] < last]
            warmup = [r for r in rows if r['date'] < first][-50:]
            if len(warmup) != 50 or len(selected) < 2:
                raise ValueError('insufficient study window/warmup')
            bars = make_bars(symbol, warmup+selected)
            start, stop = bars[50].end_ns, bars[-2].end_ns
            coverage[f'{symbol}-{split}'] = dict(first=selected[0]['date'],last=selected[-1]['date'],bars=len(selected),
                regimes=dict(Counter(regimes.get(r['date'],{}).get('regime','unclassified') for r in selected)))
            for family in (*BOOK_FAMILIES, 'buy_hold'):
                for cost, costs in COSTS.items():
                    for variant in (['hold'] if family == 'buy_hold' else protocol['variants']):
                        key=f'{symbol}-{split}-{family}-{cost}-{variant}'
                        detail=run(bars,start,stop,family,variant,costs,regimes)
                        details[key]=detail
                        records[key]=dict(detail['summary'], symbol=symbol,split=split,family=family,cost=cost,variant=variant)
                        dump(output/(key+'.json'),detail)
            print(f'Completed {symbol} {split}',flush=True)
    diagnostics=[]
    for key,r in records.items():
        if r['split']!='recent_retrospective' or r['cost']!='stress' or r['family']=='buy_hold':continue
        prefix=f"{r['symbol']}-"
        val=records[prefix+f"validation-{r['family']}-stress-{r['variant']}"]
        base=records[prefix+f"recent_retrospective-{r['family']}-base-{r['variant']}"]
        refkey=prefix+'recent_retrospective-buy_hold-stress-hold'
        a,b=daily_changes(details[key]),daily_changes(details[refkey])
        if a.keys()!=b.keys() or abs(sum(a.values())-r['pnl'])>1e-6:raise AssertionError('daily sampling mismatch')
        excess=[a[d]-b[d] for d in sorted(a)]
        groups=defaultdict(list)
        for t in details[key]['trades']:groups[t['entry_regime']].append(t['pnl'])
        checks=dict(recent_positive=min(base['pnl'],r['pnl'])>0,validation_positive=val['pnl']>0,
                    enough_trades=r['closed_trades']>=30,without_best_day_positive=r['pnl_without_best_exit_day']>0,
                    beats_hold=r['pnl']>records[refkey]['pnl'])
        diagnostics.append(dict(key=key,checks=checks,passes_screen=all(checks.values()),validation_pnl=val['pnl'],
            hold_pnl=records[refkey]['pnl'],mean_daily_excess=fmean(excess),exploratory_95pct_ci=block_ci(excess),
            entry_regime_attribution={k:dict(trades=len(v),pnl=sum(v)) for k,v in groups.items()}))
    dump(output/'summary.json',dict(results=records,diagnostics=diagnostics,coverage=coverage,exclusions=exclusions))
    report(output,records,details,diagnostics)
    code=[Path(__file__),Path('python/qte/strategies/book151.py'),Path('python/qte/strategies/candidates.py'),
          Path('python/qte/regime_study.py'),Path('python/qte/adapters/robinhood_research.py')]
    code.append(Path(qte._core.__file__))
    dump(output/'manifest.json',dict(python_version=sys.version, sources=sources,code={str(p):sha256(p.read_bytes()).hexdigest() for p in code},
        outputs={p.name:sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}))
    print(json.dumps(dict(runs=len(records),screen_passers=[d['key'] for d in diagnostics if d['passes_screen']]),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures',type=Path,default=Path('build/volume-profitability-20261117/captures'))
    parser.add_argument('--regimes',type=Path,default=Path('build/robinhood-regimes-20261115'))
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args())
