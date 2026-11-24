"""Offline mechanics demonstration. Synthetic returns do not establish an edge."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path

from qte.cli import run_research
from qte.option_structures import SPXStructure
from qte.provenance import source_identity
from qte.strategies.book151 import BOOK_FAMILIES, futures_weights
from qte.strategies.nasdaq_gamma import FuturesMark
from qte.synthetic import SyntheticConfig, generate_case, REGIMES, HOUR_NS


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--regime", choices=REGIMES, default="regime_switch")
    parser.add_argument("--seed", type=int, default=151)
    args = parser.parse_args()
    case = generate_case(SyntheticConfig(args.regime, args.seed, bars=384, symbol="SPY_SYNTH"))
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "bars.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume"))
        for b in case.bars:
            timestamp = datetime.fromtimestamp(b.start_ns / 1e9, timezone.utc).isoformat().replace("+00:00", "Z")
            writer.writerow((b.symbol, timestamp, b.open, b.high, b.low, b.close, b.volume))
    summaries = []
    for kind in BOOK_FAMILIES:
        config = {"data": {"adapter": "csv", "path": "bars.csv", "interval_ns": HOUR_NS, "action_free": True},
                  "strategy": {"type": kind, "symbol": "SPY_SYNTH"}, "engine": {"initial_cash": 100000},
                  "execution": {"commission_bps": 1, "spread_bps": 2, "slippage_bps": 1}}
        path = args.output / f"{kind}.json"
        write_json(path, config)
        run_research(path, args.output / kind, export_version=2)
        result = json.loads((args.output / kind / "report.json").read_text())
        summaries.append({"strategy": kind, **result})
    # Synthetic constant-parameter European scenarios, not historical SPX quotes.
    definitions = {
        "bull_call_spread": (7000,7050), "bear_put_spread": (6950,7000),
        "bull_put_spread": (6900,6950), "bear_call_spread": (7050,7100),
        "long_straddle": (7000,), "long_strangle": (6950,7050),
        "long_call_butterfly": (6950,7000,7050), "long_put_butterfly": (6950,7000,7050),
        "credit_iron_butterfly": (6950,7000,7050),
        "credit_iron_condor": (6900,6950,7050,7100),
    }
    structures, charts = [], []
    for name, strikes in definitions.items():
        structure = SPXStructure(name, strikes, 7*86_400_000_000_000)
        model = structure.gamma_scenario(at_ns=0, forward=7000, volatility_pct=20)
        debit = model["price_points"]
        fees = sum(abs(leg.quantity) for leg in structure.legs)  # $1 per constituent contract, illustrative.
        grid = [{"settlement_value": 6500+i*10,
                 "pnl": structure.expiry_pnl(6500+i*10, net_debit_points=debit, fees=fees)} for i in range(101)]
        risk = structure.risk(net_debit_points=debit, fees=fees)
        structures.append({"name": name, "model": model, "net_debit_points": debit,
                           "fees_dollars": fees, "risk": risk, "expiry_curve": grid})
        low, high = min(0, min(p["pnl"] for p in grid)), max(0, max(p["pnl"] for p in grid))
        scale = high-low or 1
        points = " ".join(f"{30+5*i:.2f},{185-160*(p['pnl']-low)/scale:.2f}" for i,p in enumerate(grid))
        zero = 185-160*(0-low)/scale
        charts.append(f'<section><h3>{html.escape(name)}</h3><p>Model debit {debit:.2f} points; maximum expiry loss ${risk["maximum_loss"]:.2f}; gamma {model["position_gamma"]:.4f} $/point².</p>'
            f'<svg viewBox="0 0 560 220" role="img" aria-label="Expiry PnL versus settlement value">'
            f'<path d="M30 {zero:.2f}H530" stroke="#aaa"/><polyline points="{points}" fill="none" stroke="#111" stroke-width="2"/>'
            f'<text x="30" y="210">6500</text><text x="475" y="210">7500</text>'
            f'<text x="30" y="16">${high:.0f}</text><text x="30" y="195">${low:.0f}</text></svg></section>')
    history = {}
    for symbol, seed in (("ES_SYNTH", args.seed), ("NQ_SYNTH", args.seed+1)):
        sample = generate_case(SyntheticConfig(args.regime, seed, bars=384, symbol=symbol))
        history[symbol] = [FuturesMark(symbol, b.end_ns, b.end_ns, b.close, "synthetic", str(i))
                           for i,b in enumerate(sample.bars)]
    policies = [futures_weights(history, at_ns=384*HOUR_NS, interval_ns=HOUR_NS, kind=kind)
                for kind in ("time_series_momentum", "cross_sectional_contrarian")]
    write_json(args.output / "futures-signals.json", policies)
    write_json(args.output / "option-structures.json", structures)
    write_json(args.output / "summary.json", {"evidence": "synthetic_mechanics_only", "regime": args.regime,
                                              "seed": args.seed, "equity_runs": summaries})
    rows = "".join(f'<tr><td>{r["strategy"]}</td><td>{100*r["metrics"]["total_return"]["value"]:.2f}%</td><td>{r["fill_count"]}</td></tr>' for r in summaries)
    page = ('<!doctype html><meta charset="utf-8"><title>151 strategy mechanics</title>'
            '<style>body{font:16px system-ui;max-width:960px;margin:40px auto;padding:20px;color:#222;background:#fafafa}section{border-top:1px solid #ccc}svg{max-width:560px;width:100%}td,th{text-align:left;padding:8px;border-bottom:1px solid #ccc}</style>'
            '<h1>151 strategy mechanics</h1><p>Synthetic bars and model-only option scenarios. No historical SPX/futures performance or profitability claim.</p>'
            f'<p>Regime fixture: {args.regime}; seed {args.seed}. Four C++ cash-equity backtests, next-open execution; commissions 1 bp, spread 2 bp, slippage 1 bp. Ending positions may remain marked open.</p>'
            '<table><tr><th>Strategy</th><th>Synthetic total return</th><th>Fills</th></tr>'+rows+'</table>'
            '<h2>SPX option expiry scenarios</h2><p>One structure; European cash settlement; 7 days; supplied forward 7000; common flat IV 20%; rate 0%; illustrative $1 per constituent contract total fee. Each chart has its own PnL scale. Curves are expiry payoffs, not 5-minute trading returns.</p>'
            + ''.join(charts) + '<h2>Futures policy weights</h2><pre>'+html.escape(json.dumps(policies, indent=2))+'</pre>')
    (args.output / "report.html").write_text(page)
    write_json(args.output / "manifest.json", {"evidence": "synthetic_mechanics_only", "source_identity": source_identity(),
        "input_sha256": case.source_sha256, "seed": args.seed, "regime": args.regime,
        "files": {p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(args.output.rglob("*")) if p.is_file()}})
    print(args.output / "report.html")


if __name__ == "__main__":
    main()
