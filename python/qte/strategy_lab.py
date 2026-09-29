"""Offline synthetic strategy lab. No network, credentials, or brokerage access."""
from __future__ import annotations

from dataclasses import asdict
from functools import partial
import hashlib
import json
import math
from pathlib import Path

import qte
from qte.experiments import Candidate, Evidence, ExperimentConfig, ResearchWindow, run_experiment
from qte.regimes import MacroObservation, MacroRegimeFilter, RegimeConfig
from qte.strategies.candidates import CandidateConfig, CandidateStrategy, FAMILIES
from qte.strategies.router import MacroRouterStrategy, RouterConfig
from qte.synthetic import DAY_NS, HOUR_NS, REGIMES, SyntheticConfig, generate_case, regime_at


class CashBenchmark(qte.Strategy):
    def on_bar(self, context, bar):
        pass


class HoldBenchmark(qte.Strategy):
    """One successful allocation, after the first close, no final liquidation."""
    def __init__(self, symbol: str, allocation_fraction: float):
        super().__init__()
        self.symbol, self.allocation_fraction = symbol, allocation_fraction
        self.pending = None
        self.bought = False

    def on_bar(self, context, bar):
        if bar.symbol != self.symbol or self.bought or self.pending is not None:
            return
        quantity = math.floor(context.portfolio.equity * self.allocation_fraction / bar.close)
        if quantity:
            self.pending = context.submit_order(qte.market_order(self.symbol, qte.OrderSide.BUY, quantity)).order_id

    def on_fill(self, context, fill):
        if fill.order_id == self.pending:
            self.bought, self.pending = True, None

    def on_order_update(self, context, update):
        if update.order_id == self.pending:
            if update.status == qte.OrderStatus.FILLED:
                self.bought, self.pending = True, None
            elif update.status in (qte.OrderStatus.CANCELED, qte.OrderStatus.REJECTED):
                self.pending = None


