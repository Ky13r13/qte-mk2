"""Shared single-run research preparation; no engine execution or artifact I/O."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import qte
from qte.adapters.common import AdapterResult
from qte.adapters.csv_bars import CsvBarSchema, _load_csv_bytes, _validate_csv_options
from qte.adapters.alpaca import _load_alpaca_bytes
from qte.provenance import source_identity
from qte.strategies import (
    CandidateConfig,
    CandidateStrategy,
    MovingAverageConfig,
    MovingAverageRegimeStrategy,
)
from qte.strategies.candidates import FAMILIES
from qte.router_config import RouterProfile, build_macro_router, prepare_router_profile


_INT64_MAX = 2**63 - 1


def fields(value, allowed, required=()):
    """Return one strict object section; retained as ``qte.cli.fields`` too."""
    if not isinstance(value, dict):
        raise ValueError("configuration section must be an object")
    if set(value) - set(allowed):
        raise ValueError(f"unknown configuration keys: {sorted(set(value) - set(allowed))}")
    if set(required) - set(value):
        raise ValueError(f"missing configuration keys: {sorted(set(required) - set(value))}")
    return value


@dataclass(frozen=True)
class PreparedResearch:
    """Owned inputs needed for one fresh engine run.

    Constructing this value validates configuration and canonical input data but
    deliberately does not run the engine. Result-dependent sampling checks stay
    in the C++ analytics path.
    """

    configuration: dict[str, Any]
    loaded: AdapterResult
    strategy: qte.Strategy
    strategy_config: MovingAverageConfig | CandidateConfig | RouterProfile
    strategy_type: str
    symbol: str
    engine_config: qte.BacktestConfig
    sampling: qte.SamplingConfig | None
    annualization: qte.AnnualizationConfig | None
    sampling_manifest: dict[str, Any] | None
    source_identity: str


def _sections(configuration: dict[str, Any]):
    fields(
        configuration,
        ("data", "strategy", "engine", "execution", "risk", "sampling"),
        ("data", "strategy", "engine"),
    )
    return fields(
        configuration["data"],
        ("adapter", "path", "interval_ns", "tick_size", "currency", "action_free", "schema", "expected_sha256"),
        ("adapter", "path", "interval_ns"),
    )


def capture_research_inputs(config_path: Path) -> tuple[dict[str, Any], bytes]:
    """Read a CLI configuration and its referenced input exactly once each."""
    path = Path(config_path)
    configuration = json.loads(path.read_text())
    data = _sections(configuration)
    source = path.parent / data["path"]
    _preflight_capture(data)
    return configuration, source.read_bytes()


def _preflight_capture(data: dict[str, Any]) -> None:
    """Preserve adapter option failures that historically preceded source I/O."""
    if data["adapter"] == "alpaca":
        if "schema" in data or "action_free" in data:
            raise ValueError("Alpaca assumptions must be in fixture envelope")
        return
    if data["adapter"] != "csv":
        raise ValueError("adapter must be alpaca or csv")
    schema = CsvBarSchema(**data.get("schema", {}))
    _validate_csv_options(
        interval_ns=data["interval_ns"], schema=schema, action_free=data.get("action_free")
    )


def _load_data(data: dict[str, Any], raw: bytes) -> AdapterResult:
    if "expected_sha256" in data:
        expected = data["expected_sha256"]
        if (not isinstance(expected, str) or len(expected) != 64
                or any(char not in "0123456789abcdef" for char in expected)):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 hex digest")
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("captured data does not match expected_sha256")
    kwargs = {key: data[key] for key in ("interval_ns", "tick_size", "currency") if key in data}
    if data["adapter"] == "alpaca":
        if "schema" in data or "action_free" in data:
            raise ValueError("Alpaca assumptions must be in fixture envelope")
        return _load_alpaca_bytes(raw, **kwargs)
    if data["adapter"] == "csv":
        return _load_csv_bytes(
            raw,
            **kwargs,
            action_free=data.get("action_free"),
            schema=CsvBarSchema(**data.get("schema", {})),
        )
    raise ValueError("adapter must be alpaca or csv")


def _strategy(configuration: dict[str, Any], loaded: AdapterResult):
    original = configuration["strategy"]
    if isinstance(original, dict) and original.get("type") == "macro_router":
        spec = fields(original, ("type", "symbol", "profile"), ("type", "symbol", "profile"))
        strategy_config = prepare_router_profile(spec["profile"])
        strategy = build_macro_router(spec["symbol"], strategy_config)
        history_required = strategy.history_required
        if spec["symbol"] not in loaded.dataset.symbols:
            raise ValueError("strategy symbol not in dataset")
        return spec, strategy_config, strategy, history_required
    spec = fields(
        configuration["strategy"],
        {"type", "symbol", "quantity"} | (set(CandidateConfig.__dataclass_fields__) - {"kind"}),
        ("type", "symbol"),
    )
    if spec["type"] == "moving_average_regime":
        fields(
            spec,
            ("type", "symbol", "fast_period", "slow_period", "quantity"),
            ("type", "symbol", "fast_period", "slow_period", "quantity"),
        )
        strategy_config = MovingAverageConfig(spec["fast_period"], spec["slow_period"], spec["quantity"])
        strategy = MovingAverageRegimeStrategy(spec["symbol"], strategy_config)
        history_required = strategy_config.slow_period
    elif spec["type"] in FAMILIES:
        if "quantity" in spec:
            raise ValueError("candidate strategies use allocation_fraction, not quantity")
        strategy_config = CandidateConfig(
            kind=spec["type"], **{key: value for key, value in spec.items() if key not in ("type", "symbol")}
        )
        strategy = CandidateStrategy(spec["symbol"], strategy_config)
        history_required = strategy_config.history_required
    else:
        raise ValueError("unsupported strategy type")
    if spec["symbol"] not in loaded.dataset.symbols:
        raise ValueError("strategy symbol not in dataset")
    return spec, strategy_config, strategy, history_required


def _finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _sampling(configuration: dict[str, Any], loaded: AdapterResult):
    original = configuration.get("sampling")
    if original is None:
        return None, None, None
    spec = fields(
        original,
        ("timestamps_ns", "interval_ns", "max_staleness_ns", "periods_per_year", "annual_risk_free_rate"),
        ("max_staleness_ns", "periods_per_year"),
    )
    if ("interval_ns" in spec) == ("timestamps_ns" in spec):
        raise ValueError("sampling needs exactly one of interval_ns or timestamps_ns")
    if "interval_ns" in spec:
        interval = spec["interval_ns"]
        if type(interval) is not int or interval <= 0:
            raise ValueError("sampling interval must be a positive integer")
        start, end = loaded.dataset.start_ns, loaded.dataset.end_ns
        if (end - start) % interval:
            raise ValueError("sampling interval must divide run duration")
        if (end - start) // interval > 1_000_000:
            raise ValueError("sampling grid too large")
        times = list(range(start, end + 1, interval))
    else:
        supplied = spec["timestamps_ns"]
        if not isinstance(supplied, list) or any(
            type(value) is not int or not -(2**63) <= value <= _INT64_MAX for value in supplied
        ):
            raise ValueError("sampling timestamps must be signed 64-bit integers")
        times = list(supplied)
    if len(times) < 2:
        raise ValueError("sampling requires at least two timestamps")
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("sampling timestamps must strictly increase")
    if times[0] != loaded.dataset.start_ns or times[-1] != loaded.dataset.end_ns:
        raise ValueError("sampling timestamps must retain dataset start and end")
    staleness = spec["max_staleness_ns"]
    if type(staleness) is not int or not 0 <= staleness <= _INT64_MAX:
        raise ValueError("sampling maximum staleness must be a nonnegative int64")
    periods = spec["periods_per_year"]
    risk_free = spec.get("annual_risk_free_rate", 0)
    if not _finite(periods) or periods <= 0 or not _finite(risk_free) or risk_free <= -1:
        raise ValueError("invalid annualization configuration")
    sampling = qte.SamplingConfig(times, staleness)
    annualization = qte.AnnualizationConfig(periods, risk_free)
    manifest = {**spec, "timestamps_ns": times, "policy": "last_event_at_or_before_v1"}
    return sampling, annualization, manifest


def prepare_research(
    configuration: dict[str, Any], data_bytes: bytes, *, build_identity: str | None = None,
) -> PreparedResearch:
    """Validate captured inputs and construct a fresh run plan without executing it."""
    if type(data_bytes) is not bytes:
        raise TypeError("research data must be captured bytes")
    config = copy.deepcopy(configuration)
    data = _sections(config)
    loaded = _load_data(data, data_bytes)
    spec, strategy_config, strategy, history_required = _strategy(config, loaded)
    engine = fields(config["engine"], ("initial_cash", "history_capacity", "random_seed"), ("initial_cash",))
    if engine.get("history_capacity", 256) < history_required:
        raise ValueError("history capacity is smaller than strategy requirement")
    costs = fields(config.get("execution", {}), ("commission_bps", "spread_bps", "slippage_bps"))
    risk = fields(
        config.get("risk", {}),
        ("max_order_quantity", "max_symbol_allocation", "max_gross_leverage", "allow_short", "cash_floor"),
    )
    sampling, annualization, sampling_manifest = _sampling(config, loaded)
    identity = source_identity() if build_identity is None else build_identity
    engine_config = qte.BacktestConfig(
        **engine,
        execution_costs=qte.ExecutionCosts(**costs),
        risk_limits=qte.RiskLimits(**risk),
        build_identity=identity,
    )
    return PreparedResearch(
        config,
        loaded,
        strategy,
        strategy_config,
        spec["type"],
        spec["symbol"],
        engine_config,
        sampling,
        annualization,
        sampling_manifest,
        identity,
    )
