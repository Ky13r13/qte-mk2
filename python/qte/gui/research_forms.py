"""Strict fixed-form compilation and pure research previews.

Authorization and stable input capture are caller responsibilities.  This module
only accepts already captured bytes and never opens a user-selected path.
"""
from __future__ import annotations

import copy
import csv
from dataclasses import asdict
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Any

import qte
from qte.experiments import ExperimentConfig
from qte.research_config import prepare_research
from qte.strategy_lab import _CLOCKS, prepare_lab
from qte.strategies import CandidateConfig
from qte.strategies.candidates import FAMILIES
from qte.synthetic import REGIMES


INLINE_CONFIG_ORIGIN = MappingProxyType({
    "kind": "inline_form",
    "path_base": "repository_root",
    "form_schema_version": 1,
})

_INT64_MAX = 2**63 - 1
_UINT64_MAX = 2**64 - 1
_SEED62_MAX = 2**62 - 1
_DECIMAL = re.compile(r"0|[1-9][0-9]*")
_SIGNED_DECIMAL = re.compile(r"0|-[1-9][0-9]*|[1-9][0-9]*")
_COST_FIELDS = ("commission_bps", "spread_bps", "slippage_bps")
_CANDIDATE_INTEGER_FIELDS = (
    "fast_period", "slow_period", "breakout_period", "exit_period", "atr_period",
    "contraction_window", "contraction_memory", "max_holding_bars",
)
_CANDIDATE_NUMBER_FIELDS = (
    "allocation_fraction", "contraction_quantile", "entry_z", "min_atr_fraction",
    "max_atr_fraction", "shock_fraction", "stop_atr_multiple",
)
_CANDIDATE_COMMON = ("allocation_fraction", "atr_period", "stop_atr_multiple", "max_holding_bars")
_FAMILY_FIELDS = {
    "trend": _CANDIDATE_COMMON + ("fast_period", "slow_period"),
    "breakout": _CANDIDATE_COMMON + ("breakout_period", "exit_period"),
    "contraction": _CANDIDATE_COMMON + (
        "breakout_period", "exit_period", "contraction_window",
        "contraction_quantile", "contraction_memory",
    ),
    "pullback": _CANDIDATE_COMMON + ("fast_period", "slow_period"),
    "mean_reversion": _CANDIDATE_COMMON + ("slow_period", "entry_z", "max_atr_fraction"),
    "rebound": _CANDIDATE_COMMON + ("fast_period", "min_atr_fraction", "shock_fraction"),
}


class FormError(Exception):
    """Bounded, non-diagnostic form error safe for an HTTP error response."""

    code = "invalid_configuration"
    status = 400

    def __init__(self, field: str, message: str) -> None:
        self.field = field[:160]
        self.message = message[:240]
        super().__init__(self.message)


def config_origin() -> dict[str, Any]:
    return dict(INLINE_CONFIG_ORIGIN)


def _error(field: str, message: str) -> FormError:
    return FormError(field, message)


