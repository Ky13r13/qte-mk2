"""Publish bounded, read-only chart artifacts for the Book151 research view."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path

from book151_timeframes import CLOCKS, SYMBOLS, aggregate, read_daily
from qte.adapters.schwab_research import read_full_minute_sessions
from qte.gui.safeio import MAX_DOCUMENT_BYTES


def study_inputs(study):
    hashes = json.loads((study / 'manifest.json').read_bytes())['outputs']
    values = []
    for name in ('protocol.json', 'results.json', 'passing.json'):
        raw = (study / name).read_bytes()
        if sha256(raw).hexdigest() != hashes.get(name):
            raise ValueError(f'Study manifest checksum mismatch for {name}')
        values.append(json.loads(raw))
    return values


def chart_sources(protocol, daily, minute):
    sources = {}
    for symbol in SYMBOLS:
        daily_bars, daily_hash = read_daily(daily / f'{symbol}.json', symbol)
        sessions, minute_hash = read_full_minute_sessions(
            minute / f'{symbol}-minute-20261012-20261120.json', symbol)
        if daily_hash != protocol['daily_hashes'].get(symbol):
            raise ValueError(f'{symbol} daily capture differs from the study protocol')
        if minute_hash != protocol['minute_hashes'].get(symbol):
            raise ValueError(f'{symbol} minute capture differs from the study protocol')
        sources[symbol] = (daily_bars, [bar for rows in sessions.values() for bar in rows])
    return sources


def export(study, daily, minute, output):
    protocol, results, passing = study_inputs(study)
    sources = chart_sources(protocol, daily, minute)
    catalog = Path(__file__).resolve().parents[1] / 'docs/strategies/book151-catalog.json'
    names={x['section']:x['title'] for x in json.loads(catalog.read_bytes())['entries']}
    output.mkdir(exist_ok=False)
    for clock in CLOCKS:
        intraday=type(clock) is int
        key=f'{clock}m' if intraday else clock
        for s in SYMBOLS:
            daily_bars, minute_bars = sources[s]
            source = minute_bars if intraday else daily_bars
            groups=aggregate(source,clock)
            rows=[[source[g.first].start_ns//1_000_000,source[g.last].end_ns//1_000_000,
                   g.open,g.high,g.low,g.close,g.day] for g in groups]
            write(output/f'candles-{key}-{s}.json',{'candles':rows})
        for rule, splits in results[key].items():
            series={}
            for split,costs in splits.items():
                series[split]={}
                for cost,data in costs.items():
                    series[split][cost]={'metrics':data['metrics'],'trades':[
                        [t['symbol'],t['entry_ns']//1_000_000,t['exit_ns']//1_000_000,t['direction'],t['quantity'],t['net']]
                        for t in data['ledger']]}
            write(output/f'run-{key}-{rule}.json',series)
    write(output/'index.json',{'clocks':list(results),'rules':[{ 'id':s,'name':names[s],'definition':v}
        for s,v in protocol['rules'].items()], 'windows':{'minute':protocol['intraday_windows'],'calendar':protocol['calendar_windows']},
        'passing':passing})


def write(path,value):
    raw=json.dumps(value,allow_nan=False,separators=(',',':')).encode()
    if len(raw)>MAX_DOCUMENT_BYTES:
        raise ValueError(f'Chart artifact exceeds GUI read limit: {path.name}')
    path.write_bytes(raw)


if __name__=='__main__':
    p=ArgumentParser(description=__doc__)
    for name in ('study','daily','minute','output'):
        p.add_argument('--'+name,type=Path,required=True)
    export(**vars(p.parse_args()))
