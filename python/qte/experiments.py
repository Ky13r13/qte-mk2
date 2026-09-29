"""Frozen-slate, chronological strategy comparisons; never a profitability claim.

Training windows are diagnostic, validation alone ranks candidates, and only the
locked candidate sees the final holdout. Synthetic observations cannot promote a
candidate. Every attempted scenario, including failures, remains in the report.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import functools
import hashlib
import inspect
import json
import math
from pathlib import Path
import statistics
from types import MappingProxyType
from typing import Any
import weakref

import qte
from qte.provenance import source_identity


def _text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonempty string")


def _integer(value: object, field_name: str, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")


def _number(value: object, field_name: str, *, minimum: float = 0.0) -> None:
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= minimum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{field_name} must be finite and >= {minimum}")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _json_copy(value: object) -> object:
    """Reject implicit coercions such as integer object keys or NaN parameters."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("candidate parameter keys must be strings")
        return {key: _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("candidate parameters must be finite JSON values")


@dataclass(frozen=True)
class Candidate:
    name: str
    parameters: Mapping[str, Any]
    history_capacity: int
    factory: Callable[[], qte.Strategy] = field(repr=False, compare=False)
    eligible: bool = field(default=True, kw_only=True)
    parameters_json: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.name, "candidate name")
        _integer(self.history_capacity, "history_capacity")
        if not isinstance(self.parameters, Mapping) or not callable(self.factory):
            raise ValueError("candidate requires a parameter mapping and callable factory")
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be bool")
        parameters = _json_copy(self.parameters)
        object.__setattr__(self, "parameters_json", _canonical(parameters))
        object.__setattr__(self, "parameters", _freeze_json(parameters))


@dataclass(frozen=True)
class Evidence:
    """Caller-supplied source declarations, not independently authenticated data."""

    kind: str
    source: str
    source_sha256: str
    availability_policy: str
    corporate_action_policy: str

    def __post_init__(self) -> None:
        if self.kind not in ("synthetic", "historical"):
            raise ValueError("evidence kind must be synthetic or historical")
        for name in ("source", "availability_policy", "corporate_action_policy"):
            _text(getattr(self, name), name)
        if (not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.source_sha256)):
            raise ValueError("source_sha256 must contain 64 lowercase hex characters")


@dataclass(frozen=True)
class ResearchWindow:
    name: str
    data: qte.Dataset = field(repr=False, compare=False)
    role: str
    regime: str
    evidence: Evidence

    def __post_init__(self) -> None:
        _text(self.name, "window name")
        _text(self.regime, "regime")
        if self.role not in ("train", "validation", "test"):
            raise ValueError("window role must be train, validation, or test")
        if not isinstance(self.data, qte.Dataset) or not isinstance(self.evidence, Evidence):
            raise ValueError("window requires a canonical Dataset and Evidence")


@dataclass(frozen=True)
class ExperimentConfig:
    initial_cash: float = 100_000.0
    base_costs: qte.ExecutionCosts = field(default_factory=lambda: qte.ExecutionCosts(1, 2, 1))
    stressed_costs: qte.ExecutionCosts = field(default_factory=lambda: qte.ExecutionCosts(5, 10, 5))
    min_closed_trades: int = 20
    min_validation_windows: int = 3
    required_regimes: tuple[str, ...] = ("low_vol", "high_vol")
    min_stressed_return: float = 0.0
    worst_stressed_return: float = -0.03
    max_drawdown: float = 0.20
    random_seed: int = 0
    max_symbol_allocation: float = 1.0
    max_gross_leverage: float = 1.0
    max_order_quantity: int = 1_000_000

    def __post_init__(self) -> None:
        _number(self.initial_cash, "initial_cash")
        if self.initial_cash == 0:
            raise ValueError("initial_cash must be positive")
        for name in ("min_closed_trades", "min_validation_windows", "max_order_quantity"):
            _integer(getattr(self, name), name)
        if self.max_order_quantity > 2**63 - 1:
            raise ValueError("max_order_quantity must fit int64")
        _integer(self.random_seed, "random_seed", 0)
        if self.random_seed > 2**64 - 1:
            raise ValueError("random_seed must fit uint64")
        for name in ("min_stressed_return", "max_drawdown", "max_symbol_allocation", "max_gross_leverage"):
            _number(getattr(self, name), name)
        _number(self.worst_stressed_return, "worst_stressed_return", minimum=-1.0)
        if self.worst_stressed_return > self.min_stressed_return:
            raise ValueError("worst_stressed_return must not exceed mean-return threshold")
        if self.max_drawdown > 1 or not 0 < self.max_symbol_allocation <= 1:
            raise ValueError("drawdown/allocation must be fractions, allocation > 0")
        if not 0 < self.max_gross_leverage <= 1:
            raise ValueError("initial ETF research is long-only and unlevered")
        if isinstance(self.required_regimes, str) or not isinstance(self.required_regimes, (tuple, list)):
            raise ValueError("required_regimes must be a sequence of distinct labels")
        regimes = tuple(self.required_regimes)
        for regime in regimes:
            _text(regime, "required regime")
        if not regimes or len(set(regimes)) != len(regimes):
            raise ValueError("required_regimes must be nonempty and distinct")
        object.__setattr__(self, "required_regimes", regimes)
        if not isinstance(self.base_costs, qte.ExecutionCosts) or not isinstance(self.stressed_costs, qte.ExecutionCosts):
            raise ValueError("cost scenarios must be ExecutionCosts")
        base, stress = _costs(self.base_costs), _costs(self.stressed_costs)
        if not any(base.values()) or any(stress[key] < base[key] for key in base) or stress == base:
            raise ValueError("base costs must be nonzero and stress must be componentwise higher")


