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
    if prepared.loaded.dataset.timing_policy == "explicit_availability_v1":
        manifest["timing"] = {field: getattr(result.manifest, field) for field in (
            "timing_policy", "timing_source_id", "opening_availability", "release_order",
            "observation_end_ns", "replay_end_ns",
        )}
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
        from qte.market_export import market_recording
        market_data = market_recording(prepared.loaded)
        if prepared.strategy_type == 'macro_router':
            write_owned_export(output,result,manifest,sampled_points,router_strategy=prepared.strategy,market_data=market_data)
        else:
            write_owned_export(output,result,manifest,sampled_points,market_data=market_data)
    write_json(output/'complete.json',{'schema_version':1,'sha256':{
        p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}})
    return output

def main(argv=None):
    argv=sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ['calm-condor']:
        from qte.calm_condor_forward import main as run_condor
        return run_condor(argv[1:])
    if argv[:1] == ['session-read']:
        from qte.session_read import main as run_session_read
        return run_session_read(argv[1:])
    if argv[:1]==['signals']:
        from qte.signal_engine import main as run_signals
        return run_signals(argv[1:])
    if argv[:1]==['truth']:
        from qte.truth_db import main as run_truth
        return run_truth(argv[1:])
    if argv[:1]==['search']:
        from qte.search import main as run_search
        return run_search(argv[1:])
    parser=argparse.ArgumentParser(prog='qte')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('calm-condor', help='Observe, audit and settle the calm condor paper study')
    sub.add_parser('session-read', help='Read a live or archived SPX session')
    sub.add_parser('signals',add_help=False,help='Causal trend-day and sigma-reversal signal engine (replay or live shadow mode)')
    sub.add_parser('truth',add_help=False,help='Market-data truth database: ingest, coverage, collect, regime outcomes')
    sub.add_parser('search',add_help=False,help='Strategy search: C++ sweeps, trial registry, deflated leaderboard, locked holdout')
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
    download.add_argument('--cache-dir',type=Path,default=Path('data/truth/captures/alpaca'))
    audit=sub.add_parser('audit-evidence',help='Check the output checksums listed in local research manifests')
    audit.add_argument('manifests',type=Path,nargs='+')
    brief = sub.add_parser('daily-brief', help='Publish the daily research agent’s captured sessions to the local GUI')
    brief.add_argument('--due', action='store_true', help='List completed sessions still missing from the published archive')
    demo=sub.add_parser('live-demo', help='Publish the offline synthetic Live desk example')
    demo.add_argument('--output', type=Path, default=Path('build/live-desk/demo'))
    trend = sub.add_parser('s7-study', help='Replay the S7 0DTE opening-range breakout')
    market = trend.add_mutually_exclusive_group(required=True)
    market.add_argument('--index-minute', type=Path, nargs='+', help='Robinhood index minute captures')
    market.add_argument('--demo', action='store_true', help='Use the deterministic synthetic example')
    trend.add_argument('--spy-minute', type=Path, help='Schwab SPY minute capture including the prior 20 sessions')
    trend.add_argument('--symbol', choices=('SPX','XSP'), default='SPX')
    trend.add_argument('--options', type=Path, help='Synchronized regime, option chain and quote tape')
    trend.add_argument('--cash', type=float, default=10000)
    trend.add_argument('--fee', type=float, default=.65, help='USD per contract per executed side')
    trend.add_argument('--output', type=Path, required=True, help='New output directory')
    for name, title in (('s1-study', 'SPX VWAP z-score reversion'), ('s2-study', 'SPX opening gap fade')):
        core = sub.add_parser(name, help=title)
        market = core.add_mutually_exclusive_group(required=True)
        market.add_argument('--index-minute', type=Path, nargs='+', help='Local Robinhood SPX minute captures')
        market.add_argument('--demo', action='store_true', help='Replay the synthetic mechanics example')
        if name == 's1-study':
            core.add_argument('--spy-minute', type=Path, help='Local Schwab SPY minute-volume history')
            core.add_argument('--lookback', type=int, default=30, help='Rolling deviation window in minutes')
            core.add_argument('--entry-z', type=float, default=2.0)
            core.add_argument('--stop-z', type=float, default=3.0)
        else:
            core.add_argument('--daily', type=Path, help='Local Robinhood daily SPX index history')
        core.add_argument('--options', type=Path, help='Point-in-time SPXW chain, quotes, regime and catalyst calendar')
        core.add_argument('--cash', type=float, default=10000)
        core.add_argument('--risk-fraction', type=float, default=.02, help='Per-trade risk cap as fraction of equity')
        core.add_argument('--fee', type=float, default=.65, help='USD per contract per leg per executed side')
        core.add_argument('--output', type=Path, required=True, help='New study directory')
    bounce=sub.add_parser('s5-study', help='Compare the 15:45 SPY oversold bounce against uptrend days')
    bounce.add_argument('--daily', type=Path, required=True)
    bounce.add_argument('--minute', type=Path, required=True)
    bounce.add_argument('--config', type=Path, help='Optional S5 RSI and IBS thresholds JSON')
    bounce.add_argument('--spread-data', type=Path, help='Decision-time SPXW chains and synchronized leg quotes')
    bounce.add_argument('--output', type=Path, required=True, help='New study directory')
    quality=sub.add_parser('execution-quality', help='Measure independent paper probes on recorded SPXW quotes')
    quality.add_argument('--config', type=Path, required=True)
    quality.add_argument('--schedule', type=Path, required=True)
    quality.add_argument('--quotes', type=Path, required=True)
    quality.add_argument('--output', type=Path, required=True, help='Published JSON snapshot (replaced atomically)')
    gui=sub.add_parser('gui',help='Open the read-only local documentation and evidence library')
    gui.add_argument('--repo-root',type=Path,default=Path.cwd(),help='Explicit QTE checkout containing documentation')
    gui.add_argument('--port',type=int,default=8765,help='Loopback port (default: 8765)')
    gui.add_argument('--allow-alpaca-downloads',action='store_true',
                     help='Explicitly permit user-launched historical downloads; credentials must be in server environment')
    gui.add_argument('--allow-research-agents',action='store_true',
                     help='Enable explicit OpenAI requests from the five-agent research page')
    gui.add_argument('--ollama-model',
                     help='Use this local Ollama model for explicitly selected research roles')
    args=parser.parse_args(argv)
    try:
        if args.command == 'daily-brief':
            from qte.daily_brief import due_sessions, publish_archive
            print(json.dumps(due_sessions(Path.cwd())) if args.due else publish_archive(Path.cwd()))
            return 0
        if args.command in ('s1-study', 's2-study'):
            from qte.core_reversion_study import run as run_core
            print(run_core(args))
            return 0
        if args.command=='s7-study':
            from qte.s7_study import run as run_trend
            print(run_trend(args))
            return 0
        if args.command=='s5-study':
            from qte.s5_study import run as run_bounce
            print(run_bounce(args.daily, args.minute, args.output, args.config, args.spread_data))
            return 0
        if args.command=='live-demo':
            from qte.live_demo import build_demo
            print(build_demo(Path.cwd(), args.output))
            return 0
        if args.command=='execution-quality':
            from qte.execution_quality import from_files
            from qte.live_outputs import publish
            if args.output.resolve() in {p.resolve() for p in (args.config, args.schedule, args.quotes)}:
                raise ValueError("Report output must be distinct from all three inputs")
            publish(args.output, from_files(args.config, args.schedule, args.quotes))
            print(args.output)
            return 0
        if args.command=='audit-evidence':
            from qte.evidence_audit import audit_command
            return audit_command(args.manifests)
        if args.command=='gui':
            from qte.gui.server import serve
            return serve(args.repo_root, port=args.port,
                         alpaca_download_enabled=args.allow_alpaca_downloads,
                         research_agents_enabled=args.allow_research_agents,
                         ollama_model=args.ollama_model)
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
