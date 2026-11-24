"""Generate the cached SPX regime study consumed by the local research GUI."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from .adapters.robinhood_research import load_captures
from .regime_study import (FAMILIES, INITIAL_CASH, aggregate, comparisons, features,
                           intervals, run_daily, run_intraday, walk_forward)

LIMITATIONS = [
    "Observed underlying data; all option premiums, strikes, seven-day maturities, spreads and fills are modeled. Historical listings and liquidity are unverified.",
    "SPX/SPXW options are European cash-settled. This is not an American-option replay and includes hypothetical weekly maturities before actual weekly listings.",
    "VIX is a 30-day implied-volatility index. Prior VIX / sqrt(252) is a daily sigma proxy, not an observed one-day option-implied distribution or reversal probability.",
    "Daily regimes use prior-session SPX and VIX. Historical revision/publication vintages are unavailable. No real-time news or dealer positioning inputs.",
    "Daily option holds are opening observation to final daily value, modeled as 6.5 hours. Daily source values need not be official 16:00 settlement. Opening-condition fills are idealized.",
    "Minute study uses April 2025 plus the latest 20 captured sessions, selected for event inspection; it is not a representative decade intraday test.",
    "Daily development <2016, validation 2016–2020, chronological holdout 2021 onward. Rules are retrospective, so this is not untouched prospective evidence.",
    "Fixed one contract per independent $1m sleeve. Dollar profit rankings are not risk matched. Cash-secured short puts reserve full strike notional; short calls are capped by long calls.",
    "Drawdowns use closed-trade equity and understate intratrade losses. No margin liquidation, financing income, market depth, or volatility smile calibration.",
    "SPY close-location weighted volume is a proxy, not measured aggressive order flow. The moving-average shift coefficient is fixed at +/-0.5 ATR; no coefficient optimization.",
    "No true option-quote backtest: the authenticated option-history sample was entirely interpolated. Current Greeks cannot be used as historical features.",
]
RULES = {
    "regimes": "Prior 300 SPX bars required. Stress: prior return <=-2 sigma with VIX>=25, or VIX>=35 and close<SMA20. Recovery: positive prior candle within five stress sessions. High-vol: VIX>=25; down if close<SMA50 and SMA20<SMA50, otherwise range. Up: close>SMA50 and SMA20>SMA50; low-vol if VIX<15. Down: inverse. Else range.",
    "bands": "Daily anchor = previous SPX close; one sigma = anchor * prior VIX / 100 / sqrt(252). Bollinger = 20-bar mean +/-2 population standard deviations. SMA300 uses 300 prior observed daily closes.",
    "guard": "Block bullish entries in stress/high-vol down. Outside stress, block bearish entries at/below -2 sigma, within 0.5% of SMA300, or below the lower Bollinger band. Block bullish entries at/above +2 sigma or above the upper band. These reduce chasing; they do not guarantee reversals.",
    "moving_average": "EMA20 alpha=2/21; flow=sum(volume*(2*close-high-low)/(high-low))/sum(volume) across 20 SPY bars, zero contribution for flat bars. Adjusted EMA = EMA20 +/-0.5*ATR14*flow. VWMA weights SPX closes with aligned SPY volume. Compare SMA, EMA, VWMA and both shift signs.",
    "daily_entries": "SMA/EMA/flow/VWMA: direction from prior close vs prior level. Band reentry: prior low/high penetrated band and candle closed back inside with matching candle direction. SMA300 reclaim: prior low <=1.003*SMA300, close>SMA300 and close>open. Stress momentum: bearish prior candle in stress/down regime. Apply guards to opening observation.",
    "minute_entries": "Completed minute Bollinger reentry after at least 1 daily sigma; SMA300 cross back up; or stress downside band break. EMA/flow baselines use close vs level. A matched EMA baseline requires the same available SPY-volume window as the flow variant. Signal at minute end; enter next complete minute close; exit five minutes later. One position, max five entries per session per candidate. Missing entry minute means no fill; missing exit after entry books full maximum loss, never erases the trade. Intraday stress triggers below -2.5 daily sigma regardless of prior VIX, or below -2 sigma with prior VIX>=25, while below the minute mean; it latches for the rest of the session. No overnight indicator carry.",
    "options": "European Black-76 on spot*exp((r-q)*T), r=4%, q=1.3%, T=7 calendar days. ATM rounded to nearest 5 (ties down). Long call/put, ATM credit vertical with 1%-of-spot width rounded to 5, or cash-secured ATM short put. One contract, $100 multiplier. No naked short calls.",
    "costs": "Base: 0.25-point half spread, $1.25 fee/leg/side, adverse 0.10 tick rounding. Wide: 1-point half spread, $2.50 fee/leg/side. Frozen-IV scenarios use prior VIX at both marks. VIX-repriced uses same-day VIX for exit valuation only, not entry selection; this is a timing/model sensitivity, not an observed quote.",
    "selection": "All candidates retained. Walk-forward uses previous five calendar years, >=30 trades per regime/candidate, highest net PnL/sum(max_loss), positive only; otherwise cash. Next-year outcomes scored separately. Cash-secured puts excluded from selector because their collateral profile differs materially.",
}


def generate(captures: Path, output: Path):
    daily, minute, provenance = load_captures(captures)
    rows = features(daily)
    if not rows:
        raise ValueError("No observed SPX daily data")
    trades, skipped = run_daily(rows, daily["VIX"])
    intraday, charts = run_intraday(rows, minute)
    output.mkdir(parents=True, exist_ok=True)
    def save(name, payload):
        text = json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True)
        (output / name).write_text(text+"\n")
    years = sorted({row["date"][:4] for row in rows})
    for year in years:
        save(f"year-{year}.json", {"rows": [r for r in rows if r["date"].startswith(year)]})
    for day, chart in charts.items():
        chart["trades"] = [t for t in intraday if t["date"] == day]
        save(f"minute-{day}.json", chart)
    for name, values in (("daily-trades.jsonl", trades), ("minute-trades.jsonl", intraday)):
        with (output / name).open("w") as stream:
            for value in values:
                stream.write(json.dumps(value, allow_nan=False, sort_keys=True)+"\n")
    coverage = {s: {"first": min(b) if b else None, "last": max(b) if b else None, "bars": len(b)} for s, b in daily.items()}
    summary = {"schema_version": 1, "evidence": "observed_underlying_modeled_options",
               "coverage": coverage, "years": years, "minute_days": sorted(charts),
               "minute_bars": sum(len(c["bars"]) for c in charts.values()),
               "regime_counts": dict(Counter(r["regime"] for r in rows)),
               "rules": RULES, "limitations": LIMITATIONS, "initial_cash": INITIAL_CASH,
               "daily_comparisons": comparisons(trades), "minute_comparisons": comparisons(intraday),
               "walk_forward": walk_forward(trades), "skipped": skipped,
               "minute_evidence": "selected_event_windows_only",
               "event_annotations": [{"date": "2025-04-02", "label": "Tariff announcement (historical context only)",
                                      "url": "https://www.frbsf.org/research-and-insights/publications/economic-letter/2025/10/market-reactions-to-tariff-announcements/"}]}
    save("summary.json", summary)
    save("intervals.json", {"intervals": intervals(rows)})
    source_files = [Path(__file__), Path(__file__).with_name("regime_study.py"),
                    Path(__file__).parent/"adapters"/"robinhood_research.py", Path(__file__).with_name("gamma_surface.py")]
    save("manifest.json", {**provenance, "capture_date": "2026-11-15", "code": {p.name: sha256(p.read_bytes()).hexdigest() for p in source_files},
                           "families": FAMILIES, "rules": RULES, "limitations": LIMITATIONS,
                           "outputs": {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob("*")) if p.is_file() and p.name != "manifest.json"}})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--output", default=Path("build/regime-study"), type=Path)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be new or empty; preserve previous research artifacts")
    result = generate(args.captures, args.output)
    print(json.dumps({"coverage": result["coverage"], "minute_bars": result["minute_bars"],
                      "daily_comparison_rows": len(result["daily_comparisons"])}))


if __name__ == "__main__":
    main()