def _costs(costs: qte.ExecutionCosts) -> dict[str, float]:
    return {name: getattr(costs, name) for name in ("commission_bps", "spread_bps", "slippage_bps")}


@dataclass(frozen=True)
class CandidateRecord:
    name: str
    parameters_json: str
    history_capacity: int
    eligible: bool
    factory_name: str
    factory_source_identity: str


@dataclass(frozen=True)
class WindowRecord:
    name: str
    role: str
    regime: str
    start_ns: int
    end_ns: int
    interval_ns: int
    symbols: tuple[str, ...]
    currency: str
    dataset_hash: str
    source_id: str
    bar_count: int
    gap_count: int
    evidence: Evidence


@dataclass(frozen=True)
class ScenarioResult:
    candidate: str
    window: str
    role: str
    regime: str
    cost_scenario: str
    status: str
    error: str | None = None
    total_return: float | None = None
    maximum_drawdown: float | None = None
    average_gross_exposure: float | None = None
    turnover: float | None = None
    trade_count: int = 0
    open_trade_count: int = 0
    order_count: int = 0
    fill_count: int = 0
    rejected_order_count: int = 0
    canceled_order_count: int = 0
    final_equity: float | None = None
    normalized_config: str | None = None


@dataclass(frozen=True)
class CandidateAssessment:
    candidate: str
    eligible: bool
    exclusions: tuple[str, ...]
    validation_score: tuple[float, float] | None
    validation_mean_stressed_return: float | None


@dataclass(frozen=True)
class ExperimentReport:
    protocol_id: str
    source_identity: str
    config_json: str
    candidates: tuple[CandidateRecord, ...]
    windows: tuple[WindowRecord, ...]
    scenarios: tuple[ScenarioResult, ...]
    assessments: tuple[CandidateAssessment, ...]
    selected_candidate: str | None
    holdout_status: str
    holdout_reasons: tuple[str, ...]
    parameter_status: str = "frozen_before_run_not_external_preregistration"
    limitations: tuple[str, ...] = (
        "Research-screening criteria are not statistical significance or a profitability claim.",
        "Source and corporate-action evidence are caller declarations; verify them independently.",
        "Each window starts in cash with fresh indicator warmup; no forced final liquidation.",
        "Regime labels are audit strata, never automatically passed into strategy callbacks.",
        "No annualization, parameter optimization, rolling walk-forward, or Monte Carlo is performed.",
        "All attempted candidate scenarios are retained; validation selection incurs multiple-testing risk.",
    )


def _candidate_record(candidate: Candidate) -> CandidateRecord:
    factory = candidate.factory
    while isinstance(factory, functools.partial):
        factory = factory.func
    if not inspect.isfunction(factory) and not inspect.isclass(factory) and not inspect.ismethod(factory):
        factory = type(factory)
    factory_name = f"{getattr(factory, '__module__', type(factory).__module__)}.{getattr(factory, '__qualname__', type(factory).__qualname__)}"
    identity = "unavailable"
    try:
        path = inspect.getsourcefile(factory)
        if path is not None and Path(path).is_file():
            identity = "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (TypeError, OSError):
        pass
    return CandidateRecord(candidate.name, candidate.parameters_json, candidate.history_capacity,
                           candidate.eligible, factory_name, identity)


