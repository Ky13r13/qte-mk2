"""Run the frozen reversal slate against synthetic or captured observations."""
from __future__ import annotations

import argparse
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median
from typing import Any

from .american_option import american_option_gamma
from .weekly_reversal import replay_weekly_reversal
from .weekly_reversal_fixture import SEED, fixture_inputs

EXPERIMENT = "weekly_reversal"
COSTS = {"base": {"fee_per_contract": .65, "spread_bps": 0},
         "stress": {"fee_per_contract": 1.30, "spread_bps": 20}}
MAX_DEBIT = 650.0
MULTIPLIER = 100
REFERENCES = [
    {"title": "Cox, Ross and Rubinstein (1979), Option pricing: A simplified approach",
     "url": "https://doi.org/10.1016/0304-405X(79)90015-1",
     "note": "Binomial valuation with an exercise decision at each American option node."},
    {"title": "Nagel (2012), Evaporating Liquidity",
     "url": "https://www.nber.org/papers/w17653",
     "note": "Equity reversal returns and liquidity provision vary with market stress."},
    {"title": "Dyl, Yuksel and Zaynutdinova (2019), Price reversals and price continuations following large price movements",
     "url": "https://doi.org/10.1016/j.jbusres.2018.08.036",
     "note": "News and liquidity motivate distinct hypotheses. The paper tests longer horizons."},
    {"title": "Cboe 1-Day Volatility Index methodology",
     "url": "https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_1-Day_Volatility_Index.pdf",
     "note": "One-day implied volatility provides context. QTE uses its own same-day ATM IV proxy."},
]


def _encode(value: Any) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _summarize(replay, metadata):
    session_ids = [row["id"] for row in replay["sessions"]]
    for candidate in replay["candidates"]:
        by_session = {session: {"entries": 0, "net_pnl": 0.0, "risk_deployed": 0.0, "unresolved": 0}
                      for session in session_ids}
        for kind in ("trades", "unresolved"):
            for trade in candidate[kind]:
                cell = by_session[trade["session"]]
                cell["entries"] += 1
                cell["risk_deployed"] += trade["risk_deployed"]
                if kind == "unresolved":
                    cell["unresolved"] += 1
                    cell["net_pnl"] -= trade["risk_deployed"]
                else:
                    cell["net_pnl"] += trade["net_pnl"]
        slices = {}
        for session_id, row in by_session.items():
            tags = metadata.get(session_id, {})
            for group in ("all", tags.get("split", "captured"), tags.get("case", "captured")):
                slices.setdefault(group, {})[session_id] = row
        candidate["slices"] = {
            group: {
                "sessions": len(rows),
                "entries": sum(row["entries"] for row in rows.values()),
                "net_pnl": sum(row["net_pnl"] for row in rows.values()),
                "risk_deployed": sum(row["risk_deployed"] for row in rows.values()),
                "unresolved": sum(row["unresolved"] for row in rows.values()),
                "mean_entries": mean(row["entries"] for row in rows.values()),
                "median_entries": median(row["entries"] for row in rows.values()),
            }
            for group, rows in slices.items()
        }
    return replay


def _gamma_grid() -> dict:
    strike, vol, bump, steps = 450.0, .235, 1.0, 120
    spots = [441, 444, 447, 450, 453, 456, 459]
    days = [5, 6, 7, 8, 9]
    return {
        "model": "american_crr",
        "inputs": {"strike": strike, "iv": vol, "rate": .04, "dividend_yield": .006,
                   "steps": steps, "spot_bump": bump, "option_type": "put"},
        "spots": spots, "days": days,
        "gamma": [[american_option_gamma(spot, strike, vol, day / 365, .04, .006,
                                          "put", spot_bump=bump, steps=steps)
                   for spot in spots] for day in days],
    }


def _report(sessions, news_flags, metadata, *, evidence, source_sha256):
    runs = {}
    for scenario in COSTS:
        result = replay_weekly_reversal({"sessions": sessions, "news_flags": news_flags,
                                         "costs": COSTS, "cost_scenario": scenario,
                                         "max_debit_dollars": MAX_DEBIT, "multiplier": MULTIPLIER})
        runs[scenario] = _summarize(result, metadata)
    return {"schema_version": 1, "experiment": EXPERIMENT, "evidence": evidence,
            "source_sha256": source_sha256, "costs": COSTS, "max_debit_dollars": MAX_DEBIT,
            "multiplier": MULTIPLIER, "session_metadata": metadata,
            "runs": runs, "gamma": _gamma_grid(), "references": REFERENCES}


@lru_cache(maxsize=1)
def _synthetic_record() -> bytes:
    sessions, news, metadata = fixture_inputs()
    data_hash = sha256(_encode({"seed": SEED, "sessions": sessions, "news_flags": news})).hexdigest()
    result = _report(sessions, news, metadata, evidence="synthetic_mechanics_only", source_sha256=data_hash)
    return _encode(result)


def run_weekly_research() -> dict:
    """Return a fresh copy of the frozen synthetic result."""
    return json.loads(_synthetic_record())


def captured_research(raw: bytes) -> dict:
    """Replay one bounded, user-supplied capture, with explicitly unknown merit."""
    from .gui.artifacts import strict_json

    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("capture exceeds 32 MiB")
    value = strict_json(raw)
    if (type(value) is not dict or set(value) != {"schema_version", "sessions", "news_flags"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("unsupported capture envelope")
    return _report(value["sessions"], value["news_flags"], {}, evidence="user_supplied_capture",
                   source_sha256=sha256(raw).hexdigest())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Explicit v1 point-in-time JSON capture")
    parser.add_argument("--output", type=Path, required=True, help="New JSON file")
    args = parser.parse_args()
    try:
        if args.input is None:
            result = run_weekly_research()
        else:
            with args.input.open("rb") as stream:
                result = captured_research(stream.read(32 * 1024 * 1024 + 1))
        with args.output.open("xb") as stream:
            stream.write(_encode(result) + b"\n")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"qte: {exc}\n")
    print(args.output)


if __name__ == "__main__":
    main()
