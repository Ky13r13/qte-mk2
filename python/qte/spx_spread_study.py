"""Compare 5-, 10- and 20-point SPXW debit verticals using QTE replay."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean

from .spx_spreads import REGIMES, WIDTHS, replay_spx_spreads
from .spx_spread_fixture import NY, SEED, SECOND, spx_fixture

COSTS = {"base": {"fee_per_contract": 1.25, "spread_bps": 0},
         "stress": {"fee_per_contract": 2.50, "spread_bps": 20}}
CAP = 1500.0
CAPTURE_LINE_LIMIT = 64 * 1024 * 1024


def _serial(value) -> bytes:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, sort_keys=True).encode()


def _pnl(trades, unresolved):
    return {"trades": len(trades), "unresolved": len(unresolved),
            "closed_net_pnl": sum(t["net_pnl"] for t in trades),
            "net_pnl_after_zero_recovery": sum(t["net_pnl"] for t in trades) -
            sum(t["risk_deployed"] for t in unresolved),
            "risk_deployed": sum(t["risk_deployed"] for t in trades + unresolved),
            "mean_debit_points": mean([t["entry_debit_points"] for t in trades + unresolved])
            if trades or unresolved else None}


def _record(candidates, rows, scenario):
    for row in rows:
        key = scenario, row["id"]
        if key not in candidates:
            candidates[key] = {k: value for k, value in row.items()
                               if k not in {"trades", "skipped", "unresolved", "aggregate", "regimes"}}
            for field in ("trades", "skipped", "unresolved"):
                candidates[key][field] = []
        for field in ("trades", "skipped", "unresolved"):
            candidates[key][field].extend(row[field])


def _report(source, *, evidence, metadata=None):
    """Replay one session at a time to bound source quote memory."""
    metadata = metadata or {}
    by_candidate = {}
    chronology = {}
    prior_end = None
    missing = {}
    for session, news in source:
        session_id, open_ns, end_ns = session["id"], session["open_ns"], session["end_ns"]
        if (type(session_id) is not str or not session_id or type(open_ns) is not int
                or type(end_ns) is not int or end_ns <= open_ns
                or prior_end is not None and open_ns < prior_end or session_id in chronology):
            raise ValueError("capture sessions must be unique and in nonoverlapping chronological order")
        prior_end = end_ns
        year = str(datetime.fromtimestamp(open_ns // SECOND, NY).year)
        chronology[session_id] = {"year": year, "open_ns": open_ns, "end_ns": end_ns,
                                  **metadata.get(session_id, {})}
        if session["opening_daily_iv"] is None:
            reason = "missing_same_day_atm_iv"
            chronology[session_id]["status"] = reason
            missing[session_id] = reason
            continue
        for scenario in COSTS:
            result = replay_spx_spreads({"sessions": [session], "news_flags": news, "costs": COSTS,
                                         "cost_scenario": scenario, "max_debit_dollars": CAP,
                                         "multiplier": 100})
            _record(by_candidate, result["candidates"], scenario)
            if scenario == "base":
                chronology[session_id].update(result["sessions"][0])
    if not chronology:
        raise ValueError("capture contains no sessions")
    details = []
    yearly_observations = defaultdict(Counter)
    yearly_opportunities = defaultdict(Counter)
    for session in chronology.values():
        yearly_observations[session["year"]].update(session.get("regime_observations", {}))
        yearly_opportunities[session["year"]].update(session.get("regime_opportunities", {}))
    for (scenario, _candidate_id), item in by_candidate.items():
        trades, unresolved = item["trades"], item["unresolved"]
        by_group = defaultdict(lambda: ([], []))
        for index, rows in enumerate((trades, unresolved)):
            for trade in rows:
                session_id = trade["session"]
                group = chronology[session_id]
                observed = trade["observable_regime"]
                by_group[("year", group["year"])][index].append(trade)
                by_group[("regime", observed)][index].append(trade)
                by_group[("year_and_regime", group["year"], observed)][index].append(trade)
                if "fixture" in group:
                    by_group[("fixture", group["fixture"])][index].append(trade)
        skips = Counter((chronology[row["session"]]["year"], row["observable_regime"], row["reason"])
                        for row in item["skipped"])
        for year in yearly_observations:
            by_group[("year", year)]
            for regime in REGIMES:
                by_group[("year_and_regime", year, regime)]
                by_group[("regime", regime)]
        details.append({**item, "cost_scenario": scenario,
                        "aggregate": _pnl(trades, unresolved),
                        "skipped_by_year_regime": [{"year": key[0], "regime": key[1], "reason": key[2], "count": count}
                                                    for key, count in sorted(skips.items())],
                        "breakdowns": [{"dimension": group[0], "key": list(group[1:]), **_pnl(*rows)}
                                       for group, rows in sorted(by_group.items())]})
    return {"schema_version": 1, "evidence": evidence, "product": "SPXW",
            "exercise": "european", "settlement": "pm_cash", "multiplier": 100,
            "widths_points": list(WIDTHS), "max_debit_dollars": CAP, "costs": COSTS,
            "coverage": {"sessions": chronology, "missing_same_day_atm_iv": missing,
                         "years": sorted({v["year"] for v in chronology.values()}),
                         "regime_observations_by_year": dict(yearly_observations),
                         "regime_opportunities_by_year": dict(yearly_opportunities)},
            "candidates": details}


def synthetic_report():
    sessions, news, metadata = spx_fixture()
    # State from previous sessions persists only if no clear was supplied.
    report = _report(((session, [item for item in news if item["available_ns"] <= session["end_ns"]])
                      for session in sessions), evidence="synthetic_mechanics_only", metadata=metadata)
    report["seed"] = SEED
    return report


def captured_report(path):
    """Read the provider-neutral, versioned JSON Lines capture without bulk loading."""
    from .gui.artifacts import strict_json

    digest = sha256()
    with path.open("rb") as stream:
        def read_record():
            row = stream.readline(CAPTURE_LINE_LIMIT + 1)
            if not row:
                return None
            if len(row) > CAPTURE_LINE_LIMIT:
                raise ValueError("a capture record exceeded 64 MiB")
            digest.update(row)
            record = strict_json(row)
            if type(record) is not dict:
                raise ValueError("each SPXW capture line must be a JSON object")
            return record

        header = read_record()
        if (type(header) is not dict or set(header) != {"schema_version", "product", "source", "cadence"}
                or type(header["schema_version"]) is not int or header["schema_version"] != 1
                or header["product"] != "SPXW"
                or any(not isinstance(header[x], str) or not header[x] for x in ("source", "cadence"))):
            raise ValueError("invalid SPXW capture header")

        def records():
            while (row := read_record()) is not None:
                if set(row) != {"session", "news_flags"}:
                    raise ValueError("expected one session and its applicable point-in-time news state")
                yield row["session"], row["news_flags"]

        result = _report(records(), evidence="user_supplied_capture")
    return {**result, "source": header["source"], "cadence": header["cadence"],
            "source_sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Normalized SPXW point-in-time JSONL capture")
    parser.add_argument("--output", type=Path, required=True, help="New JSON report")
    args = parser.parse_args()
    try:
        result = captured_report(args.input) if args.input else synthetic_report()
        with args.output.open("xb") as stream:
            stream.write(_serial(result) + b"\n")
    except (OSError, ValueError, OverflowError, KeyError, TypeError) as exc:
        parser.exit(2, f"qte: {exc}\n")
    print(args.output)


if __name__ == "__main__":
    main()
