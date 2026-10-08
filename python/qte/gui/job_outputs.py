"""Strict completion checks for newly produced fixed synthetic-lab jobs."""
from __future__ import annotations

import csv
from dataclasses import asdict
from hashlib import sha256
import io
import inspect
import json
import math
from pathlib import Path
import re

import qte
from qte.experiments import CandidateAssessment, ExperimentReport, ScenarioResult
from qte.regimes import RegimeConfig
from qte.strategy_lab import CashBenchmark, HoldBenchmark, _CLOCKS, prepare_lab
from qte.strategies.candidates import CandidateStrategy, FAMILIES
from qte.strategies.router import MacroRouterStrategy, RouterConfig
from qte.synthetic import GENERATOR_VERSION, SyntheticConfig, regime_at

from .artifact_io import FILE_LIMIT, METADATA_LIMIT, inventory, read_stable
from .artifacts import inspect_artifact, strict_json


_HEX = re.compile(r"[0-9a-f]{64}")
_COST_FIELDS = ("commission_bps", "spread_bps", "slippage_bps")
_CANDIDATE_FIELDS = {
    "name", "parameters_json", "history_capacity", "eligible", "factory_name",
    "factory_source_identity",
}
_WINDOW_FIELDS = {
    "name", "role", "regime", "start_ns", "end_ns", "interval_ns", "symbols",
    "currency", "dataset_hash", "source_id", "bar_count", "gap_count", "evidence",
}
_EVIDENCE_FIELDS = {
    "kind", "source", "source_sha256", "availability_policy", "corporate_action_policy",
}
_SCENARIO_FIELDS = set(ScenarioResult.__dataclass_fields__)
_ASSESSMENT_FIELDS = set(CandidateAssessment.__dataclass_fields__)
_REPORT_FIELDS = set(ExperimentReport.__dataclass_fields__)
_SCENARIO_COLUMNS = (
    "candidate", "window", "role", "regime", "cost_scenario", "status",
    "total_return", "maximum_drawdown", "trade_count", "open_trade_count",
    "fill_count", "rejected_order_count", "error",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _json(root: Path, snapshots: dict, name: str):
    limit = METADATA_LIMIT if name.endswith(".json") else FILE_LIMIT
    return strict_json(read_stable(root, name, limit, snapshots[name]))


def _canonical_text(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError
    parsed = strict_json(value.encode("utf-8"))
    if _canonical(parsed).decode("utf-8") != value:
        raise ValueError
    return parsed


def _costs(value) -> dict[str, float]:
    return {name: getattr(value, name) for name in _COST_FIELDS}


def _experiment_configuration(prepared) -> dict:
    config = prepared.experiment_config
    result = {
        name: getattr(config, name)
        for name in config.__dataclass_fields__
        if name not in {"base_costs", "stressed_costs"}
    }
    result["base_costs"] = _costs(config.base_costs)
    result["stressed_costs"] = _costs(config.stressed_costs)
    # Normalize tuples exactly as JSON does before comparing with config_json.
    return strict_json(_canonical(result))


def _candidate_names() -> tuple[str, ...]:
    return tuple(
        f"{family}-{gate}"
        for family in FAMILIES
        for gate in ("ungated", "macro")
    ) + ("macro-router", "cash", "buy-and-hold")


def _factory_identity(factory) -> str:
    path = inspect.getsourcefile(factory)
    if path is None:
        raise ValueError
    return "sha256:" + sha256(Path(path).read_bytes()).hexdigest()


def _candidates(report: dict, slate: dict, prepared, macro_hash: str) -> tuple[str, ...]:
    candidates, slate_rows = report.get("candidates"), slate.get("candidates")
    expected = _candidate_names()
    if (set(slate) != {"candidates", "warning"}
            or slate.get("warning") != "Mechanics-only synthetic trials; generated context is not predictive evidence."
            or not isinstance(candidates, list) or not isinstance(slate_rows, list)
            or len(candidates) != len(expected) or len(slate_rows) != len(expected)):
        raise ValueError
    if tuple(row.get("name") for row in candidates if isinstance(row, dict)) != expected:
        raise ValueError
    if tuple(row.get("name") for row in slate_rows if isinstance(row, dict)) != expected:
        raise ValueError
    configs = {config.kind: config for config in prepared.configs}
    config_rows = [asdict(config) for config in prepared.configs]
    router_parameters = {
        "symbol": "QTE_SYNTH", "strategy_configs": config_rows,
        "router": asdict(RouterConfig()), "macro_sha256": macro_hash,
        "dealer_overlay": "disabled",
    }
    router_parameters = strict_json(_canonical(router_parameters))
    expected_rows = {}
    for family in FAMILIES:
        config = configs[family]
        for gate in ("ungated", "macro"):
            expected_rows[f"{family}-{gate}"] = (
                {"symbol": "QTE_SYNTH", "strategy": asdict(config),
                 "macro_sha256": macro_hash if gate == "macro" else None,
                 "dealer_overlay": "disabled"},
                max(256, config.history_required), True,
                "qte.strategies.candidates.CandidateStrategy", _factory_identity(CandidateStrategy),
            )
    expected_rows["macro-router"] = (
        router_parameters, max(256, *(config.history_required for config in prepared.configs)), True,
        "qte.strategies.router.MacroRouterStrategy", _factory_identity(MacroRouterStrategy),
    )
    expected_rows["cash"] = (
        {"benchmark": "cash_no_interest"}, 1, False,
        "qte.strategy_lab.CashBenchmark", _factory_identity(CashBenchmark),
    )
    expected_rows["buy-and-hold"] = (
        {"benchmark": "one_allocation_hold",
         "allocation_fraction": prepared.configs[0].allocation_fraction}, 1, False,
        "qte.strategy_lab.HoldBenchmark", _factory_identity(HoldBenchmark),
    )
    for index, (record, shown) in enumerate(zip(candidates, slate_rows, strict=True)):
        if (not isinstance(record, dict) or set(record) != _CANDIDATE_FIELDS
                or not isinstance(shown, dict) or set(shown) != {"name", "parameters", "eligible"}
                or shown["name"] != record["name"] or type(record["history_capacity"]) is not int
                or record["history_capacity"] <= 0 or type(record["eligible"]) is not bool
                or shown["eligible"] is not record["eligible"]
                or not isinstance(record["factory_name"], str)
                or not isinstance(record["factory_source_identity"], str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", record["factory_source_identity"]) is None):
            raise ValueError
        parameters = _canonical_text(record["parameters_json"])
        if parameters != shown["parameters"] or not isinstance(parameters, dict):
            raise ValueError
        name = expected[index]
        expected_parameters, history, eligible, factory_name, factory_identity = expected_rows[name]
        if (parameters != expected_parameters or record["history_capacity"] != history
                or record["eligible"] is not eligible or record["factory_name"] != factory_name
                or record["factory_source_identity"] != factory_identity):
            raise ValueError
    return expected


def _data_case(root: Path, snapshots: dict, name: str, config: SyntheticConfig):
    rows = _csv_rows(root, snapshots, name)
    if len(rows) != config.bars:
        raise ValueError
    bars, digest = [], sha256()
    digest.update((GENERATOR_VERSION + _canonical(asdict(config)).decode("utf-8")).encode("utf-8"))
    previous_end = None
    for index, row in enumerate(rows):
        if set(row) != {"symbol", "start_ns", "end_ns", "open", "high", "low", "close", "volume"}:
            raise ValueError
        start_ns, end_ns = int(row["start_ns"]), int(row["end_ns"])
        expected_start = config.start_ns if index == 0 else previous_end
        if config.regime == "gap_shock" and index and index % 79 == 0:
            expected_start += config.interval_ns
        if (row["symbol"] != config.symbol or start_ns != expected_start
                or end_ns != start_ns + config.interval_ns):
            raise ValueError
        values = tuple(float(row[field]) for field in ("open", "high", "low", "close", "volume"))
        if any(not math.isfinite(value) for value in values):
            raise ValueError
        bars.append(qte.Bar(config.symbol, start_ns, end_ns, *values))
        digest.update(json.dumps([start_ns, end_ns, *values], separators=(",", ":"),
                                 allow_nan=False).encode("utf-8") + b"\n")
        previous_end = end_ns
    source_hash = digest.hexdigest()
    source_id = f"synthetic:{GENERATOR_VERSION}:{source_hash}"
    dataset = qte.Dataset.from_bars(bars, interval_ns=config.interval_ns, source_id=source_id)
    return dataset, source_hash, tuple(bar.end_ns for bar in bars)


def _macro_values(regime: str, index: int) -> tuple[float, float, float, float]:
    regime = regime_at(regime, index)
    growth, inflation, liquidity, volatility = 1.0, 0.0, 1.0, 12.0
    if regime in ("low_vol_range", "high_vol_range"):
        growth, liquidity = 0.0, 0.0
    if regime in ("high_vol_trend", "high_vol_range", "rebound"):
        volatility = 30.0
    if regime == "selloff":
        growth, inflation, liquidity, volatility = -1.0, 1.0, -1.0, 45.0
    if regime == "gap_shock":
        growth, inflation, liquidity, volatility = 0.0, 1.0, -1.0, 42.0
    return growth, inflation, liquidity, volatility


def _generated_evidence(root: Path, snapshots: dict, report: dict, prepared,
                        timeframe: str, clock_index: int) -> tuple[tuple[str, ...], dict]:
    rows = report.get("windows")
    expected = tuple(f"{role}-{regime}" for role in ("train", "validation", "test")
                     for regime in prepared.regimes)
    if not isinstance(rows, list) or tuple(row.get("name") for row in rows if isinstance(row, dict)) != expected:
        raise ValueError
    interval = _CLOCKS[timeframe]
    clock, case_rows, source_hashes, macro_observations = 0, [], [], []
    row_index = 0
    for role_index, role in enumerate(("train", "validation", "test")):
        for regime_index, regime in enumerate(prepared.regimes):
            name = f"{role}-{regime}"
            config = SyntheticConfig(
                regime, prepared.spec["seed"] + clock_index * 10000 + role_index * 100 + regime_index,
                bars=prepared.spec["bars_per_window"], interval_ns=interval, start_ns=clock,
            )
            dataset, source_hash, ends = _data_case(
                root, snapshots, f"{timeframe}/data/{name}.csv", config,
            )
            source_id = f"synthetic:{GENERATOR_VERSION}:{source_hash}"
            evidence = {
                "kind": "synthetic", "source": source_id, "source_sha256": source_hash,
                "availability_policy": "completed_bar_at_end; macro_reference_close_plus_one_bar",
                "corporate_action_policy": "fabricated_prices_no_corporate_actions",
            }
            expected_row = {
                "name": name, "role": role, "regime": regime,
                "start_ns": dataset.start_ns, "end_ns": dataset.end_ns,
                "interval_ns": interval, "symbols": ["QTE_SYNTH"], "currency": "USD",
                "dataset_hash": dataset.hash, "source_id": source_id,
                "bar_count": dataset.bar_count, "gap_count": dataset.gap_count,
                "evidence": evidence,
            }
            if rows[row_index] != expected_row:
                raise ValueError
            row_index += 1
            case_rows.append(asdict(config))
            source_hashes.append(source_hash)
            for index in range(0, config.bars, 16):
                values = _macro_values(regime, index)
                for feature, value in zip(
                    ("growth_z", "inflation_z", "liquidity_z", "volatility_pct"), values, strict=True,
                ):
                    macro_observations.append({
                        "feature": feature, "value": value, "reference_ns": ends[index],
                        "available_ns": ends[index] + interval,
                        "source_id": "synthetic:scripted-macro-v1",
                        "vintage_id": f"seed-{config.seed}-bar-{index}",
                    })
            clock = dataset.end_ns + interval
    generator = _json(root, snapshots, f"{timeframe}/generator.json")
    if generator != {"cases": case_rows, "source_sha256": source_hashes}:
        raise ValueError
    macro_config = RegimeConfig(
        max_macro_age_ns=32 * interval, max_volatility_age_ns=32 * interval,
        dealer_mode="ignore",
    )
    macro = {
        "config": asdict(macro_config), "observations": macro_observations,
        "dealer_observations": [],
        "evidence": "fabricated_not_observed_macro_or_dealer_data",
    }
    return expected, macro


def _nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _finite_or_none(value: object) -> bool:
    return value is None or (type(value) in (int, float) and math.isfinite(value))


def _scenarios(report: dict, candidates: tuple[str, ...], windows: tuple[str, ...]):
    rows = report.get("scenarios")
    expected = tuple(
        (candidate, window, window.split("-", 1)[0], window.split("-", 1)[1], cost)
        for window in windows if not window.startswith("test-")
        for candidate in candidates
        for cost in ("base", "stress")
    )
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError
    actual = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _SCENARIO_FIELDS:
            raise ValueError
        actual.append(tuple(row.get(name) for name in ("candidate", "window", "role", "regime", "cost_scenario")))
        if row.get("status") == "error":
            if (not isinstance(row.get("error"), str) or not row["error"] or len(row["error"]) > 1000
                    or any(row.get(name) is not None for name in (
                        "total_return", "maximum_drawdown", "average_gross_exposure", "turnover",
                        "final_equity", "normalized_config",
                    ))
                    or any(type(row.get(name)) is not int or row.get(name) != 0 for name in (
                        "trade_count", "open_trade_count", "order_count", "fill_count",
                        "rejected_order_count", "canceled_order_count",
                    ))):
                raise ValueError
        elif row.get("status") == "ok":
            if (row.get("error") is not None or not isinstance(row.get("normalized_config"), str)
                    or not row["normalized_config"]
                    or any(not _nonnegative_integer(row.get(name)) for name in (
                        "trade_count", "open_trade_count", "order_count", "fill_count",
                        "rejected_order_count", "canceled_order_count",
                    ))
                    or any(not _finite_or_none(row.get(name)) for name in (
                        "total_return", "maximum_drawdown", "average_gross_exposure", "turnover",
                    ))
                    or not _finite_or_none(row.get("final_equity")) or row.get("final_equity") is None):
                raise ValueError
        else:
            raise ValueError
    if tuple(actual) != expected:
        raise ValueError
    return rows


def _assessments(report: dict, candidates: tuple[str, ...]) -> None:
    rows = report.get("assessments")
    if (set(report) != _REPORT_FIELDS or not isinstance(rows, list)
            or tuple(row.get("candidate") for row in rows if isinstance(row, dict)) != candidates):
        raise ValueError
    for row in rows:
        exclusions = row.get("exclusions")
        if (set(row) != _ASSESSMENT_FIELDS or row.get("eligible") is not False
                or not isinstance(exclusions, list) or "synthetic_not_promotion_evidence" not in exclusions):
            raise ValueError
    if (report.get("selected_candidate") is not None
            or report.get("holdout_status") != "not_run_no_selection"
            or report.get("holdout_reasons") != []
            or report.get("parameter_status") != "frozen_before_run_not_external_preregistration"):
        raise ValueError


def _csv_rows(root: Path, snapshots: dict, name: str) -> list[dict[str, str]]:
    data = read_stable(root, name, FILE_LIMIT, snapshots[name])
    rows = csv.DictReader(io.StringIO(data.decode("utf-8")), strict=True)
    return list(rows)


def _event_logs(root: Path, snapshots: dict, timeframe: str, scenarios: list[dict]) -> set[str]:
    names: set[str] = set()
    for row in scenarios:
        if row["status"] != "ok":
            continue
        prefix = f"{timeframe}/runs/{row['window']}--{row['candidate']}--{row['cost_scenario']}"
        equity_name, fills_name, orders_name = (
            f"{prefix}/equity.csv", f"{prefix}/fills.csv", f"{prefix}/orders.csv",
        )
        names.update((equity_name, fills_name, orders_name))
        equity = _csv_rows(root, snapshots, equity_name)
        fills = _csv_rows(root, snapshots, fills_name)
        orders = _csv_rows(root, snapshots, orders_name)
        if (not equity or len(fills) != row["fill_count"] or len(orders) != row["order_count"]
                or not math.isclose(float(equity[-1]["equity"]), row["final_equity"], rel_tol=0, abs_tol=1e-9)):
            raise ValueError
    return names


def _comparison(root: Path, snapshots: dict, timeframe: str, scenarios: list[dict]) -> None:
    rows = _csv_rows(root, snapshots, f"{timeframe}/comparison.csv")
    def cell(value: object) -> str:
        return "" if value is None else str(value)

    if len(rows) != len(scenarios) or any(
        set(csv_row) != set(_SCENARIO_COLUMNS)
        or csv_row != {key: cell(report_row[key]) for key in _SCENARIO_COLUMNS}
        for csv_row, report_row in zip(rows, scenarios, strict=True)
    ):
        raise ValueError


def _verify(output: Path, source_identity: str, canonical_config_sha256: str) -> bool:
    if (not isinstance(output, Path) or not isinstance(source_identity, str) or not source_identity
            or not isinstance(canonical_config_sha256, str)
            or _HEX.fullmatch(canonical_config_sha256) is None):
        return False
    artifact = inspect_artifact(output)
    if (artifact.kind != "strategy_lab_v1" or artifact.metadata.get("integrity") != "verified"
            or artifact.metadata.get("evidence") != "synthetic"):
        return False
    snapshots = artifact.snapshots
    config = _json(output, snapshots, "configuration.json")
    if not isinstance(config, dict) or sha256(_canonical(config)).hexdigest() != canonical_config_sha256:
        return False
    prepared = prepare_lab(config)
    summary = _json(output, snapshots, "summary.json")
    if (not isinstance(summary, dict) or set(summary) != {"evidence", "experiments", "limitations"}
            or summary.get("evidence") != "synthetic_only" or not isinstance(summary.get("experiments"), list)
            or not isinstance(summary.get("limitations"), list)
            or any(not isinstance(value, str) for value in summary["limitations"])):
        return False
    experiments = summary["experiments"]
    if tuple(item.get("timeframe") for item in experiments if isinstance(item, dict)) != prepared.timeframes:
        return False

    expected_inventory = {"configuration.json", "summary.json", "complete.json"}
    protocols: list[str] = []
    for clock_index, (timeframe, summary_row) in enumerate(
            zip(prepared.timeframes, experiments, strict=True)):
        if (not isinstance(summary_row, dict) or set(summary_row) != {
                "timeframe", "protocol_id", "scenario_count", "candidate_count",
                "scenario_failures", "selected_candidate", "holdout_status",
        }):
            return False
        report_name, slate_name = f"{timeframe}/report.json", f"{timeframe}/slate.json"
        report, slate = _json(output, snapshots, report_name), _json(output, snapshots, slate_name)
        if (not isinstance(report, dict) or not isinstance(slate, dict)
                or report.get("source_identity") != source_identity):
            return False
        config_json = _canonical_text(report.get("config_json"))
        if config_json != _experiment_configuration(prepared):
            return False
        windows, expected_macro = _generated_evidence(
            output, snapshots, report, prepared, timeframe, clock_index,
        )
        macro = _json(output, snapshots, f"{timeframe}/macro.json")
        if macro != expected_macro:
            return False
        macro_hash = sha256(_canonical(macro)).hexdigest()
        candidates = _candidates(report, slate, prepared, macro_hash)
        scenarios = _scenarios(report, candidates, windows)
        _assessments(report, candidates)
        protocol = {
            "source_identity": source_identity,
            "config": config_json,
            "candidates": report["candidates"],
            "windows": report["windows"],
        }
        protocol_id = "sha256:" + sha256(_canonical(protocol)).hexdigest()
        if report.get("protocol_id") != protocol_id or summary_row.get("protocol_id") != protocol_id:
            return False
        protocols.append(protocol_id)
        failures = sum(row["status"] != "ok" for row in scenarios)
        if (type(summary_row.get("candidate_count")) is not int
                or summary_row["candidate_count"] != len(candidates)
                or type(summary_row.get("scenario_count")) is not int
                or summary_row["scenario_count"] != len(scenarios)
                or type(summary_row.get("scenario_failures")) is not int
                or summary_row["scenario_failures"] != failures
                or summary_row.get("selected_candidate") is not None
                or summary_row.get("holdout_status") != "not_run_no_selection"):
            return False
        _comparison(output, snapshots, timeframe, scenarios)
        expected_inventory.update({
            report_name, slate_name, f"{timeframe}/generator.json", f"{timeframe}/macro.json",
            f"{timeframe}/comparison.csv", f"{timeframe}/regime-decisions.csv",
        })
        expected_inventory.update(f"{timeframe}/data/{name}.csv" for name in windows)
        expected_inventory.update(_event_logs(output, snapshots, timeframe, scenarios))

    if (artifact.metadata.get("protocol_ids") != protocols
            or artifact.metadata.get("source_identities") != [source_identity] * len(prepared.timeframes)
            or set(snapshots) != expected_inventory
            or inventory(output) != snapshots):
        return False
    return True


def verify_fixed_lab_output(
    output: Path,
    source_identity: str,
    canonical_config_sha256: str,
) -> bool:
    """Return whether one new fixed-lab output is complete and causally admissible.

    This intentionally adds no restrictions to the generic catalog reader,
    which continues to support the earlier 14-entry lab artifact.
    """
    try:
        return _verify(output, source_identity, canonical_config_sha256)
    except Exception:
        return False