def _object(value: object, allowed: tuple[str, ...] | set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise _error(field, "Must be an object.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise _error(field, "Contains an unsupported field.")
    return value


def _required(value: dict[str, Any], names: tuple[str, ...], field: str) -> None:
    if any(name not in value for name in names):
        raise _error(field, "A required field is missing.")


def _text(value: object, field: str, *, maximum: int = 256) -> str:
    if (type(value) is not str or not value or len(value) > maximum
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise _error(field, "Must be bounded nonempty text.")
    return value


def _choice(value: object, choices: tuple[str, ...], field: str) -> str:
    text = _text(value, field)
    if text not in choices:
        raise _error(field, "Must be one of the supported values.")
    return text


def _integer(value: object, field: str, *, minimum: int, maximum: int, signed: bool = False) -> int:
    pattern = _SIGNED_DECIMAL if signed else _DECIMAL
    if type(value) is not str or len(value) > 21 or pattern.fullmatch(value) is None:
        raise _error(field, "Must be a canonical decimal string.")
    try:
        parsed = int(value)
    except ValueError:
        raise _error(field, "Must be a canonical decimal string.") from None
    if not minimum <= parsed <= maximum:
        raise _error(field, "Is outside the supported integer range.")
    return parsed


def _number(value: object, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if type(value) not in (int, float):
        raise _error(field, "Must be a finite JSON number.")
    try:
        parsed = float(value)
        finite = math.isfinite(parsed)
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise _error(field, "Must be a finite JSON number.")
    return parsed


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise _error(field, "Must be a JSON boolean.")
    return value


def _copy_numbers(section: dict[str, Any], names: tuple[str, ...], path: str) -> dict[str, float]:
    return {name: _number(section[name], f"{path}.{name}") for name in names if name in section}  # type: ignore[misc]


def _compile_candidate(section: dict[str, Any], strategy_type: str, path: str) -> dict[str, Any]:
    allowed = {"type", "symbol", *_FAMILY_FIELDS[strategy_type]}
    section = _object(section, allowed, path)
    _required(section, ("type", "symbol"), path)
    result: dict[str, Any] = {
        "type": strategy_type,
        "symbol": _text(section["symbol"], f"{path}.symbol"),
    }
    for name in _FAMILY_FIELDS[strategy_type]:
        if name not in section:
            continue
        if name in _CANDIDATE_INTEGER_FIELDS:
            result[name] = _integer(section[name], f"{path}.{name}", minimum=1, maximum=_UINT64_MAX)
        else:
            result[name] = _number(section[name], f"{path}.{name}")
    return result


def _compile_research(wire: object) -> dict[str, Any]:
    top = _object(wire, {"data", "strategy", "engine", "execution", "risk", "sampling"}, "configuration")
    _required(top, ("data", "strategy", "engine"), "configuration")

    data = _object(top["data"], {"adapter", "path", "interval_ns", "tick_size", "currency", "action_free", "schema"}, "configuration.data")
    _required(data, ("adapter", "path", "interval_ns"), "configuration.data")
    adapter = _choice(data["adapter"], ("csv", "alpaca"), "configuration.data.adapter")
    typed_data: dict[str, Any] = {
        "adapter": adapter,
        "path": _text(data["path"], "configuration.data.path", maximum=1024),
        "interval_ns": _integer(data["interval_ns"], "configuration.data.interval_ns", minimum=1, maximum=_INT64_MAX),
    }
    if "tick_size" in data:
        typed_data["tick_size"] = _number(data["tick_size"], "configuration.data.tick_size")
    if "currency" in data:
        typed_data["currency"] = _text(data["currency"], "configuration.data.currency", maximum=32)
    if "action_free" in data:
        typed_data["action_free"] = _boolean(data["action_free"], "configuration.data.action_free")
    if "schema" in data:
        schema = _object(data["schema"], {"symbol", "timestamp", "open", "high", "low", "close", "volume"}, "configuration.data.schema")
        typed_data["schema"] = {
            name: _text(value, f"configuration.data.schema.{name}", maximum=128)
            for name, value in schema.items()
        }

    strategy = _object(top["strategy"], {"type", "symbol", "fast_period", "slow_period", "quantity", *_CANDIDATE_INTEGER_FIELDS, *_CANDIDATE_NUMBER_FIELDS}, "configuration.strategy")
    _required(strategy, ("type", "symbol"), "configuration.strategy")
    strategy_type = _choice(strategy["type"], ("moving_average_regime", *FAMILIES), "configuration.strategy.type")
    if strategy_type == "moving_average_regime":
        strategy = _object(strategy, {"type", "symbol", "fast_period", "slow_period", "quantity"}, "configuration.strategy")
        _required(strategy, ("type", "symbol", "fast_period", "slow_period", "quantity"), "configuration.strategy")
        typed_strategy = {
            "type": strategy_type,
            "symbol": _text(strategy["symbol"], "configuration.strategy.symbol"),
            "fast_period": _integer(strategy["fast_period"], "configuration.strategy.fast_period", minimum=1, maximum=_UINT64_MAX),
            "slow_period": _integer(strategy["slow_period"], "configuration.strategy.slow_period", minimum=1, maximum=_UINT64_MAX),
            "quantity": _integer(strategy["quantity"], "configuration.strategy.quantity", minimum=1, maximum=_INT64_MAX),
        }
    else:
        typed_strategy = _compile_candidate(strategy, strategy_type, "configuration.strategy")

    engine = _object(top["engine"], {"initial_cash", "history_capacity", "random_seed"}, "configuration.engine")
    _required(engine, ("initial_cash",), "configuration.engine")
    typed_engine: dict[str, Any] = {"initial_cash": _number(engine["initial_cash"], "configuration.engine.initial_cash")}
    if "history_capacity" in engine:
        typed_engine["history_capacity"] = _integer(engine["history_capacity"], "configuration.engine.history_capacity", minimum=1, maximum=_UINT64_MAX)
    if "random_seed" in engine:
        typed_engine["random_seed"] = _integer(engine["random_seed"], "configuration.engine.random_seed", minimum=0, maximum=_UINT64_MAX)

    result: dict[str, Any] = {"data": typed_data, "strategy": typed_strategy, "engine": typed_engine}
    if "execution" in top:
        section = _object(top["execution"], set(_COST_FIELDS), "configuration.execution")
        result["execution"] = _copy_numbers(section, _COST_FIELDS, "configuration.execution")
    if "risk" in top:
        section = _object(top["risk"], {"max_order_quantity", "max_symbol_allocation", "max_gross_leverage", "allow_short", "cash_floor"}, "configuration.risk")
        risk: dict[str, Any] = {}
        if "max_order_quantity" in section:
            risk["max_order_quantity"] = None if section["max_order_quantity"] is None else _integer(section["max_order_quantity"], "configuration.risk.max_order_quantity", minimum=1, maximum=_INT64_MAX)
        for name in ("max_symbol_allocation", "max_gross_leverage", "cash_floor"):
            if name in section:
                risk[name] = _number(section[name], f"configuration.risk.{name}", nullable=True)
        if "allow_short" in section:
            risk["allow_short"] = _boolean(section["allow_short"], "configuration.risk.allow_short")
        result["risk"] = risk
    if "sampling" in top:
        section = _object(top["sampling"], {"interval_ns", "timestamps_ns", "max_staleness_ns", "periods_per_year", "annual_risk_free_rate"}, "configuration.sampling")
        _required(section, ("max_staleness_ns", "periods_per_year"), "configuration.sampling")
        if ("interval_ns" in section) == ("timestamps_ns" in section):
            raise _error("configuration.sampling", "Must contain exactly one sampling grid field.")
        sampling: dict[str, Any] = {
            "max_staleness_ns": _integer(section["max_staleness_ns"], "configuration.sampling.max_staleness_ns", minimum=0, maximum=_INT64_MAX),
            "periods_per_year": _number(section["periods_per_year"], "configuration.sampling.periods_per_year"),
        }
        if "interval_ns" in section:
            sampling["interval_ns"] = _integer(section["interval_ns"], "configuration.sampling.interval_ns", minimum=1, maximum=_INT64_MAX)
        else:
            values = section["timestamps_ns"]
            if not isinstance(values, list):
                raise _error("configuration.sampling.timestamps_ns", "Must be an array of decimal strings.")
            sampling["timestamps_ns"] = [
                _integer(value, f"configuration.sampling.timestamps_ns[{index}]", minimum=-(2**63), maximum=_INT64_MAX, signed=True)
                for index, value in enumerate(values)
            ]
        if "annual_risk_free_rate" in section:
            sampling["annual_risk_free_rate"] = _number(section["annual_risk_free_rate"], "configuration.sampling.annual_risk_free_rate")
        result["sampling"] = sampling
    return result


def _unique_choices(value: object, choices: tuple[str, ...], field: str, *, required: bool) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise _error(field, "Must be a nonempty array of supported names.")
    result = [_choice(item, choices, f"{field}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise _error(field, "Must not contain duplicate names.")
    return result


def _compile_lab(wire: object) -> dict[str, Any]:
    top = _object(wire, {"schema_version", "seed", "bars_per_window", "timeframes", "regimes", "strategy_parameters", "engine", "execution", "stress_execution", "selection"}, "configuration")
    _required(top, ("schema_version", "seed", "bars_per_window", "timeframes"), "configuration")
    if type(top["schema_version"]) is not int or top["schema_version"] != 1:
        raise _error("configuration.schema_version", "Must be the JSON integer 1.")
    result: dict[str, Any] = {
        "schema_version": 1,
        "seed": _integer(top["seed"], "configuration.seed", minimum=0, maximum=_SEED62_MAX),
        "bars_per_window": _integer(top["bars_per_window"], "configuration.bars_per_window", minimum=1, maximum=_UINT64_MAX),
        "timeframes": _unique_choices(top["timeframes"], tuple(_CLOCKS), "configuration.timeframes", required=True),
    }
    if "regimes" in top:
        result["regimes"] = _unique_choices(top["regimes"], tuple(REGIMES), "configuration.regimes", required=True)
    if "strategy_parameters" in top:
        section = _object(top["strategy_parameters"], {*_CANDIDATE_INTEGER_FIELDS, *_CANDIDATE_NUMBER_FIELDS}, "configuration.strategy_parameters")
        params: dict[str, Any] = {}
        for name, value in section.items():
            params[name] = (_integer(value, f"configuration.strategy_parameters.{name}", minimum=1, maximum=_UINT64_MAX)
                            if name in _CANDIDATE_INTEGER_FIELDS else _number(value, f"configuration.strategy_parameters.{name}"))
        result["strategy_parameters"] = params
    if "engine" in top:
        section = _object(top["engine"], {"initial_cash", "max_symbol_allocation", "max_gross_leverage", "max_order_quantity"}, "configuration.engine")
        engine = _copy_numbers(section, ("initial_cash", "max_symbol_allocation", "max_gross_leverage"), "configuration.engine")
        if "max_order_quantity" in section:
            engine["max_order_quantity"] = _integer(section["max_order_quantity"], "configuration.engine.max_order_quantity", minimum=1, maximum=_INT64_MAX)
        result["engine"] = engine
    for name in ("execution", "stress_execution"):
        if name in top:
            section = _object(top[name], set(_COST_FIELDS), f"configuration.{name}")
            result[name] = _copy_numbers(section, _COST_FIELDS, f"configuration.{name}")
    if "selection" in top:
        section = _object(top["selection"], {"min_closed_trades", "min_validation_windows", "min_stressed_return", "worst_stressed_return", "max_drawdown"}, "configuration.selection")
        selection = _copy_numbers(section, ("min_stressed_return", "worst_stressed_return", "max_drawdown"), "configuration.selection")
        for name in ("min_closed_trades", "min_validation_windows"):
            if name in section:
                selection[name] = _integer(section[name], f"configuration.selection.{name}", minimum=1, maximum=_UINT64_MAX)
        result["selection"] = selection
    return result


def compile_configuration(kind: str, wire_config: object) -> dict[str, Any]:
    """Convert one allowlisted form object into an owned CLI-shaped config."""
    if kind == "research_run":
        return _compile_research(wire_config)
    if kind == "strategy_lab":
        return _compile_lab(wire_config)
    raise _error("kind", "Must be a supported fixed research kind.")


def _field(field_type: str, *, required: bool = False, nullable: bool = False, default: object = None,
           choices: tuple[str, ...] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"type": field_type, "required": required, "nullable": nullable}
    if default is not None or nullable:
        value["default"] = default
    if choices is not None:
        value["choices"] = list(choices)
    return value


def form_options() -> dict[str, Any]:
    """Return registry-derived form presentation metadata and effective defaults."""
    candidate_defaults = asdict(CandidateConfig())
    candidate_defaults.pop("kind")
    experiment = ExperimentConfig()
    base = {name: getattr(experiment.base_costs, name) for name in _COST_FIELDS}
    stress = {name: getattr(experiment.stressed_costs, name) for name in _COST_FIELDS}
    candidate_fields = {
        name: _field("integer_string" if name in _CANDIDATE_INTEGER_FIELDS else "number",
                     default=candidate_defaults[name])
        for name in (*_CANDIDATE_INTEGER_FIELDS, *_CANDIDATE_NUMBER_FIELDS)
    }
    strategies = [{
        "type": "moving_average_regime",
        "label": "Moving average regime",
        "exposed_fields": ["fast_period", "slow_period", "quantity"],
        "fields": {
            "fast_period": _field("integer_string", required=True),
            "slow_period": _field("integer_string", required=True),
            "quantity": _field("integer_string", required=True),
        },
    }]
    strategies.extend({
        "type": family,
        "label": family.replace("_", " ").title(),
        "exposed_fields": list(_FAMILY_FIELDS[family]),
        "fields": {name: copy.deepcopy(candidate_fields[name]) for name in _FAMILY_FIELDS[family]},
        "hidden_defaults": {name: candidate_defaults[name] for name in candidate_defaults if name not in _FAMILY_FIELDS[family]},
    } for family in FAMILIES)
    return {
        "schema_version": 1,
        "config_origin": config_origin(),
        "research_run": {
            "adapters": ["csv", "alpaca"],
            "strategies": strategies,
            "fields": {
                "data.adapter": _field("choice", required=True, choices=("csv", "alpaca")),
                "data.path": _field("string", required=True),
                "data.interval_ns": _field("integer_string", required=True),
                "data.tick_size": _field("number", default=0.01),
                "data.currency": _field("string", default="USD"),
                "data.action_free": _field("boolean"),
                "data.schema.*": _field("string"),
                "strategy.type": _field("choice", required=True, choices=("moving_average_regime", *FAMILIES)),
                "strategy.symbol": _field("string", required=True),
                "engine.initial_cash": _field("number", required=True),
                "engine.history_capacity": _field("integer_string", default=256),
                "engine.random_seed": _field("integer_string", default=0),
                **{f"execution.{name}": _field("number", default=0.0) for name in _COST_FIELDS},
                "risk.max_order_quantity": _field("integer_string", nullable=True, default=_INT64_MAX),
                "risk.max_symbol_allocation": _field("number", nullable=True, default=1.0),
                "risk.max_gross_leverage": _field("number", nullable=True, default=1.0),
                "risk.allow_short": _field("boolean", default=False),
                "risk.cash_floor": _field("number", nullable=True, default=0.0),
                "sampling.interval_ns": _field("integer_string"),
                "sampling.timestamps_ns": _field("integer_string_array"),
                "sampling.max_staleness_ns": _field("integer_string", required=True),
                "sampling.periods_per_year": _field("number", required=True),
                "sampling.annual_risk_free_rate": _field("number", default=0.0),
            },
            "defaults": {
                "data": {"tick_size": 0.01, "currency": "USD"},
                "engine": {"history_capacity": 256, "random_seed": 0},
                "execution": {name: getattr(qte.ExecutionCosts(), name) for name in _COST_FIELDS},
                "risk": {"max_order_quantity": _INT64_MAX, "max_symbol_allocation": 1.0,
                         "max_gross_leverage": 1.0, "allow_short": False, "cash_floor": 0.0},
                "sampling": {"annual_risk_free_rate": 0.0},
            },
        },
        "strategy_lab": {
            "timeframes": list(_CLOCKS),
            "regimes": list(REGIMES),
            "families": list(FAMILIES),
            "strategy_parameter_fields": candidate_fields,
            "fields": {
                "schema_version": _field("json_integer", required=True, default=1),
                "seed": _field("integer_string", required=True),
                "bars_per_window": _field("integer_string", required=True),
                "timeframes": _field("choice_array", required=True, choices=tuple(_CLOCKS)),
                "regimes": _field("choice_array", choices=tuple(REGIMES)),
                "engine.initial_cash": _field("number", default=experiment.initial_cash),
                "engine.max_symbol_allocation": _field("number", default=experiment.max_symbol_allocation),
                "engine.max_gross_leverage": _field("number", default=experiment.max_gross_leverage),
                "engine.max_order_quantity": _field("integer_string", default=experiment.max_order_quantity),
                **{f"execution.{name}": _field("number", default=base[name]) for name in _COST_FIELDS},
                **{f"stress_execution.{name}": _field("number", default=stress[name]) for name in _COST_FIELDS},
                "selection.min_closed_trades": _field("integer_string", default=experiment.min_closed_trades),
                "selection.min_validation_windows": _field("integer_string", default=experiment.min_validation_windows),
                "selection.min_stressed_return": _field("number", default=experiment.min_stressed_return),
                "selection.worst_stressed_return": _field("number", default=experiment.worst_stressed_return),
                "selection.max_drawdown": _field("number", default=experiment.max_drawdown),
            },
            "defaults": {
                "regimes": list(REGIMES),
                "strategy_parameters": candidate_defaults,
                "engine": {name: getattr(experiment, name) for name in (
                    "initial_cash", "max_symbol_allocation", "max_gross_leverage", "max_order_quantity")},
                "execution": base,
                "stress_execution": stress,
                "selection": {name: getattr(experiment, name) for name in (
                    "min_closed_trades", "min_validation_windows", "min_stressed_return",
                    "worst_stressed_return", "max_drawdown")},
            },
        },
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _research_assumptions(config: dict[str, Any], prepared: Any) -> list[str]:
    data, strategy = config["data"], prepared.strategy_config
    costs = {name: config.get("execution", {}).get(name, 0.0) for name in _COST_FIELDS}
    risk = {"max_order_quantity": _INT64_MAX, "max_symbol_allocation": 1.0,
            "max_gross_leverage": 1.0, "allow_short": False, "cash_floor": 0.0}
    risk.update(config.get("risk", {}))
    sampling_spec = prepared.sampling_manifest
    if sampling_spec is None:
        sampling = "disabled"
    else:
        grid = (f"interval_ns={sampling_spec['interval_ns']}" if "interval_ns" in sampling_spec
                else f"explicit_timestamps={len(sampling_spec['timestamps_ns'])}")
        sampling = (f"{sampling_spec['policy']} ({grid}), max_staleness_ns={sampling_spec['max_staleness_ns']}, "
                    f"periods_per_year={sampling_spec['periods_per_year']}, "
                    f"annual_risk_free_rate={sampling_spec.get('annual_risk_free_rate', 0)}")
    return [
        f"Source is the captured repository input at {data['path']} using the {data['adapter']} adapter; preview cannot prove untouched data.",
        f"Declared bars use interval_ns={data['interval_ns']}, currency={data.get('currency', 'USD')}, raw adjustment, and action-free assumptions required by the adapter.",
        f"Strategy {prepared.strategy_type} on {prepared.symbol} uses effective parameters {json.dumps(asdict(strategy), sort_keys=True, separators=(',', ':'))}.",
        f"Orders use deterministic next-open execution with commission/spread/slippage bps {costs['commission_bps']}/{costs['spread_bps']}/{costs['slippage_bps']}.",
        f"Effective risk limits are {json.dumps(risk, sort_keys=True, separators=(',', ':'))}.",
        f"Engine random_seed={config['engine'].get('random_seed', 0)} and history_capacity={config['engine'].get('history_capacity', 256)}.",
        f"Sampling and annualization are {sampling}; result-dependent staleness and metric validity remain runtime checks.",
        "This is one run with no optimization, selection, preregistration, or holdout claim.",
    ]


def _lab_assumptions(prepared: Any) -> list[str]:
    base = "/".join(str(getattr(prepared.base_costs, name)) for name in _COST_FIELDS)
    stress = "/".join(str(getattr(prepared.stress_costs, name)) for name in _COST_FIELDS)
    return [
        "Inputs and prices are synthetic-only mechanics fixtures and are not evidence of profitability.",
        f"The fixed complete slate contains {2 * len(FAMILIES) + 3} entries: every registered family gated and ungated, macro router, cash, and buy-and-hold.",
        f"Base and stress commission/spread/slippage bps are {base} and {stress}.",
        "Every candidate/window/cost attempt and every failure is retained in the lab report.",
        "Validation comparisons have multiple-testing risk; synthetic results cannot promote a candidate.",
        "Macro observations are scripted from generator-related state and are not observed or calibrated evidence.",
        "Dealer evidence is explicitly absent and the dealer overlay is disabled.",
        "daily_24h is a continuous synthetic clock, not an exchange session.",
        "Test-role values remain withheld by the GUI disclosure protocol; preview cannot prevent direct filesystem inspection.",
    ]


def _compiled_identity(kind: str, config: dict, data_hash: str | None) -> dict[str, str]:
    canonical_hash = sha256(_canonical(config)).hexdigest()
    fingerprint = sha256(_canonical({
        "schema_version": 1,
        "kind": kind,
        "canonical_config_sha256": canonical_hash,
        "data_sha256": data_hash,
    })).hexdigest()
    return {"canonical_config_sha256": canonical_hash, "preview_fingerprint": fingerprint}


def form_identity(kind: str, wire_config: object, data_sha256: str | None) -> dict[str, str]:
    """Compile identity after authorization, before diagnostics about stale data."""
    config = compile_configuration(kind, wire_config)
    if ((kind == "research_run" and (not isinstance(data_sha256, str)
                                     or re.fullmatch(r"[0-9a-f]{64}", data_sha256) is None))
            or (kind == "strategy_lab" and data_sha256 is not None)):
        raise _error("data_sha256", "Captured data identity does not match the research kind.")
    return _compiled_identity(kind, config, data_sha256)


def prepare_form(kind: str, wire_config: object, data_bytes: bytes | None) -> dict[str, Any]:
    """Compile and validate an authorized captured form without executing research."""
    config = compile_configuration(kind, wire_config)
    try:
        if kind == "research_run":
            if type(data_bytes) is not bytes:
                raise _error("data_bytes", "Captured research data is required.")
            prepared = prepare_research(config, data_bytes)
            data_hash: str | None = sha256(data_bytes).hexdigest()
            assumptions = _research_assumptions(config, prepared)
        else:
            if data_bytes is not None:
                raise _error("data_bytes", "Synthetic labs do not accept data bytes.")
            prepared = prepare_lab(config)
            data_hash = None
            assumptions = _lab_assumptions(prepared)
    except FormError:
        raise
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError, csv.Error):
        raise _error("configuration", "Configuration or captured data is incompatible with this fixed form.") from None
    return {
        "kind": kind,
        "config_origin": config_origin(),
        **_compiled_identity(kind, config, data_hash),
        "effective_configuration": copy.deepcopy(config),
        "assumptions": assumptions,
    }
