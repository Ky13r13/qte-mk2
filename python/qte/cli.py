"""Configuration-driven offline research and historical download commands."""
from __future__ import annotations
import argparse
import csv
import json
import sys
import hashlib
from dataclasses import asdict
from pathlib import Path
import qte
from qte.adapters import load_alpaca_fixture, load_csv_bars, CsvBarSchema
from qte.provenance import source_identity
from qte.strategies import MovingAverageConfig, MovingAverageRegimeStrategy, CandidateConfig, CandidateStrategy
from qte.strategies.candidates import FAMILIES

def fields(value, allowed, required=()):
    if not isinstance(value, dict): raise ValueError('configuration section must be an object')
    if set(value)-set(allowed): raise ValueError(f'unknown configuration keys: {sorted(set(value)-set(allowed))}')
    if set(required)-set(value): raise ValueError(f'missing configuration keys: {sorted(set(required)-set(value))}')
    return value

def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')

def write_csv(path, columns, rows):
    with path.open('x', newline='', encoding='utf-8') as stream:
        writer=csv.writer(stream); writer.writerow(columns); writer.writerows(rows)

def run_research(config_path: Path, output: Path, *, export_version=1):
    if type(export_version) is not int or export_version not in (1, 2):
        raise ValueError('export version must be 1 or 2')
    config=json.loads(config_path.read_text())
    fields(config, ('data','strategy','engine','execution','risk','sampling'), ('data','strategy','engine'))
    d=fields(config['data'], ('adapter','path','interval_ns','tick_size','currency','action_free','schema'),
             ('adapter','path','interval_ns'))
    source=config_path.parent / d['path']
    kwargs={k:d[k] for k in ('interval_ns','tick_size','currency') if k in d}
    if d['adapter']=='alpaca':
        if 'schema' in d or 'action_free' in d: raise ValueError('Alpaca assumptions must be in fixture envelope')
        loaded=load_alpaca_fixture(source,**kwargs)
    elif d['adapter']=='csv':
        loaded=load_csv_bars(source,**kwargs,action_free=d.get('action_free'),schema=CsvBarSchema(**d.get('schema',{})))
    else: raise ValueError('adapter must be alpaca or csv')
    s=fields(config['strategy'], {'type','symbol','quantity'} | (set(CandidateConfig.__dataclass_fields__)-{'kind'}),
             ('type','symbol'))
    if s['type']=='moving_average_regime':
        fields(s,('type','symbol','fast_period','slow_period','quantity'),
               ('type','symbol','fast_period','slow_period','quantity'))
        strategy_config=MovingAverageConfig(s['fast_period'],s['slow_period'],s['quantity'])
        strategy=MovingAverageRegimeStrategy(s['symbol'],strategy_config)
        history_required=strategy_config.slow_period
    elif s['type'] in FAMILIES:
        if 'quantity' in s: raise ValueError('candidate strategies use allocation_fraction, not quantity')
        strategy_config=CandidateConfig(kind=s['type'],**{k:v for k,v in s.items() if k not in ('type','symbol')})
        strategy=CandidateStrategy(s['symbol'],strategy_config)
        history_required=strategy_config.history_required
    else: raise ValueError('unsupported strategy type')
    if s['symbol'] not in loaded.dataset.symbols: raise ValueError('strategy symbol not in dataset')
    e=fields(config['engine'],('initial_cash','history_capacity','random_seed'),('initial_cash',))
    if e.get('history_capacity',256)<history_required: raise ValueError('history capacity is smaller than strategy requirement')
    costs=fields(config.get('execution',{}),('commission_bps','spread_bps','slippage_bps'))
    risk=fields(config.get('risk',{}),('max_order_quantity','max_symbol_allocation','max_gross_leverage','allow_short','cash_floor'))
    identity=source_identity()
    result=qte.BacktestEngine(qte.BacktestConfig(**e,execution_costs=qte.ExecutionCosts(**costs),
                risk_limits=qte.RiskLimits(**risk),build_identity=identity)).run(
                loaded.dataset,strategy)
    sampling=None; annual=None
    sampling_spec=config.get('sampling')
    if sampling_spec is not None:
        fields(sampling_spec,('timestamps_ns','interval_ns','max_staleness_ns','periods_per_year','annual_risk_free_rate'),
               ('max_staleness_ns','periods_per_year'))
        if ('interval_ns' in sampling_spec)==('timestamps_ns' in sampling_spec):
            raise ValueError('sampling needs exactly one of interval_ns or timestamps_ns')
        if 'interval_ns' in sampling_spec:
            interval=sampling_spec['interval_ns']
            if type(interval) is not int or interval<=0: raise ValueError('sampling interval must be a positive integer')
            start,end=loaded.dataset.start_ns,loaded.dataset.end_ns
            if (end-start)%interval: raise ValueError('sampling interval must divide run duration')
            if (end-start)//interval>1_000_000: raise ValueError('sampling grid too large')
            times=list(range(start,end+1,interval))
        else: times=sampling_spec['timestamps_ns']
        sampling=qte.SamplingConfig(times,sampling_spec['max_staleness_ns'])
        annual=qte.AnnualizationConfig(sampling_spec['periods_per_year'],sampling_spec.get('annual_risk_free_rate',0))
        sampling_spec={**sampling_spec,'timestamps_ns':times,'policy':'last_event_at_or_before_v1'}
    report=qte.analyze(result,annual,sampling)
    metrics={name:{'value':getattr(report,name).value,'undefined_reason':getattr(report,name).undefined_reason}
             for name in ('total_return','maximum_drawdown','win_rate','profit_factor','expectancy',
                          'average_winning_trade','average_losing_trade','turnover','average_gross_exposure',
                          'annualized_return','annualized_volatility','sharpe_ratio','sortino_ratio','calmar_ratio')}
    manifest={'schema_version':1,'dataset_hash':result.manifest.dataset_hash,
              'dataset_hash_algorithm':result.manifest.dataset_hash_algorithm,
              'source':asdict(loaded.provenance),'source_id':loaded.dataset.source_id,
              'source_identity':identity,'normalized_engine_config':result.manifest.normalized_config,
              'strategy':{'type':s['type'],'symbol':s['symbol'],**asdict(strategy_config)},
              'configuration':config,'sampling':sampling_spec,
              'execution_model':result.manifest.execution_model,
              'dataset_start_ns':loaded.dataset.start_ns,'dataset_end_ns':loaded.dataset.end_ns,
              'research_label':'single_run_no_optimization_or_holdout_claim'}
    # Refuse an existing directory so previous research artifacts cannot be overwritten.
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'manifest.json',manifest)
    write_json(output/'report.json',{'metrics':metrics,'returns':report.returns,
                'trade_count':report.trade_count,'open_trade_count':len(result.open_trades),
                'order_count':len(result.orders),'fill_count':len(result.fills)})
    write_csv(output/'equity.csv',('timestamp_ns','equity','gross_exposure'),
              ((p.timestamp_ns,p.equity,p.gross_exposure) for p in result.equity_curve))
    sampled_points=qte.sample_equity(result,sampling) if sampling else None
    if sampling:
        write_csv(output/'sampled_equity.csv',('timestamp_ns','equity','gross_exposure'),
                  ((p.timestamp_ns,p.equity,p.gross_exposure) for p in sampled_points))
    write_csv(output/'fills.csv',('fill_id','order_id','symbol','side','quantity','timestamp_ns','price','commission'),
              ((f.id,f.order_id,f.symbol,f.side.name,f.quantity,f.effective_ns,f.executed_price,f.commission) for f in result.fills))
    write_csv(output/'orders.csv',('order_id','symbol','status','detail'),
              ((o.id,o.symbol,o.status.name,o.detail) for o in result.orders))
    if export_version==2:
        from qte.research_export import write_owned_export
        write_owned_export(output,result,manifest,sampled_points)
    write_json(output/'complete.json',{'schema_version':1,'sha256':{
        p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}})
    return output