def _validate_windows(windows: tuple[ResearchWindow, ...]) -> None:
    if not windows or any(not isinstance(window, ResearchWindow) for window in windows):
        raise ValueError("windows must contain ResearchWindow instances")
    if len({window.name for window in windows}) != len(windows):
        raise ValueError("window names must be unique")
    if {window.role for window in windows} != {"train", "validation", "test"}:
        raise ValueError("declare train, validation, and final test windows before running")
    order = {"train": 0, "validation": 1, "test": 2}
    first = windows[0].data
    signature = (tuple(first.symbols), first.currency, first.interval_ns)
    previous = None
    for window in windows:
        data = window.data
        if (tuple(data.symbols), data.currency, data.interval_ns) != signature:
            raise ValueError("all windows must have identical universe, currency, and interval")
        if previous is not None:
            if data.start_ns < previous.data.end_ns:
                raise ValueError("windows must be chronological and non-overlapping")
            if order[window.role] < order[previous.role]:
                raise ValueError("roles must be train, validation, then untouched final test")
        previous = window


def _run(candidate: Candidate, window: ResearchWindow, config: ExperimentConfig,
         label: str, costs: qte.ExecutionCosts, identity: str,
         seen: list[weakref.ReferenceType],
         on_result: Callable[[ScenarioResult, Any], None] | None) -> ScenarioResult:
    common = dict(candidate=candidate.name, window=window.name, role=window.role,
                  regime=window.regime, cost_scenario=label)
    try:
        strategy = candidate.factory()
        if not isinstance(strategy, qte.Strategy):
            raise TypeError("factory must return a qte.Strategy")
        # Identity comparison, not hash/equality, also supports custom strategies
        # defining __eq__. Weak references do not retain expired strategy objects.
        seen[:] = [reference for reference in seen if reference() is not None]
        if any(reference() is strategy for reference in seen):
            raise ValueError("factory reused a strategy instance")
        seen.append(weakref.ref(strategy))
        engine_config = qte.BacktestConfig(
            config.initial_cash, execution_costs=costs,
            risk_limits=qte.RiskLimits(max_order_quantity=config.max_order_quantity,
                                      max_symbol_allocation=config.max_symbol_allocation,
                                      max_gross_leverage=config.max_gross_leverage),
            history_capacity=candidate.history_capacity, random_seed=config.random_seed,
            build_identity=identity)
        result = qte.BacktestEngine(engine_config).run(window.data, strategy)
        metrics = qte.analyze(result)
        scenario = ScenarioResult(
            **common, status="ok", total_return=metrics.total_return.value,
            maximum_drawdown=metrics.maximum_drawdown.value,
            average_gross_exposure=metrics.average_gross_exposure.value,
            turnover=metrics.turnover.value, trade_count=metrics.trade_count,
            open_trade_count=len(result.open_trades), order_count=len(result.orders),
            fill_count=len(result.fills),
            rejected_order_count=sum(order.status == qte.OrderStatus.REJECTED for order in result.orders),
            canceled_order_count=sum(order.status == qte.OrderStatus.CANCELED for order in result.orders),
            final_equity=result.equity_curve[-1].equity,
            normalized_config=result.manifest.normalized_config)
    except Exception as error:
        # Preserve failures without hiding the candidate. KeyboardInterrupt and
        # SystemExit remain interruptible because they are not Exception subclasses.
        return ScenarioResult(**common, status="error", error=f"{type(error).__name__}: {error}"[:1000])
    # Artifact I/O is not strategy failure. An error here aborts the experiment;
    # callers should write their completion marker only after a returned report.
    if on_result is not None:
        on_result(scenario, result)
    return scenario


def _gates(rows: Sequence[ScenarioResult], config: ExperimentConfig, *, validation: bool) -> list[str]:
    reasons = []
    if any(row.status != "ok" for row in rows):
        reasons.append("scenario_failure")
    stress = [row for row in rows if row.cost_scenario == "stress"]
    if validation and len(stress) < config.min_validation_windows:
        reasons.append("insufficient_validation_windows")
    if not set(config.required_regimes) <= {row.regime for row in stress}:
        reasons.append("insufficient_regime_coverage")
    if sum(row.trade_count for row in stress) < config.min_closed_trades:
        reasons.append("insufficient_closed_trades")
    returns = [row.total_return for row in stress if row.total_return is not None]
    # An intentionally flat risk-off window is permissible. The equal-window
    # arithmetic mean must clear the hurdle, while each window obeys a loss floor.
    # This is a screening statistic, not a compounded portfolio return.
    if len(returns) != len(stress) or not returns or statistics.fmean(returns) <= config.min_stressed_return:
        reasons.append("stressed_return_gate_failed")
    if any(value < config.worst_stressed_return for value in returns):
        reasons.append("worst_window_return_gate_failed")
    if any(row.maximum_drawdown is None or row.maximum_drawdown > config.max_drawdown for row in rows):
        reasons.append("drawdown_gate_failed")
    return reasons