def _synthetic_macro(case):
    """Scripted context for routing tests, NOT an inferred or calibrated model.

    It deliberately relates to generator state. This makes economic comparisons
    of gated/ungated results uninformative even when synthetic returns improve.
    Release is delayed one full bar beyond the reference observation's close.
    """
    observations = []
    for index, bar in enumerate(case.bars):
        if index % 16:
            continue
        regime = regime_at(case.config.regime, index)
        growth, inflation, liquidity, volatility = 1.0, 0.0, 1.0, 12.0
        if regime in ("low_vol_range", "high_vol_range"):
            growth, liquidity = 0.0, 0.0
        if regime in ("high_vol_trend", "high_vol_range", "rebound"):
            volatility = 30.0
        if regime == "selloff":
            growth, inflation, liquidity, volatility = -1.0, 1.0, -1.0, 45.0
        if regime == "gap_shock":
            growth, inflation, liquidity, volatility = 0.0, 1.0, -1.0, 42.0
        for feature, value in zip(("growth_z", "inflation_z", "liquidity_z", "volatility_pct"),
                                  (growth, inflation, liquidity, volatility), strict=True):
            observations.append(MacroObservation(
                feature, value, bar.end_ns, bar.end_ns + case.config.interval_ns,
                "synthetic:scripted-macro-v1", f"seed-{case.config.seed}-bar-{index}"))
    return tuple(observations)


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def run_lab(config_path: Path, output: Path) -> Path:
    # Reuse the existing strict parser and artifact conventions, not another CLI.
    from qte.cli import fields, write_csv, write_json
    spec = json.loads(config_path.read_text(encoding="utf-8"))
    fields(spec, ("schema_version", "seed", "bars_per_window", "timeframes", "regimes", "strategy_parameters",
                  "engine", "execution", "stress_execution", "selection"),
           ("schema_version", "seed", "bars_per_window", "timeframes"))
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("lab schema_version must be 1")
    if type(spec["seed"]) is not int or not 0 <= spec["seed"] < 2**62:
        raise ValueError("lab seed must be a nonnegative 62-bit integer")
    if type(spec["bars_per_window"]) is not int or not 128 <= spec["bars_per_window"] <= 4096:
        raise ValueError("bars_per_window must be an integer in [128, 4096]")
    clocks = {"hourly": HOUR_NS, "daily_24h": DAY_NS}
    timeframes = spec["timeframes"]
    if (not isinstance(timeframes, list) or not timeframes or any(t not in clocks for t in timeframes)
            or len(set(timeframes)) != len(timeframes)):
        raise ValueError("timeframes must be unique hourly/daily_24h names")
    regimes = spec.get("regimes", list(REGIMES))
    if (not isinstance(regimes, list) or not regimes or any(r not in REGIMES for r in regimes)
            or len(set(regimes)) != len(regimes)):
        raise ValueError("regimes must be unique supported synthetic regime names")
    params = fields(spec.get("strategy_parameters", {}),
                    set(CandidateConfig.__dataclass_fields__) - {"kind"})
    configs = tuple(CandidateConfig(kind=family, **params) for family in FAMILIES)
    if max(c.history_required for c in configs) >= spec["bars_per_window"]:
        raise ValueError("windows must leave bars after every strategy's warmup")
    engine = fields(spec.get("engine", {}), ("initial_cash", "max_symbol_allocation", "max_gross_leverage", "max_order_quantity"))
    cost_keys = ("commission_bps", "spread_bps", "slippage_bps")
    base = qte.ExecutionCosts(**fields(spec.get("execution", dict(commission_bps=1, spread_bps=2, slippage_bps=1)), cost_keys))
    stress = qte.ExecutionCosts(**fields(spec.get("stress_execution", dict(commission_bps=5, spread_bps=10, slippage_bps=5)), cost_keys))
    selection = fields(spec.get("selection", {}),
                       ("min_closed_trades", "min_validation_windows", "min_stressed_return", "worst_stressed_return", "max_drawdown"))
    experiment_config = ExperimentConfig(**engine, **selection, base_costs=base, stressed_costs=stress,
                                         required_regimes=tuple(regimes), random_seed=spec["seed"])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "configuration.json", spec)
    summaries = []
    for clock_index, timeframe in enumerate(timeframes):
        folder = output / timeframe
        folder.mkdir()
        data_folder = folder / "data"
        data_folder.mkdir()
        cases, windows, observations = [], [], []
        clock = 0
        for role_index, role in enumerate(("train", "validation", "test")):
            for regime_index, regime in enumerate(regimes):
                name = f"{role}-{regime}"
                case = generate_case(SyntheticConfig(
                    regime, spec["seed"] + clock_index * 10000 + role_index * 100 + regime_index,
                    bars=spec["bars_per_window"], interval_ns=clocks[timeframe], start_ns=clock))
                cases.append(case)
                observations.extend(_synthetic_macro(case))
                clock = case.data.end_ns + clocks[timeframe]
                windows.append(ResearchWindow(name, case.data, role, regime, Evidence(
                    "synthetic", case.data.source_id, case.source_sha256,
                    "completed_bar_at_end; macro_reference_close_plus_one_bar",
                    "fabricated_prices_no_corporate_actions")))
                write_csv(data_folder / f"{name}.csv", ("symbol", "start_ns", "end_ns", "open", "high", "low", "close", "volume"),
                          ((b.symbol, b.start_ns, b.end_ns, b.open, b.high, b.low, b.close, b.volume) for b in case.bars))
        macro_config = RegimeConfig(max_macro_age_ns=32 * clocks[timeframe],
                                    max_volatility_age_ns=32 * clocks[timeframe], dealer_mode="ignore")
        gate = MacroRegimeFilter(observations, macro_config)
        macro_payload = {"config": asdict(macro_config), "observations": [asdict(o) for o in observations],
                         "dealer_observations": [], "evidence": "fabricated_not_observed_macro_or_dealer_data"}
        macro_hash = _json_hash(macro_payload)
        write_json(folder / "macro.json", macro_payload)
        write_json(folder / "generator.json", {"cases": [asdict(c.config) for c in cases],
                                               "source_sha256": [c.source_sha256 for c in cases]})
        candidates = []
        for cfg in configs:
            for gated in (False, True):
                name = f"{cfg.kind}-{'macro' if gated else 'ungated'}"
                candidates.append(Candidate(name,
                    {"symbol": "QTE_SYNTH", "strategy": asdict(cfg),
                     "macro_sha256": macro_hash if gated else None, "dealer_overlay": "disabled"},
                    max(256, cfg.history_required),
                    partial(CandidateStrategy, "QTE_SYNTH", cfg, regime_filter=gate if gated else None)))
        router_config = RouterConfig()
        candidates.append(Candidate("macro-router",
            {"symbol": "QTE_SYNTH", "strategy_configs": [asdict(c) for c in configs],
             "router": asdict(router_config), "macro_sha256": macro_hash, "dealer_overlay": "disabled"},
            max(256, *(c.history_required for c in configs)),
            partial(MacroRouterStrategy, "QTE_SYNTH", configs, gate, router_config)))
        candidates.extend((
            Candidate("cash", {"benchmark": "cash_no_interest"}, 1, CashBenchmark, eligible=False),
            Candidate("buy-and-hold", {"benchmark": "one_allocation_hold", "allocation_fraction": configs[0].allocation_fraction},
                      1, partial(HoldBenchmark, "QTE_SYNTH", configs[0].allocation_fraction), eligible=False),
        ))
        write_json(folder / "slate.json", {"candidates": [
            {"name": c.name, "parameters": json.loads(c.parameters_json), "eligible": c.eligible} for c in candidates],
            "warning": "Mechanics-only synthetic trials; generated context is not predictive evidence."})
        # Event logs make each outcome auditable without changing engine ownership.
        runs_folder = folder / "runs"
        runs_folder.mkdir()

        def save_result(scenario, result):
            run_folder = runs_folder / f"{scenario.window}--{scenario.candidate}--{scenario.cost_scenario}"
            run_folder.mkdir()
            write_csv(run_folder / "equity.csv", ("timestamp_ns", "equity", "gross_exposure"),
                      ((p.timestamp_ns, p.equity, p.gross_exposure) for p in result.equity_curve))
            write_csv(run_folder / "fills.csv", ("id", "order_id", "symbol", "side", "quantity", "timestamp_ns", "price", "commission"),
                      ((f.id, f.order_id, f.symbol, f.side.name, f.quantity, f.effective_ns, f.executed_price, f.commission) for f in result.fills))
            write_csv(run_folder / "orders.csv", ("id", "symbol", "status", "detail"),
                      ((o.id, o.symbol, o.status.name, o.detail) for o in result.orders))

        report = run_experiment(candidates, windows, experiment_config, on_result=save_result)
        write_json(folder / "report.json", asdict(report))
        scenario_columns = ("candidate", "window", "role", "regime", "cost_scenario", "status", "total_return",
                            "maximum_drawdown", "trade_count", "open_trade_count", "fill_count", "rejected_order_count", "error")
        write_csv(folder / "comparison.csv", scenario_columns,
                  (tuple(getattr(s, key) for key in scenario_columns) for s in report.scenarios))
        write_csv(folder / "regime-decisions.csv", ("timestamp_ns", "macro_regime", "volatility_regime", "permitted_families"),
                  ((b.end_ns, d.macro_regime, d.volatility_regime, ",".join(d.permitted_families))
                   for c in cases for b in c.bars for d in (gate.explain(b.end_ns),)))
        summaries.append({"timeframe": timeframe, "protocol_id": report.protocol_id,
                          "scenario_count": len(report.scenarios), "candidate_count": len(candidates),
                          "scenario_failures": sum(s.status != "ok" for s in report.scenarios),
                          "selected_candidate": report.selected_candidate, "holdout_status": report.holdout_status})
    write_json(output / "summary.json", {"evidence": "synthetic_only", "experiments": summaries,
        "limitations": ["No live or real historical market data was used.",
                        "Macro observations are scripted from generator regimes, not independently observed predictions.",
                        "No dealer-positioning data or dealer trading-flow estimates are present.",
                        "daily_24h is a synthetic fixed-duration clock, not a US exchange session.",
                        "All-cash is permitted. No synthetic candidate can be promoted.",
                        "Each window starts fresh. Open episodes remain marked, not forcibly liquidated."]})
    write_json(output / "complete.json", {"schema_version": 1, "sha256": {
        p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.rglob("*")) if p.is_file()}})
    return output