def main(argv=None):
    parser=argparse.ArgumentParser(prog='qte')
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run'); run.add_argument('--config',type=Path,required=True); run.add_argument('--output',type=Path,required=True)
    run.add_argument('--export-version',type=int,choices=(1,2),default=1)
    lab=sub.add_parser('lab',help='Run an offline synthetic multi-strategy research lab')
    lab.add_argument('--config',type=Path,required=True); lab.add_argument('--output',type=Path,required=True)
    download=sub.add_parser('download-alpaca')
    download.add_argument('--symbols',nargs='+',required=True)
    download.add_argument('--timeframe',required=True)
    download.add_argument('--start',required=True); download.add_argument('--end',required=True)
    download.add_argument('--feed',choices=['iex','sip'],required=True)
    download.add_argument('--action-free',action='store_true',required=True)
    download.add_argument('--cache-dir',type=Path,default=Path('.cache/alpaca'))
    gui=sub.add_parser('gui',help='Open the read-only local documentation and evidence library')
    gui.add_argument('--repo-root',type=Path,default=Path.cwd(),help='Explicit QTE checkout containing documentation')
    gui.add_argument('--port',type=int,default=8765,help='Loopback port (default: 8765)')
    args=parser.parse_args(argv)
    try:
        if args.command=='gui':
            from qte.gui.server import serve
            return serve(args.repo_root, port=args.port)
        elif args.command=='download-alpaca':
            from qte.adapters.alpaca_download import download_stock_bars
            output=download_stock_bars(symbols=args.symbols,timeframe=args.timeframe,start=args.start,
                     end=args.end,feed=args.feed,action_free=args.action_free,cache_dir=args.cache_dir)
        elif args.command=='lab':
            from qte.strategy_lab import run_lab
            output=run_lab(args.config,args.output)
        else:
            output=run_research(args.config,args.output,export_version=args.export_version)
    except (ValueError,TypeError,OSError) as error:
        print(f'qte: {error}',file=sys.stderr); return 2
    print(f'Output: {output}')
    return 0
