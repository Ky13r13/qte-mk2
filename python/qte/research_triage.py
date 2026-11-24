"""Offline diagnostics of existing SPX model studies, not a new backtest.

Compare identical recorded trades across cost scenarios and expose concentration.
No parameter search, executable-price inference, or strategy promotion.
"""
import argparse
from collections import defaultdict
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path


def concentration(trades):
    days = defaultdict(list)
    regimes = defaultdict(list)
    for trade in trades:
        pnl = trade["pnl"]
        if type(pnl) not in (int, float) or not math.isfinite(pnl):
            raise ValueError("Trade PnL must be finite")
        date.fromisoformat(trade["date"])
        days[trade["date"]].append(pnl)
        regimes[trade["regime"]].append(pnl)
    daily = {day: math.fsum(values) for day, values in sorted(days.items())}
    total = math.fsum(daily.values())
    best = max(daily, key=lambda day: (daily[day], day)) if daily else None
    return {
        "trades": len(trades), "sessions": len(days), "pnl": round(total, 2),
        "best_day": best, "best_day_pnl": daily[best] if best else None,
        "pnl_excluding_best_day": round(total-daily[best], 2) if best else None,
        "pnl_by_year": {year: round(math.fsum(v for day, v in daily.items()
                                              if day[:4] == year), 2)
                        for year in sorted({day[:4] for day in daily})},
        "pnl_by_regime": {regime: round(math.fsum(values), 2)
                          for regime, values in sorted(regimes.items())},
    }


def compare(trades, scenarios):
    """Match recorded intent within one family/vehicle/split.

    A matched cohort is a retrospective diagnostic, not a portfolio: it drops
    trades absent from any scenario and cannot repair eligibility/solvency effects.
    """
    indexed = {scenario: {} for scenario in scenarios}
    for trade in trades:
        scenario = trade["scenario"]
        if scenario not in indexed:
            raise ValueError(f"Unexpected scenario: {scenario}")
        key = (trade["date"], trade.get("entry_time"), trade["direction"])
        if key in indexed[scenario]:
            raise ValueError("Duplicate trade identity within scenario")
        indexed[scenario][key] = trade
    common = set.intersection(*(set(values) for values in indexed.values()))
    # Do not compare different contracts, observed prices, or exit schedules as
    # though the scenario changed only costs/IV. Fail rather than silently omit.
    fields = ("strike", "width", "entry_spot", "exit_spot", "exit_time", "regime")
    for key in common:
        reference = indexed[scenarios[0]][key]
        for values in indexed.values():
            if any(values[key].get(field) != reference.get(field) for field in fields):
                raise ValueError("Matched trade has conflicting contract or observation")
    return {
        "common_trades": len(common),
        "scenarios": {
            scenario: {
                "all_recorded": concentration(list(values.values())),
                "matched": concentration([values[key] for key in sorted(common)]),
                "not_in_common": len(values)-len(common),
            } for scenario, values in indexed.items()
        },
    }


def triage(trades, scenarios):
    groups = defaultdict(list)
    for trade in trades:
        day = trade["date"]
        date.fromisoformat(day)
        split = ("development_before_2016" if day < "2016-01-01" else
                 "validation_2016_2020" if day < "2021-01-01" else
                 "previously_inspected_2021_onward")
        groups[(split, trade["family"], trade["vehicle"])].append(trade)
    return [{"split": split, "family": family, "vehicle": vehicle,
             **compare(values, scenarios)}
            for (split, family, vehicle), values in sorted(groups.items())]


def generate(study, output):
    """Write a new immutable report directory; preserve previous artifacts."""
    report = {
        "schema_version": 1,
        "status": "Exploratory diagnostics of previously inspected modeled options",
        "limitations": [
            "No new backtest or independent data validation; input JSONL is a saved QTE model study.",
            "Chronological labels are not fresh holdouts: these outcomes have already been inspected.",
            "Matched cohorts discard scenario-specific trades and are not deployable portfolios.",
            "Removing the best day is a concentration diagnostic, not a significance test.",
            "No historical option quotes, calibrated smile, or execution evidence is added.",
            "No winner selected; positive modeled PnL alone does not establish viability.",
        ],
    }
    sources = []
    for frequency, scenarios in (
        ("daily", ("base_frozen_iv", "wide_frozen_iv", "vix_repriced")),
        ("minute", ("base_frozen_iv", "wide_frozen_iv")),
    ):
        path = study / f"{frequency}-trades.jsonl"
        raw = path.read_bytes()
        trades = [json.loads(line) for line in raw.splitlines() if line.strip()]
        report[frequency] = triage(trades, scenarios)
        sources.append({"file": path.name, "sha256": sha256(raw).hexdigest(), "rows": len(trades)})
    output.mkdir(parents=True, exist_ok=False)
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)+"\n"
    (output / "triage.json").write_text(encoded, encoding="utf-8")
    lines = ["# Research triage", "", report["status"], "",
             "All figures are modeled USD PnL for existing one-contract studies, not live results.",
             "Only the previously inspected 2021-onward slice is shown below; JSON retains every split.", "",
             "| Sample | Family / vehicle | Common trades | Base PnL | Wide PnL | Wide excluding best day |",
             "|---|---|---:|---:|---:|---:|"]
    for frequency in ("daily", "minute"):
        for row in report[frequency]:
            if row["split"] != "previously_inspected_2021_onward":
                continue
            base = row["scenarios"]["base_frozen_iv"]["matched"]
            wide = row["scenarios"]["wide_frozen_iv"]["matched"]
            def shown(value):
                return "unavailable" if not row["common_trades"] else str(value)
            lines.append(f"| {frequency} | {row['family']} / {row['vehicle']} | {row['common_trades']} | "
                         f"{shown(base['pnl'])} | {shown(wide['pnl'])} | {shown(wide['pnl_excluding_best_day'])} |")
    lines.extend(["", *[f"- {limitation}" for limitation in report["limitations"]], ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    manifest = {"inputs": sources, "code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "outputs": {name: sha256((output/name).read_bytes()).hexdigest()
                            for name in ("triage.json", "report.md")}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.study, args.output)
    print(f"Research diagnostics: {args.output / 'report.md'}")


if __name__ == "__main__":
    main()