def run_experiment(candidates: Sequence[Candidate], windows: Sequence[ResearchWindow],
                   config: ExperimentConfig | None = None, *,
                   on_result: Callable[[ScenarioResult, Any], None] | None = None) -> ExperimentReport:
    """Run a frozen slate; rank validation worst/median stress return, then name.

    Training data is diagnostic only. Holdout failure never promotes a runner-up.
    A synthetic or inadequately evidenced experiment correctly selects nobody.
    """
    config = ExperimentConfig() if config is None else config
    if not isinstance(config, ExperimentConfig):
        raise ValueError("config must be ExperimentConfig")
    if on_result is not None and not callable(on_result):
        raise ValueError("on_result must be callable")
    candidates, windows = tuple(candidates), tuple(windows)
    if not candidates or any(not isinstance(candidate, Candidate) for candidate in candidates):
        raise ValueError("candidates must contain Candidate instances")
    if len({candidate.name for candidate in candidates}) != len(candidates):
        raise ValueError("candidate names must be unique")
    _validate_windows(windows)
    identity = source_identity()
    candidate_records = tuple(_candidate_record(candidate) for candidate in candidates)
    window_records = tuple(WindowRecord(
        window.name, window.role, window.regime, window.data.start_ns, window.data.end_ns,
        window.data.interval_ns, tuple(window.data.symbols), window.data.currency,
        window.data.hash, window.data.source_id, window.data.bar_count, window.data.gap_count,
        window.evidence) for window in windows)
    config_dict = {name: getattr(config, name) for name in config.__dataclass_fields__}
    config_dict.update(base_costs=_costs(config.base_costs), stressed_costs=_costs(config.stressed_costs))
    config_json = _canonical(config_dict)
    protocol = dict(source_identity=identity, config=config_dict,
                    candidates=[asdict(row) for row in candidate_records],
                    windows=[asdict(row) for row in window_records])
    protocol_id = "sha256:" + hashlib.sha256(_canonical(protocol).encode()).hexdigest()
    scenarios: list[ScenarioResult] = []
    seen: list[weakref.ReferenceType] = []
    for window in windows:
        if window.role == "test":
            continue
        for candidate in candidates:
            for label, costs in (("base", config.base_costs), ("stress", config.stressed_costs)):
                scenarios.append(_run(candidate, window, config, label, costs, identity, seen, on_result))
    assessments = []
    for record in candidate_records:
        rows = [row for row in scenarios if row.candidate == record.name]
        validation_rows = [row for row in rows if row.role == "validation"]
        reasons = _gates(validation_rows, config, validation=True)
        if any(row.status != "ok" for row in rows) and "scenario_failure" not in reasons:
            reasons.append("scenario_failure")
        if not record.eligible:
            reasons.append("benchmark_only")
        if record.factory_source_identity == "unavailable":
            reasons.append("unverified_factory_source")
        if any(window.evidence.kind != "historical" for window in windows):
            reasons.append("synthetic_not_promotion_evidence")
        elif any("synthetic" in window.data.source_id.lower() for window in windows):
            reasons.append("synthetic_source_mislabeled_historical")
        returns = [row.total_return for row in validation_rows
                   if row.cost_scenario == "stress" and row.status == "ok" and row.total_return is not None]
        score = (min(returns), statistics.median(returns)) if returns else None
        assessments.append(CandidateAssessment(record.name, not reasons, tuple(reasons), score,
                                               statistics.fmean(returns) if returns else None))
    eligible = [row for row in assessments if row.eligible]
    eligible.sort(key=lambda row: (-row.validation_score[0], -row.validation_score[1], row.candidate))
    selected = eligible[0].candidate if eligible else None
    holdout_status, holdout_reasons = "not_run_no_selection", ()
    if selected is not None:
        candidate = next(candidate for candidate in candidates if candidate.name == selected)
        holdout = []
        for window in windows:
            if window.role == "test":
                for label, costs in (("base", config.base_costs), ("stress", config.stressed_costs)):
                    holdout.append(_run(candidate, window, config, label, costs, identity, seen, on_result))
        scenarios.extend(holdout)
        holdout_reasons = tuple(_gates(holdout, config, validation=False))
        holdout_status = "failed" if holdout_reasons else "passed_research_screen_only"
    # A manifest must not silently describe source files edited during the run.
    # This detects changed on-disk inputs, not modules imported before older edits;
    # a fresh process remains required after source changes.
    if source_identity() != identity or tuple(_candidate_record(candidate) for candidate in candidates) != candidate_records:
        raise RuntimeError("source files changed during experiment; discard incomplete artifacts and rerun in a fresh process")
    return ExperimentReport(protocol_id, identity, config_json, candidate_records, window_records,
                            tuple(scenarios), tuple(assessments), selected,
                            holdout_status, holdout_reasons)
