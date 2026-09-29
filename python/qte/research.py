from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from qte.provenance import source_identity

from qte import (
    BacktestConfig,
    BacktestEngine,
    Dataset,
    ExecutionCosts,
    RiskLimits,
    analyze,
)
from qte.strategies import MovingAverageConfig, MovingAverageRegimeStrategy


@dataclass(frozen=True)
class ScenarioResult:
    sample: str
    cost_scenario: str
    total_return: float | None
    maximum_drawdown: float | None
    trade_count: int
    order_count: int
    fill_count: int
    dataset_hash: str
    normalized_config: str
    strategy_config: str
    source_identity: str
    source_id: str
    start_ns: int
    end_ns: int


@dataclass(frozen=True)
class ComparisonReport:
    hypothesis: str
    parameter_status: str
    scenarios: tuple[ScenarioResult, ...]


def _run_scenario(
    data: Dataset,
    *,
    sample: str,
    cost_scenario: str,
    symbol: str,
    strategy_config: MovingAverageConfig,
    initial_cash: float,
    costs: ExecutionCosts,
) -> ScenarioResult:
    config = BacktestConfig(
        initial_cash,
        execution_costs=costs,
        risk_limits=RiskLimits(),
        random_seed=0,
        build_identity=source_identity(),
        history_capacity=max(256, strategy_config.slow_period),
    )
    result = BacktestEngine(config).run(
        data, MovingAverageRegimeStrategy(symbol, strategy_config)
    )
    metrics = analyze(result)
    return ScenarioResult(
        sample=sample,
        cost_scenario=cost_scenario,
        total_return=metrics.total_return.value,
        maximum_drawdown=metrics.maximum_drawdown.value,
        trade_count=metrics.trade_count,
        order_count=len(result.orders),
        fill_count=len(result.fills),
        dataset_hash=result.manifest.dataset_hash,
        normalized_config=result.manifest.normalized_config,
        strategy_config=json.dumps({'symbol': symbol, **asdict(strategy_config)}, sort_keys=True),
        source_identity=result.manifest.build_identity,
        source_id=data.source_id,
        start_ns=data.start_ns,
        end_ns=data.end_ns,
    )


def compare_moving_average(
    *,
    in_sample: Dataset,
    out_of_sample: Dataset,
    symbol: str,
    strategy_config: MovingAverageConfig,
    initial_cash: float = 100_000.0,
    stressed_costs: ExecutionCosts = ExecutionCosts(
        commission_bps=5.0, spread_bps=10.0, slippage_bps=5.0
    ),
) -> ComparisonReport:
    """Run pre-registered parameters; this function performs no optimization."""
    if in_sample.end_ns > out_of_sample.start_ns:
        raise ValueError('train/test splits must be chronological and non-overlapping')
    if in_sample.interval_ns != out_of_sample.interval_ns:
        raise ValueError('train/test intervals must match')
    if in_sample.currency != out_of_sample.currency:
        raise ValueError('train/test valuation currencies must match')
    if symbol not in in_sample.symbols or symbol not in out_of_sample.symbols:
        raise ValueError('strategy symbol must exist in both splits')
    scenarios = []
    for sample, data in (
        ("in_sample", in_sample),
        ("out_of_sample", out_of_sample),
    ):
        scenarios.append(
            _run_scenario(
                data,
                sample=sample,
                cost_scenario="baseline_zero_cost",
                symbol=symbol,
                strategy_config=strategy_config,
                initial_cash=initial_cash,
                costs=ExecutionCosts(),
            )
        )
        scenarios.append(
            _run_scenario(
                data,
                sample=sample,
                cost_scenario="stressed_costs",
                symbol=symbol,
                strategy_config=strategy_config,
                initial_cash=initial_cash,
                costs=stressed_costs,
            )
        )
    return ComparisonReport(
        hypothesis=(
            "A short SMA above a long SMA identifies a long regime; exit when "
            "the short SMA falls below the long SMA."
        ),
        parameter_status="pre_registered_not_optimized",
        scenarios=tuple(scenarios),
    )
