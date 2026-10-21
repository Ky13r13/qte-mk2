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
from qte.research_config import capture_research_inputs, fields, prepare_research

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
    config,data_bytes=capture_research_inputs(config_path)
    prepared=prepare_research(config,data_bytes)
    result=qte.BacktestEngine(prepared.engine_config).run(prepared.loaded.dataset,prepared.strategy)
    report=qte.analyze(result,prepared.annualization,prepared.sampling)
    metrics={name:{'value':getattr(report,name).value,'undefined_reason':getattr(report,name).undefined_reason}
             for name in ('total_return','maximum_drawdown','win_rate','profit_factor','expectancy',
                          'average_winning_trade','average_losing_trade','turnover','average_gross_exposure',
                          'annualized_return','annualized_volatility','sharpe_ratio','sortino_ratio','calmar_ratio')}
    manifest={'schema_version':1,'dataset_hash':result.manifest.dataset_hash,
              'dataset_hash_algorithm':result.manifest.dataset_hash_algorithm,
              'source':asdict(prepared.loaded.provenance),'source_id':prepared.loaded.dataset.source_id,
              'source_identity':prepared.source_identity,'normalized_engine_config':result.manifest.normalized_config,
              'strategy':{'type':prepared.strategy_type,'symbol':prepared.symbol,**asdict(prepared.strategy_config)},
              'configuration':prepared.configuration,'sampling':prepared.sampling_manifest,
              'execution_model':result.manifest.execution_model,
              'dataset_start_ns':prepared.loaded.dataset.start_ns,'dataset_end_ns':prepared.loaded.dataset.end_ns,
              'research_label':'single_run_no_optimization_or_holdout_claim'}
    # Refuse an existing directory so previous research artifacts cannot be overwritten.
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'manifest.json',manifest)
    write_json(output/'report.json',{'metrics':metrics,'returns':report.returns,
                'trade_count':report.trade_count,'open_trade_count':len(result.open_trades),
                'order_count':len(result.orders),'fill_count':len(result.fills)})
    write_csv(output/'equity.csv',('timestamp_ns','equity','gross_exposure'),
              ((p.timestamp_ns,p.equity,p.gross_exposure) for p in result.equity_curve))
    sampled_points=qte.sample_equity(result,prepared.sampling) if prepared.sampling else None
    if prepared.sampling:
        write_csv(output/'sampled_equity.csv',('timestamp_ns','equity','gross_exposure'),
                  ((p.timestamp_ns,p.equity,p.gross_exposure) for p in sampled_points))
    write_csv(output/'fills.csv',('fill_id','order_id','symbol','side','quantity','timestamp_ns','price','commission'),
              ((f.id,f.order_id,f.symbol,f.side.name,f.quantity,f.effective_ns,f.executed_price,f.commission) for f in result.fills))
    write_csv(output/'orders.csv',('order_id','symbol','status','detail'),
              ((o.id,o.symbol,o.status.name,o.detail) for o in result.orders))
    if export_version==2:
        from qte.research_export import write_owned_export
        if prepared.strategy_type == 'macro_router':
            write_owned_export(output,result,manifest,sampled_points,router_strategy=prepared.strategy)
        else:
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
    validate=sub.add_parser('validate-data',help='Validate and capture one local dataset')
    validate.add_argument('--config',type=Path,required=True); validate.add_argument('--output',type=Path,required=True)
    compare=sub.add_parser('compare',help='Run a frozen, declarative local strategy comparison')
    compare.add_argument('--config',type=Path,required=True)
    compare.add_argument('--output',type=Path,required=True)
    download_config=sub.add_parser('download-alpaca-config',help='Explicit bounded acquisition with a verified artifact')
    download_config.add_argument('--config',type=Path,required=True)
    download_config.add_argument('--output',type=Path,required=True)
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
    gui.add_argument('--allow-alpaca-downloads',action='store_true',
                     help='Explicitly permit user-launched historical downloads; credentials must be in server environment')
    args=parser.parse_args(argv)
    try:
        if args.command=='gui':
            from qte.gui.server import serve
            return serve(args.repo_root, port=args.port,
                         alpaca_download_enabled=args.allow_alpaca_downloads)
        elif args.command=='download-alpaca-config':
            from qte.download_artifact import write_download_output
            try:
                output=write_download_output(args.config,args.output)
            except Exception:
                print('qte: historical download failed; provider diagnostics omitted',file=sys.stderr)
                return 2
        elif args.command=='download-alpaca':
            from qte.adapters.alpaca_download import download_stock_bars
            output=download_stock_bars(symbols=args.symbols,timeframe=args.timeframe,start=args.start,
                     end=args.end,feed=args.feed,action_free=args.action_free,cache_dir=args.cache_dir)
        elif args.command=='lab':
            from qte.strategy_lab import run_lab
            output=run_lab(args.config,args.output)
        elif args.command=='validate-data':
            from qte.data_validation import write_dataset_output
            output=write_dataset_output(args.config,args.output)
        elif args.command=='compare':
            from qte.comparison_export import run_comparison
            output=run_comparison(args.config,args.output)
        else:
            output=run_research(args.config,args.output,export_version=args.export_version)
    except (ValueError,TypeError,OSError) as error:
        print(f'qte: {error}',file=sys.stderr); return 2
    print(f'Output: {output}')
    return 0
