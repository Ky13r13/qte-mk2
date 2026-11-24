"""Reproduce the fixed reversal and last-hour put investigation offline."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from .adapters.robinhood_research import load_captures, load_last_hour_screen
from .regime_study import features
from .reversal_tail import session_bars, reversals, tail_candidates, put_mark, exit_proceeds


def generate(captures, tail_captures, output):
    output.mkdir(parents=True, exist_ok=True)
    daily, minute, provenance = load_captures(captures)
    _, tail_minutes, tail_provenance = load_captures(tail_captures)
    rows = features(daily)
    by_date = {r["date"]: r for r in rows}
    screen, screen_sources = load_last_hour_screen(tail_captures)
    selected = [r["date"] for r in sorted(screen, key=lambda r: (r["return"], r["date"]))[:20]]
    trades, skipped = reversals(rows, minute["SPX"])
    candidates, tail_skipped = tail_candidates(rows, tail_minutes["SPX"], selected)
    comparisons = []
    for bound in (1, 2):
        for veto in (True, False):
            for scenario in ("base_frozen_iv", "wide_frozen_iv"):
                chosen = [t for t in trades if (t["bound"], t["veto"], t["scenario"]) == (bound, veto, scenario)]
                comparisons.append({"bound": bound, "veto": veto, "scenario": scenario,
                                    "trades": len(chosen), "call_pnl": round(sum(t["pnl"] for t in chosen), 2),
                                    "points": round(sum(t["points"] or 0 for t in chosen), 2),
                                    "mes_illustration": round(sum(t["mes_illustration"] or 0 for t in chosen), 2),
                                    "missing_exits": sum(t["missing_exit"] for t in chosen),
                                    "win_rate": sum(t["pnl"] > 0 for t in chosen)/len(chosen) if chosen else None,
                                    "regimes": dict(Counter(t["regime"] for t in chosen))})
    events = []
    for day in selected:
        matches = [c for c in candidates if c["date"] == day and c["iv_scale"] == 1]
        if not matches:
            continue
        best = max(matches, key=lambda c: c["peak_ratio"])
        sensitivity = [{"iv_scale": c["iv_scale"], "peak_ratio": c["peak_ratio"], "entry_cost": c["entry_cost"]}
                       for c in candidates if (c["date"], c["entry_time"], c["strike"]) == (day, best["entry_time"], best["strike"])]
        events.append({**best, "sensitivity": sensitivity, "tested_base_choices": len(matches),
                       "tenfold_base_choices": sum(c["peak_ratio"] >= 10 for c in matches),
                       "negative_close_choices": sum(c["close_pnl"] < 0 for c in matches)})
    def write(name, obj):
        (output/name).write_text(json.dumps(obj, allow_nan=False, sort_keys=True, separators=(",", ":"))+"\n")
    for name, entries in (("reversal-trades.jsonl", trades), ("put-candidates.jsonl", candidates)):
        (output/name).write_text("".join(json.dumps(r, allow_nan=False, sort_keys=True)+"\n" for r in entries))
    days = sorted(set(t["date"] for t in trades) | set(e["date"] for e in events))
    for day in days:
        # Never merge event-selected days into the reversal test universe.
        bars = minute["SPX"].get(day, tail_minutes["SPX"].get(day, []))
        event = next((e for e in events if e["date"] == day), None)
        path = []
        if event:
            from datetime import datetime
            from .reversal_tail import NY
            end = int(datetime.fromtimestamp(event["entry_time"], NY).replace(hour=16, minute=0, second=0).timestamp())
            path = [{"time": b["time"], "spot": b["close"],
                     "ratio": exit_proceeds(put_mark(b["close"], event["strike"], event["entry_iv"], (end-b["time"])/60))/event["entry_cost"]}
                    for b in bars if event["entry_time"] < b["time"] < end]
        write(f"day-{day}.json", {"daily": by_date[day], "bars": session_bars(by_date[day], bars), "put_path": path,
                                 "trades": [t for t in trades if t["date"] == day],
                                 "event": event})
    summary = {"schema_version": 1, "status": "Exploratory; modeled derivatives; no verified 10x option returns",
               "reversal_sessions": sum(r["regime"] != "unclassified" and r["date"] in minute["SPX"] for r in rows),
               "reversal_days": sorted(minute["SPX"]), "comparisons": comparisons, "skipped": skipped,
               "screen_sessions": len(screen), "screen_start": screen[0]["date"] if screen else None,
               "screen_end": screen[-1]["date"] if screen else None, "screen_selected": selected,
               "candidate_count": len(candidates), "tail_skipped": tail_skipped,
               "verified_tenfold_days": 0, "events": events, "days": days,
               "rules": [
                   "Daily sigma = previous SPX close × previous VIX / 100 / sqrt(252). VIX is a 30-day proxy, not a measured one-day option move.",
                   "Long reversal: previous minute close below BB(20,2 population SD); current positive candle closes back above lower band. Last five-minute low touches -1 or -2 daily sigma; current close within .25 sigma of that level.",
                   "Enter next complete minute close; exit five minutes later; max five non-overlapping trades/day per variant; final exit by 15:59 ET. No intrabar stop/target inference.",
                   "Veto prior stress/high-vol-down regime and latch intraday decline ≥2.5 sigma, or ≥2 sigma when prior VIX≥25. No-veto is a fixed counterexample comparator.",
                   "Reversal test uses original April 2025 / recent 2026 windows only. Tail discovery selects 20 largest 15:00–16:00 declines from all available observed half-hour sessions; it is hindsight-selected, not an out-of-sample strategy.",
                   "Put entries every five minutes 15:00–15:50 ET; hypothetical same-day PM expiry. Strikes floor to five points at .5/1/1.5/2 remaining model sigma OTM. Tenfold = exit proceeds / entry outlay ≥10 (900% profit).",
                   "0DTE marks use prior VIX, frozen flat IV and a trading variance clock: remaining RTH minutes/(390×252). Sensitivity .75/1/1.25/2/3 IV; no skew, calibration or observed NBBO. SPXW is European style.",
                   "Put costs: .10-point adverse half-spread, .05 price tick below 3.00 and .10 above, $1.25/side; entry theoretical mid ≥.10. Exit last mark 15:59, not settlement. Peak is hindsight; 10x trigger exits next complete minute when available.",
                   "Seven-day long-call model: one contract, $100 multiplier; .25/.10 half-spread/tick base, 1.00/.10 wide; $1.25/$2.50 per-side fees. No historical option quotes or listing verification.",
                   "MES illustration is SPX point change ×$5 minus $5 round-trip friction (two .25-point ticks plus $2.50 commissions). Not actual MES history, futures execution, basis, roll or margin accounting.",
               ], "limitations": [
                   "No actual futures history/feed is connected. CME snapshots are delayed and do not refresh in this report.",
                   "No verified historical option bid/ask data: no confirmed 10x returns or evidence of mispriced volatility. Sparse/interpolated option probes cannot establish executable entry/exit pairs.",
                   "Event-selected outcomes and small reversal samples cannot support live-profitability or regime robustness claims. No annualized metrics or portfolio returns are inferred.",
                   "Selecting worst close-to-close final hours misses intrahour crashes that recovered and other strike/entry combinations. This is a bounded first search, not an exhaustive decade census.",
                   "Index candles are observed values, not executable trades. No order-flow or dealer positioning has been inferred.",
               ]}
    summary["context"] = [
        {"date": "2024-12-18", "text": "FOMC day: December projections raised the 2025 policy-rate and inflation outlook relative to September. Plausible macro context for repricing; not proof of causation for each minute or of cheap puts.", "source": "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20241218.htm"},
        {"date": "2025-03-04", "text": "Canada/Mexico tariffs took effect March 4 after a one-month delay. Historical event annotation, not a timestamped trading signal or proof of the late-hour move's cause.", "source": "https://commonslibrary.parliament.uk/research-briefings/cbp-12094/"},
    ]
    quote_file = tail_captures/"futures-snapshots.json"
    summary["futures"] = json.loads(quote_file.read_text()) if quote_file.exists() else []
    summary["option_audits"] = []
    for path in sorted(tail_captures.glob("option-audit-*.json")):
        capture = json.loads(path.read_text())
        for result in capture["response"]["data"].get("results", []) or []:
            bars = result["bars"] or []
            summary["option_audits"].append({"contract": capture["contract"], "bars": len(bars),
                                             "observed": sum(not b.get("interpolated", False) for b in bars),
                                             "observed_times": [b["begins_at"] for b in bars if not b.get("interpolated", False)]})
    write("summary.json", summary)
    write("screen.json", {"sessions": screen})
    sources = [{**s, "capture_root": str(captures)} for s in provenance["sources"]] + [
        {**s, "capture_root": str(tail_captures)} for s in tail_provenance["sources"]]
    code = [Path(__file__), Path(__file__).with_name("reversal_tail.py"), Path(__file__).with_name("regime_study.py"),
            Path(__file__).with_name("gamma_surface.py"), Path(__file__).parent/"adapters"/"robinhood_research.py"]
    write("manifest.json", {"sources": sources, "screen_sources": screen_sources,
                           "quote_sha256": sha256(quote_file.read_bytes()).hexdigest() if quote_file.exists() else None,
                           "code": {str(p): sha256(p.read_bytes()).hexdigest() for p in code},
                           "files": {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file() and p.name != "manifest.json"}})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--tail-captures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output must be empty; preserve previous research artifacts")
    result = generate(args.captures, args.tail_captures, args.output)
    print(json.dumps({k: result[k] for k in ("reversal_sessions", "comparisons", "screen_sessions", "candidate_count")}))


if __name__ == "__main__":
    main()
