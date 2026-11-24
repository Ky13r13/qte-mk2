"""Run the frozen reversal forward-outcome study on local Robinhood captures."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from .adapters import robinhood_research
from . import forward_outcomes, regime_study, reversal_tail

RULES = {
    "signals": "Existing reversal_signal: previous close below BB(20,2 population SD), bullish close back inside, recent five-bar low touches -1/-2 prior-VIX daily sigma; close within .25 daily sigma of level.",
    "variants": "Both bounds, each with/without existing prior stress/high-vol-down and latched intraday stress veto; no fitted thresholds.",
    "selection": "At most five intents/day/variant; common five-minute hold plus one-minute entry delay sets cooldown, irrespective of evaluation horizon. Missing future entry does not erase an intent. Thirty-minute outcomes may overlap; not portfolio returns.",
    "timing": "Adapter timestamps are completed minute availability in UTC seconds, zero extra delivery latency assumed. Anchor observation next complete minute close; outcome 1/5/15/30 minutes after anchor, no later than 15:59 America/New_York.",
    "missing": "Require every minute from anchor through outcome. Retain missing entry, missing exit, missing interior path and cutoff exclusions. No interpolated prices or forced loss imputation in this descriptive study.",
    "controls": "All warmed-up eligible clock moments in earlier captured sessions with the same effective regime and 30-minute ET bucket measured from 09:30; same anchor delay/horizon/coverage rules. Controls may contain signals. No matching on subsequent price outcomes.",
    "weighting": "Within each control session average available moment returns, then equal-weight prior session means. Minimum three prior control sessions; no same-day/future-day controls. Primary signal summary equal-weights signal-session means; event-weighted distribution also retained.",
    "units": "SPX close-to-close basis points and points, with close-only MAE/MFE after anchor. Index observations are not executable prices. No option premiums, trading costs or annualized returns.",
    "distribution": "p10/median/p90 use linear interpolation at q*(n-1). Empirical positive fractions are descriptive, not forecasts. Excess means observed move minus prior-control mean, not causal treatment effect.",
}
LIMITATIONS = [
    "Previously inspected, event-selected April 2025/recent 2026 captures; no fresh holdout, representative decade sample, or independent confirmation.",
    "Small signal/control session counts and overlapping outcomes prevent treating minutes or variants as independent trials. No significance or profitability claim.",
    "Four fixed variants and four horizons are all reported; choosing a winner after inspection requires new data. Horizon availability can change cohorts.",
    "Controls match regime/time, not every volatility, event or price-path characteristic; differences are associations, not evidence of causation.",
    "Prior VIX daily sigma is a proxy, not observed one-day option-implied movement. Historical publication/revision vintages and receive times remain unavailable.",
    "Fixed regular-session clock is not a full exchange calendar; partial sessions/gaps are excluded per outcome rather than repaired. Prior controls can be far apart in calendar time.",
    "Signal logic remains unchanged. This is a separate underlying event-study command, not C++ derivative replay or an executable strategy.",
]


def generate(captures, output):
    if output.exists():
        raise FileExistsError("Use a new output directory; preserve previous research")
    daily, minute, provenance = robinhood_research.load_captures(captures)
    result = forward_outcomes.study(regime_study.features(daily), minute["SPX"])
    if not any(r["status"] == "included" for r in result["coverage"]):
        raise ValueError("No usable SPX minute sessions with prior features")
    result["schema_version"] = 1
    result["status"] = "Exploratory underlying outcomes; no executable option evidence"
    result["rules"], result["limitations"] = RULES, LIMITATIONS
    output.mkdir(parents=True, exist_ok=False)
    names = []
    for key in ("outcomes", "baseline_sessions"):
        name = key.replace("_", "-")+".jsonl"
        (output/name).write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False)+"\n"
                                        for r in result[key]), encoding="utf-8")
        names.append(name)
    summary = {k: v for k, v in result.items() if k not in ("outcomes", "baseline_sessions")}
    (output/"summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)+"\n", encoding="utf-8")
    def fmt(value):
        return "unavailable" if value is None else f"{value:.2f}"
    lines = ["# Frozen reversal forward outcomes", "", result["status"], "",
             "Means are basis points, equally weighted across signal sessions. Positive moves are not net option profits.", "",
             "| Bound / veto | Minutes | Observed events / sessions | Mean move | Matched events / sessions | Matched move | Excess over prior controls |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for row in result["summary"]:
        if row["regime"] != "all":
            continue
        observed = row["event_return_bps"]["count"]
        signal = row["session_return_bps"]
        paired = row["paired_session_return_bps"]
        excess = row["paired_session_excess_bps"]
        lines.append(f"| -{row['bound']} sigma / {'on' if row['veto'] else 'off'} | {row['horizon_minutes']} | "
                     f"{observed} / {signal['count']} | {fmt(signal['mean'])} | "
                     f"{row['paired_events']} / {paired['count']} | {fmt(paired['mean'])} | {fmt(excess['mean'])} |")
    lines.extend(["", "## Rules", "", *[f"- **{key}:** {value}" for key, value in RULES.items()],
                  "", "## Limits", "", *[f"- {value}" for value in LIMITATIONS], "",
                  "`summary.json` includes regime breakdowns, empirical distributions and exclusion counts. "
                  "`outcomes.jsonl` retains every selected intent/horizon, prior control dates and close-only excursions. "
                  "`baseline-sessions.jsonl` retains control counts/means for audit and reconstruction.", ""])
    (output/"report.md").write_text("\n".join(lines), encoding="utf-8")
    names.extend(("summary.json", "report.md"))
    modules = (forward_outcomes, regime_study, reversal_tail, robinhood_research)
    code = {Path(module.__file__).name: sha256(Path(module.__file__).read_bytes()).hexdigest() for module in modules}
    code[Path(__file__).name] = sha256(Path(__file__).read_bytes()).hexdigest()
    manifest = {"inputs": provenance, "code_sha256": code,
                "outputs": {name: sha256((output/name).read_bytes()).hexdigest() for name in names}}
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.captures, args.output)
    print(json.dumps({"included_sessions": sum(r["status"] == "included" for r in result["coverage"]),
                      "report": str(args.output/"report.md")}))


if __name__ == "__main__":
    main()
