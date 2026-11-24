"""Compile a 365-day, two-provider evidence panel, preserving source clocks."""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import csv
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from qte.adapters.robinhood_research import load_captures
from qte.regime_study import features

ROOT = Path('build/daily-regimes/year-20261120')
NY = ZoneInfo('America/New_York')
START, END = date(2025,11,21), date(2026,11,21)
HOLIDAYS = set('2025-11-27 2025-12-25 2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07'.split())
EARLY = {'2025-11-28','2025-12-24'}
TICKERS = ('SPX','VIX','SPY','QQQ','IWM')
ETFS = TICKERS[2:]
PROVIDERS = ('Robinhood', 'Schwab')
INTERVALS = {'day': ('daily', 0), 'minute': ('intraday', 1), '30minute': ('intraday', 30)}


@dataclass(frozen=True)
class Inputs:
    archive: Path = ROOT
    schwab_daily: Path = Path('build/schwab-daily-20261121')
    schwab_minute: Path = Path('build/schwab-pilot-20261121')
    prior: Path = Path('build/robinhood-regimes-20261115')

    def protect(self, output):
        for directory in (self.archive, self.schwab_daily, self.schwab_minute, self.prior):
            if output.resolve().is_relative_to(directory.resolve()):
                raise ValueError(f'Output must be outside input directory {directory}')
        if output.exists() or output.is_symlink():
            raise FileExistsError(f'Use a fresh output directory: {output}')
        for directory in (self.archive / 'raw', self.archive / 'schwab', self.schwab_daily,
                          self.schwab_minute, self.prior):
            if not directory.is_dir():
                raise FileNotFoundError(f'Missing capture directory: {directory}')


def session(day):
    return day.weekday()<5 and day.isoformat() not in HOLIDAYS


def interval_end(d):
    return 780 if d in EARLY else 960


class YearPanel:
    def __init__(self, inputs):
        self.inputs = inputs
        self.daily = defaultdict(dict)
        self.intraday = defaultdict(lambda: defaultdict(dict))
        self.audit = Counter()
        self.hashes = {}

    def read_capture(self, path):
        raw=path.read_bytes()
        self.hashes[str(path)]=hashlib.sha256(raw).hexdigest()
        doc=json.loads(raw)
        if 'response_canonical_json_sha256' in doc:
            encoded=json.dumps(doc['response'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            if hashlib.sha256(encoded).hexdigest()!=doc['response_canonical_json_sha256']:
                raise ValueError(f'Capture hash mismatch: {path}')
        return doc


    def normalize(self, provider,symbol,raw,kind,size):
        if raw.get('interpolated'):
            self.audit[provider+' interpolated']+=1
            return
        if provider=='Robinhood':
            stamp=datetime.fromisoformat(raw['begins_at'].replace('Z','+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Robinhood bars require an explicit timezone')
            suffix='_value' if symbol in TICKERS[:2] else '_price'
        else:
            stamp=datetime.fromtimestamp(raw['datetime']/1000,NY)
            suffix=''
        local=stamp.astimezone(NY)
        d=(stamp.date() if provider=='Robinhood' and kind=='daily' else local.date())
        ds=d.isoformat()
        if not session(d) or d>=END or (kind=='intraday' and d<START):
            return
        fields={k:float(raw[k+suffix]) for k in ('open','high','low','close')}
        o,h,l,c=(fields[k] for k in ('open','high','low','close'))
        v=raw.get('volume')
        if not all(map(math.isfinite,(o,h,l,c))) or not 0<l<=min(o,c)<=max(o,c)<=h or (v is not None and (not math.isfinite(v) or v<0)):
            self.audit[f'{provider} inconsistent OHLCV']+=1
            return
        fields['date']=ds
        if v is not None:
            fields['volume']=v
        if kind=='daily':
            key=ds; dest=self.daily[provider,symbol]
        else:
            k=60*local.hour+local.minute
            if local.second or local.microsecond or k<570 or k+size>interval_end(ds) or (k-570)%size:
                self.audit[f'{provider} excluded off-grid/outside']+=1
                return
            key=k; dest=self.intraday[provider,symbol,size][ds]
        if key in dest and dest[key]!=fields:
            raise ValueError(f'Conflicting duplicate: {(provider,symbol,kind,ds,key)}')
        dest[key]=fields


    def load(self):
        files = sorted((self.inputs.archive / 'raw').glob('*.json'))
        if not files:
            raise ValueError('No Robinhood year captures found')
        for p in files:
            doc = self.read_capture(p)
            interval = doc['request']['interval']
            if interval not in INTERVALS:
                raise ValueError(f'{p}: unsupported Robinhood interval {interval!r}')
            kind, size = INTERVALS[interval]
            data = doc['response'].get('data', {})
            if not data.get('results'):
                self.audit['Robinhood failed captures'] += 1
                continue
            for item in data['results']:
                if item['interval'] != interval or item['symbol'] not in TICKERS:
                    raise ValueError(f'{p}: unexpected result symbol or interval')
                for raw in item.get('bars') or ():
                    self.normalize('Robinhood',item['symbol'],raw,kind,size)
        for probe in (self.inputs.archive/'robinhood-older-minute-probe.json',self.inputs.archive/'schwab/SPY-older-probe.json'):
            if probe.exists():
                self.read_capture(probe)
        for sym in ETFS:
            for path,kind,size in [
                (self.inputs.schwab_daily / f'{sym}.json' ,'daily',0),
                (self.inputs.archive / 'schwab' / f'{sym}-30m.json','intraday',30),
                (self.inputs.schwab_minute / f'{sym}-minute-20261012-20261120.json','intraday',1),
            ]:
                if not path.exists():
                    self.audit['Schwab unavailable '+kind+' '+sym]+=1
                    continue
                doc = self.read_capture(path)
                request = doc['request']['query_parameters']
                freq_kind = 'daily' if kind == 'daily' else 'minute'
                if (doc['provider'] != 'schwab' or doc['request']['operation_id'] != 'priceHistory'
                        or request['symbol'] != sym or doc['response']['symbol'] != sym
                        or request['frequencyType'] != freq_kind or request['frequency'] != (size or 1)):
                    raise ValueError(f'{path}: Schwab capture identity or frequency mismatch')
                for raw in doc['response']['candles']:
                    self.normalize('Schwab',sym,raw,kind,size)


    def observed(self, provider,sym,k,d):
        return self.intraday[provider,sym,k].get(d,{})


def complete(rows,d,k):
    return set(rows)==set(range(570,interval_end(d),k))


def intraday_metrics(panel, provider,sym,k,d,prior):
    rows=panel.observed(provider,sym,k,d)
    result={'count':len(rows),'expected_count':(interval_end(d)-570)//k,'complete':complete(rows,d,k)}
    if not result['complete']:
        return result
    seq=[rows[i] for i in sorted(rows)]
    peak=0
    dd=0
    for b in seq:
        if peak:
            dd=min(dd,b['low']/peak-1)
        peak=max(peak,b['high'])
    result.update(ordered_drawdown_pct=100*dd,
        high_to_close_pct=100*(seq[-1]['close']/peak-1),high=peak,low=min(x['low'] for x in seq),
        realized_vol_pct=100*math.sqrt(sum(math.log(b['close']/a['close'])**2 for a,b in zip(seq,seq[1:]))))
    if 'volume' in seq[0]:
        eligible=[panel.observed(provider,sym,k,x) for x in prior if complete(panel.observed(provider,sym,k,x),x,k)
                  and interval_end(x)>=interval_end(d)][-20:]
        if len(eligible)==20:
            rvol={}
            for offset in (30,120,interval_end(d)-570):
                cutoff=570+offset
                volume=lambda bars:sum(b['volume'] for i,b in bars.items() if i<cutoff)
                baseline=median(volume(bars) for bars in eligible)
                if baseline>0:
                    rvol[str(offset)]=volume(rows)/baseline
            result['same_clock_rvol_vs_prior20']=rvol
    return result


def encode(obj):
    return json.dumps(obj,allow_nan=False,indent=2)+'\n'


def build_records(panel):
    prior_dir = panel.inputs.prior
    historical,_,meta=load_captures(prior_dir)
    if not all(historical[symbol] for symbol in ('SPX', 'SPY', 'VIX')):
        raise ValueError('Historical warm-up needs SPX, SPY and VIX daily captures')
    for meta_row in meta['sources']:
        panel.hashes[str(prior_dir/meta_row['file'])]=meta_row['sha256']
    for symbol in ('SPX','VIX','SPY'):
        historical[symbol].update(panel.daily['Robinhood',symbol])
    context={r['date']:r for r in features(historical)}
    records=[]; counts=Counter(); extremes=[]
    dates=[START+timedelta(days=i) for i in range((END-START).days)]
    past=sorted({x for x in panel.daily['Robinhood','SPY'] if x<START.isoformat()})
    # Cross-source gap/return comparisons use each source's own preceding
    # scheduled session. Differences are recorded, never averaged.
    for d in dates:
        ds=d.isoformat()
        if not session(d):
            records.append(dict(date=ds,status='closed',reason='weekend' if d.weekday()>=5 else 'NYSE holiday'))
            continue
        row=dict(date=ds,status='completed_cash_session',close_et='13:00' if ds in EARLY else '16:00',daily={},intraday={},comparison={})
        for provider in PROVIDERS:
            row['daily'][provider]={}
            for symbol in TICKERS if provider=='Robinhood' else ETFS:
                b=panel.daily[provider,symbol].get(ds)
                if b is None:
                    counts[f'{provider} {symbol} daily missing']+=1
                    continue
                older=panel.daily[provider,symbol].get(past[-1]) if past else None
                if older:
                    b=dict(b,return_pct=100*(b['close']/older['close']-1),gap_pct=100*(b['open']/older['close']-1))
                row['daily'][provider][symbol]=b
                counts[f'{provider} {symbol} daily valid']+=1
            row['intraday'][provider]={}
            for symbol in ('SPX',*ETFS) if provider=='Robinhood' else ETFS:
                for k in ((30,) if provider=='Robinhood' or symbol=='IWM' else (1,30)):
                    m=intraday_metrics(panel, provider,symbol,k,ds,past)
                    row['intraday'][provider][f'{symbol}_{k}m']=m
                    counts[f'{provider} {symbol} {k}m '+('complete' if m['complete'] else 'incomplete')]+=1
        if (panel.inputs.archive/'raw'/f'spx-minute-{ds}.json').is_file():
            one_minute=intraday_metrics(panel, 'Robinhood','SPX',1,ds,past)
            row['intraday']['Robinhood']['SPX_1m']=one_minute
            counts['Robinhood targeted SPX 1m '+('complete' if one_minute['complete'] else 'incomplete')]+=1
        for sym in ETFS:
            a,b=[row['daily'][p].get(sym) for p in PROVIDERS]
            if a and b:
                row['comparison'][sym]={'close_schwab_minus_robinhood_bps':10000*(b['close']/a['close']-1)}
                if 'return_pct' in a and 'return_pct' in b:
                    row['comparison'][sym]['return_schwab_minus_robinhood_bps']=100*(b['return_pct']-a['return_pct'])
        f=context.get(ds,{})
        if 'expected_move' in f:
            row['prior_known_regime']={k:f[k] for k in ('asof','regime','reason','anchor','vix','expected_move','sma20','sma50','sma300')}
            m=row['intraday']['Robinhood']['SPX_30m']
            close=row['daily']['Robinhood'].get('SPX')
            if m['complete'] and close:
                low,high,settled=((p-f['anchor'])/f['expected_move'] for p in (m['low'],m['high'],close['close']))
                row['completed_session_excursions']={'low_sigma':low,'high_sigma':high,'daily_bar_close_sigma':settled,
                    'close_distance_to_sma300_pct':100*(close['close']/f['sma300']-1)}
                if low<=-2 or high>=2:
                    extremes.append(dict(date=ds,regime=f['regime'],low_sigma=low,high_sigma=high,close_sigma=settled))
        else:
            counts['Missing prior regime']+=1
        if ds=='2026-06-05':
            row['verified_calendar_events']=[dict(time_et='08:30',event='BLS Employment Situation for May 2026',
                source='https://www.bls.gov/news.release/archives/empsit_06052026.htm',
                causal_attribution='unverified')]
        if ds=='2026-11-20':
            row['verified_calendar_events']=[dict(event='Fed calendar lists Cook and Waller appearances',
                source='https://www.federalreserve.gov/newsevents/2026-speeches.htm',
                causal_attribution='unverified')]
        past.append(ds)
        records.append(row)
    return records, counts, extremes


def write_outputs(output, panel, records, counts, extremes):
    output.mkdir(parents=True, exist_ok=False)
    (output/'sessions.json').write_text(encode(records), encoding='utf-8')
    with (output/'sessions.csv').open('x', newline='', encoding='utf-8') as stream:
        writer=csv.writer(stream)
        writer.writerow(['date','status','prior_regime','spx_return_pct','vix_close','spy_robinhood_return_pct',
                         'spy_schwab_return_pct','spx_low_sigma','spx_high_sigma','spx_30m_count','qqq_schwab_30m_count'])
        for r in records:
            dl=r.get('daily',{}); rh=dl.get('Robinhood',{}); sw=dl.get('Schwab',{}); intr=r.get('intraday',{})
            x=r.get('completed_session_excursions',{})
            writer.writerow([r['date'],r['status'],r.get('prior_known_regime',{}).get('regime'),rh.get('SPX',{}).get('return_pct'),
                rh.get('VIX',{}).get('close'),rh.get('SPY',{}).get('return_pct'),sw.get('SPY',{}).get('return_pct'),
                x.get('low_sigma'),x.get('high_sigma'),intr.get('Robinhood',{}).get('SPX_30m',{}).get('count'),
                intr.get('Schwab',{}).get('QQQ_30m',{}).get('count')])
    (output/'extremes.json').write_text(encode(extremes), encoding='utf-8')
    lines=['# One year of QTE market regime evidence','',
           f'{len(records)} calendar dates, {sum(r["status"]!="closed" for r in records)} completed cash sessions, {records[0]["date"]} through {records[-1]["date"]}.',
           'Each source retains its own prices and gaps. Half-hour metrics require a complete regular or early-close grid. Prior-known labels use earlier bars; same-day excursions are retrospective.',
           'The 30-minute grids are coarser than the one-minute single-session audit. Realized volatility is the unannualized root sum of squared observed log close changes. For ordered drawdown, lows are compared only against highs of earlier bars. See the JSON for same-clock cumulative volumes against the median of the last 20 eligible sessions.',
           '','| Coverage | Count |','|---|---:|']
    lines += [f'| {k} | {v} |' for k,v in sorted(counts.items())]
    lines += ['',f'{len(extremes)} sessions crossed at least one prior 2-sigma band on a complete SPX grid. No cause is inferred from those movements.',
              '', '| Date | Prior regime | Low sigma | High sigma | Close sigma |','|---|---|---:|---:|---:|']
    lines += [f"| {x['date']} | {x['regime']} | {x['low_sigma']:.2f} | {x['high_sigma']:.2f} | {x['close_sigma']:.2f} |" for x in extremes]
    lines += coverage_notes(records, extremes)
    (output/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    hashes = {name: hashlib.sha256((output/name).read_bytes()).hexdigest()
              for name in ('sessions.json', 'sessions.csv', 'extremes.json', 'report.md')}
    (output/'manifest.json').write_text(encode(dict(sources=panel.hashes,coverage=dict(counts),exclusions=dict(panel.audit),
        artifact_sha256=hashes,
        calendars=['https://www.nyse.com/publicdocs/ICE_NYSE_2025_Yearly_Trading_Calendar.pdf',
                   'https://www.nyse.com/trade/hours-calendars'],missing=['No historical futures or option bid/ask series','No year of verified timestamped catalyst events'])))


def coverage_notes(records, extremes):
    sessions = [row for row in records if row['status'] != 'closed']
    lines = ['', '| Half-hour series | First complete session | Complete sessions |',
             '|---|---|---:|']
    for provider in PROVIDERS:
        for symbol in ETFS:
            dates = [r['date'] for r in sessions if r['intraday'][provider][symbol+'_30m']['complete']]
            lines.append(f'| {provider} {symbol} | {dates[0] if dates else "unavailable"} | {len(dates)} |')
    lines += ['', '| ETF daily closes | Matched sessions | Median absolute difference (bp) | '
              'Largest absolute difference (bp) | First date at maximum |', '|---|---:|---:|---:|---|']
    for symbol in ETFS:
        diffs = [(abs(r['comparison'][symbol]['close_schwab_minus_robinhood_bps']), r['date'])
                 for r in sessions if symbol in r['comparison']]
        if diffs:
            highest = max(value for value, _ in diffs)
            first = next(day for value, day in diffs if value == highest)
            lines.append(f'| {symbol} | {len(diffs)} | {median(v for v, _ in diffs):.2f} | '
                         f'{highest:.2f} | {first} |')
        else:
            lines.append(f'| {symbol} | 0 | unavailable | unavailable | unavailable |')
    tail_dates = {row['date'] for row in extremes}
    by_status = defaultdict(list)
    for r in sessions:
        if r['date'] in tail_dates:
            minute = r['intraday']['Robinhood'].get('SPX_1m')
            status = ('complete' if minute['complete'] else 'incomplete') if minute else 'unrequested'
            by_status[status].append(r['date'])
    for status in ('complete', 'incomplete', 'unrequested'):
        dates = by_status[status]
        lines.extend(['', f'Tail sessions with {status} one-minute SPX coverage ({len(dates)}): '
                          + (', '.join(dates) or 'none') + '.'])
    for label, missing in (
        ('Robinhood VIX daily', [r['date'] for r in sessions if 'VIX' not in r['daily']['Robinhood']]),
        ('Prior-known regime', [r['date'] for r in sessions if 'prior_known_regime' not in r]),
    ):
        lines.extend(['', f'{label} unavailable ({len(missing)}): ' + (', '.join(missing) or 'none') + '.'])
    lines.extend(['', 'The manifest identifies the input captures and exclusions. Dated event annotations '
                      'in sessions.json provide context only. The archive covers a limited set of catalysts.'])
    return lines


def replay(inputs, output):
    inputs.protect(output)
    panel = YearPanel(inputs)
    panel.load()
    records, counts, extremes = build_records(panel)
    write_outputs(output, panel, records, counts, extremes)
    return {'days':len(records), 'extremes':len(extremes), 'coverage':counts, 'exclusions':panel.audit}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Fresh directory outside all input trees')
    for key in ('archive', 'schwab_daily', 'schwab_minute', 'prior'):
        parser.add_argument('--'+key.replace('_', '-'), type=Path, default=getattr(Inputs(), key))
    args = parser.parse_args(argv)
    inputs = Inputs(args.archive, args.schwab_daily, args.schwab_minute, args.prior)
    print(encode(replay(inputs, args.output)))


if __name__ == '__main__':
    main()
