"""Strict, captured-input preparation for declarative strategy comparisons.

This module performs no engine execution and no output I/O.  It turns one
JSON-compatible declaration plus already-captured bytes into the existing
``experiments`` types (or the legacy two-split moving-average inputs).
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

import qte
from qte.experiments import Candidate, Evidence, ExperimentConfig, ResearchWindow, _validate_windows
from qte.adapters.csv_bars import CsvBarSchema
from qte.provenance import source_identity
from qte.research_config import _load_data, _preflight_capture, fields
from qte.strategies import (
    CandidateConfig,
    CandidateStrategy,
    MovingAverageConfig,
    MovingAverageRegimeStrategy,
)
from qte.strategies.candidates import FAMILIES


_CONFIG_LIMIT = 1024 * 1024
_DATA_LIMIT = 16 * 1024 * 1024
_TOTAL_DATA_LIMIT = 64 * 1024 * 1024
_MAX_HISTORY = 100_000
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_DATA_KEYS = (
    "adapter", "path", "interval_ns", "tick_size", "currency", "action_free",
    "schema", "expected_sha256",
)
_EVIDENCE_KEYS = (
    "kind", "source", "source_sha256", "availability_policy",
    "corporate_action_policy",
)
_COST_KEYS = ("commission_bps", "spread_bps", "slippage_bps")
_EXPERIMENT_KEYS = tuple(ExperimentConfig.__dataclass_fields__)
_CANDIDATE_PARAMETER_KEYS = tuple(
    name for name in CandidateConfig.__dataclass_fields__ if name != "kind"
)


@dataclass(frozen=True)
class PreparedComparison:
    configuration: dict[str, Any]
    candidates: tuple[Candidate, ...]
    windows: tuple[ResearchWindow, ...]
    experiment_config: ExperimentConfig | None
    source_identity: str

    @property
    def symbol(self) -> str:
        """Legacy ``compare_moving_average`` compatibility."""
        return self.configuration["candidates"][0]["symbol"]

    @property
    def strategy_config(self) -> MovingAverageConfig:
        """The sole legacy moving-average configuration."""
        if self.configuration["workflow"] != "moving_average_comparison":
            raise AttributeError("strategy_config is only defined for moving_average_comparison")
        return MovingAverageConfig(**self.configuration["candidates"][0]["parameters"])

    @property
    def initial_cash(self) -> float:
        return self.configuration["experiment"]["initial_cash"]

    @property
    def stressed_costs(self) -> qte.ExecutionCosts:
        return qte.ExecutionCosts(**self.configuration["experiment"]["stressed_costs"])


def _strict_json_value(value: object, *, depth: int = 1) -> None:
    if depth > 64:
        raise ValueError("comparison configuration exceeds the nesting limit")
    if type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("comparison configuration contains invalid Unicode")
        return
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("comparison configuration contains a nonfinite number")
        return
    if type(value) is list:
        for item in value:
            _strict_json_value(item, depth=depth + 1)
        return
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("comparison configuration keys must be strings")
        if any(any(0xD800 <= ord(character) <= 0xDFFF for character in key) for key in value):
            raise ValueError("comparison configuration contains invalid Unicode")
        for item in value.values():
            _strict_json_value(item, depth=depth + 1)
        return
    raise ValueError("comparison configuration must contain only JSON values")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError(f"{label} must be a 1-64 character safe ASCII identifier")
    return value


def _text(value: object, label: str, *, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{label} must be a nonempty bounded string")
    return value


def _digest(value: object, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _number(value: object, label: str) -> int | float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{label} must be a finite JSON number")
    return value


def _data(specification: object) -> dict[str, Any]:
    spec = fields(specification, _DATA_KEYS, ("adapter", "path", "interval_ns"))
    adapter = spec["adapter"]
    if not isinstance(adapter, str):
        raise ValueError("adapter must be alpaca or csv")
    path = _text(spec["path"], "data path")
    if Path(path).is_absolute():
        raise ValueError("data path must be relative to the comparison configuration")
    interval = spec["interval_ns"]
    if type(interval) is not int or not 0 < interval <= 2**63 - 1:
        raise ValueError("interval_ns must be a positive int64")
    result: dict[str, Any] = {
        "adapter": adapter,
        "path": path,
        "interval_ns": interval,
        "tick_size": 0.01,
        "currency": "USD",
    }
    if "tick_size" in spec:
        tick = _number(spec["tick_size"], "tick_size")
        if tick <= 0:
            raise ValueError("tick_size must be positive")
        result["tick_size"] = tick
    if "currency" in spec:
        result["currency"] = _text(spec["currency"], "currency", limit=32)
    if "action_free" in spec:
        if type(spec["action_free"]) is not bool:
            raise ValueError("action_free must be bool")
        result["action_free"] = spec["action_free"]
    if "schema" in spec:
        schema = fields(
            spec["schema"],
            ("symbol", "timestamp", "open", "high", "low", "close", "volume"),
        )
        result["schema"] = {
            key: _text(value, f"schema {key}", limit=256) for key, value in schema.items()
        }
    if adapter == "csv":
        result["schema"] = asdict(CsvBarSchema(**result.get("schema", {})))
    if "expected_sha256" in spec:
        result["expected_sha256"] = _digest(spec["expected_sha256"], "expected_sha256")
    # Reuse the adapter boundary's option rules without reading the source.
    _preflight_capture(result)
    return result


def _evidence(specification: object) -> dict[str, Any]:
    spec = fields(specification, _EVIDENCE_KEYS, _EVIDENCE_KEYS)
    evidence = Evidence(
        spec["kind"],
        _text(spec["source"], "evidence source"),
        _digest(spec["source_sha256"], "source_sha256"),
        _text(spec["availability_policy"], "availability_policy"),
        _text(spec["corporate_action_policy"], "corporate_action_policy"),
    )
    if evidence.availability_policy != "bar_end_ns":
        raise ValueError("availability_policy must be bar_end_ns for the current core")
    return asdict(evidence)


def _costs(specification: object, defaults: qte.ExecutionCosts) -> dict[str, int | float]:
    spec = fields(specification, _COST_KEYS)
    values = {name: getattr(defaults, name) for name in _COST_KEYS}
    for name, value in spec.items():
        number = _number(value, name)
        if number < 0:
            raise ValueError(f"{name} must be nonnegative")
        values[name] = number
    # The binding owns its final range validation.
    validated = qte.ExecutionCosts(**values)
    return {name: getattr(validated, name) for name in _COST_KEYS}


def _candidate(specification: object) -> dict[str, Any]:
    spec = fields(
        specification, ("name", "type", "symbol", "parameters", "eligible"),
        ("name", "type", "symbol", "parameters"),
    )
    name = _identifier(spec["name"], "candidate name")
    strategy_type = spec["type"]
    if not isinstance(strategy_type, str) or strategy_type not in ("moving_average_regime", *FAMILIES):
        raise ValueError("unsupported strategy type")
    symbol = _text(spec["symbol"], "candidate symbol", limit=64)
    eligible = spec.get("eligible", True)
    if type(eligible) is not bool:
        raise ValueError("eligible must be bool")
    parameters = spec["parameters"]
    if strategy_type == "moving_average_regime":
        parameters = fields(parameters, ("fast_period", "slow_period", "quantity"),
                            ("fast_period", "slow_period", "quantity"))
        validated: MovingAverageConfig | CandidateConfig = MovingAverageConfig(**parameters)
        if validated.quantity > 2**63 - 1:
            raise ValueError("quantity must fit int64")
        normalized_parameters = asdict(validated)
        history_required = validated.slow_period
    else:
        parameters = fields(parameters, _CANDIDATE_PARAMETER_KEYS)
        validated = CandidateConfig(kind=strategy_type, **parameters)
        normalized_parameters = asdict(validated)
        normalized_parameters.pop("kind")
        history_required = validated.history_required
    if history_required > _MAX_HISTORY:
        raise ValueError("candidate lookback must not exceed 100000 bars")
    return {
        "name": name,
        "type": strategy_type,
        "symbol": symbol,
        "parameters": normalized_parameters,
        "eligible": eligible,
    }


def _historical_experiment(specification: object) -> dict[str, Any]:
    spec = fields(specification, _EXPERIMENT_KEYS)
    defaults = ExperimentConfig()
    values: dict[str, Any] = {}
    for name in _EXPERIMENT_KEYS:
        if name in ("base_costs", "stressed_costs"):
            supplied = spec.get(name, {})
            values[name] = qte.ExecutionCosts(**_costs(supplied, getattr(defaults, name)))
        elif name == "required_regimes":
            supplied = spec.get(name, list(defaults.required_regimes))
            if type(supplied) is not list:
                raise ValueError("required_regimes must be a JSON array")
            if any(not isinstance(item, str) for item in supplied):
                raise ValueError("required_regimes must contain strings")
            values[name] = tuple(supplied)
        else:
            values[name] = spec.get(name, getattr(defaults, name))
    validated = ExperimentConfig(**values)
    result = {
        name: getattr(validated, name)
        for name in _EXPERIMENT_KEYS if name not in ("base_costs", "stressed_costs", "required_regimes")
    }
    result["required_regimes"] = list(validated.required_regimes)
    result["base_costs"] = {name: getattr(validated.base_costs, name) for name in _COST_KEYS}
    result["stressed_costs"] = {name: getattr(validated.stressed_costs, name) for name in _COST_KEYS}
    return result


def _legacy_experiment(specification: object) -> dict[str, Any]:
    spec = fields(specification, ("initial_cash", "stressed_costs"))
    initial_cash = spec.get("initial_cash", 100_000.0)
    number = _number(initial_cash, "initial_cash")
    if number <= 0:
        raise ValueError("initial_cash must be positive")
    stressed = _costs(spec.get("stressed_costs", {}), qte.ExecutionCosts(5, 10, 5))
    # The baseline is intentionally absent: compare_moving_average fixes it at zero.
    return {"initial_cash": number, "stressed_costs": stressed}


def compile_comparison_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one declarative comparison without doing I/O."""
    _strict_json_value(configuration)
    config = copy.deepcopy(configuration)
    fields(config, ("schema_version", "workflow", "windows", "candidates", "experiment"),
           ("schema_version", "workflow", "windows", "candidates", "experiment"))
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    workflow = config["workflow"]
    if workflow not in ("historical_experiment", "moving_average_comparison"):
        raise ValueError("unsupported comparison workflow")
    raw_windows = config["windows"]
    minimum = 3 if workflow == "historical_experiment" else 2
    maximum = 24 if workflow == "historical_experiment" else 2
    if type(raw_windows) is not list or not minimum <= len(raw_windows) <= maximum:
        raise ValueError(f"{workflow} requires {minimum}-{maximum} windows")
    windows = []
    for raw in raw_windows:
        spec = fields(raw, ("name", "role", "regime", "data", "evidence"),
                      ("name", "role", "regime", "data", "evidence"))
        role = spec["role"]
        if role not in ("train", "validation", "test"):
            raise ValueError("window role must be train, validation, or test")
        windows.append({
            "name": _identifier(spec["name"], "window name"),
            "role": role,
            "regime": _identifier(spec["regime"], "window regime"),
            "data": _data(spec["data"]),
            "evidence": _evidence(spec["evidence"]),
        })
    names = [window["name"] for window in windows]
    if len(set(names)) != len(names):
        raise ValueError("window names must be unique")

    raw_candidates = config["candidates"]
    if type(raw_candidates) is not list or not 1 <= len(raw_candidates) <= 32:
        raise ValueError("candidates must contain 1-32 entries")
    candidates = [_candidate(spec) for spec in raw_candidates]
    names = [candidate["name"] for candidate in candidates]
    if len(set(names)) != len(names):
        raise ValueError("candidate names must be unique")

    if workflow == "historical_experiment":
        roles = [window["role"] for window in windows]
        if set(roles) != {"train", "validation", "test"} or roles.count("test") != 1 or roles[-1] != "test":
            raise ValueError("historical experiment requires train, validation, and one final test window")
        order = {"train": 0, "validation": 1, "test": 2}
        if any(order[right] < order[left] for left, right in zip(roles, roles[1:])):
            raise ValueError("historical window roles must be ordered train, validation, then test")
        experiment = _historical_experiment(config["experiment"])
    else:
        if [window["role"] for window in windows] != ["train", "test"]:
            raise ValueError("moving-average comparison requires exactly train then test windows")
        if len(candidates) != 1 or candidates[0]["type"] != "moving_average_regime":
            raise ValueError("moving-average comparison requires exactly one moving_average_regime candidate")
        if not candidates[0]["eligible"]:
            raise ValueError("the moving-average comparison candidate must be eligible")
        experiment = _legacy_experiment(config["experiment"])
    return {
        "schema_version": 1,
        "workflow": workflow,
        "windows": windows,
        "candidates": candidates,
        "experiment": experiment,
    }


def _read_stable_regular(path: Path, limit: int, label: str) -> tuple[bytes, tuple[int, int]]:
    # Walk every parent by descriptor so a symlink in the path cannot bypass the
    # leaf O_NOFOLLOW check. ``abspath`` is lexical: it removes ``..`` without
    # resolving links, preserving config-relative CLI paths.
    absolute = Path(os.path.abspath(path))
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_descriptor: int | None = None
    try:
        parent_descriptor = os.open(absolute.anchor, directory_flags)
        for component in absolute.parts[1:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
            parent_descriptor = next_descriptor
    except OSError as error:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise ValueError(f"{label} is unavailable") from error
    assert parent_descriptor is not None
    try:
        leaf = absolute.name
        try:
            before = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as error:
            raise ValueError(f"{label} is unavailable") from error
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError(f"{label} must be a bounded regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(leaf, flags, dir_fd=parent_descriptor)
        except OSError as error:
            raise ValueError(f"{label} changed during capture")
        try:
            opened = os.fstat(descriptor)
            stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if (not stat.S_ISREG(opened.st_mode)
                    or any(getattr(opened, key) != getattr(before, key) for key in stable)):
                raise ValueError(f"{label} changed during capture")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read(limit + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        try:
            final = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as error:
            raise ValueError(f"{label} changed during capture") from error
    finally:
        os.close(parent_descriptor)
    if (len(content) != opened.st_size or len(content) > limit
            or any(getattr(after, key) != getattr(opened, key) for key in stable)
            or any(getattr(final, key) != getattr(opened, key) for key in stable)):
        raise ValueError(f"{label} changed during capture")
    return content, (opened.st_dev, opened.st_ino)


def _parse_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("comparison configuration contains a duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise ValueError("comparison configuration contains a nonfinite number")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("comparison configuration is invalid JSON") from error
    _strict_json_value(value)
    if type(value) is not dict:
        raise ValueError("comparison configuration must be an object")
    return value


def capture_comparison_inputs(config_path: Path) -> tuple[dict[str, Any], tuple[bytes, ...]]:
    """Stably capture one bounded config and every distinct window source once."""
    path = Path(config_path)
    raw_config, _ = _read_stable_regular(path, _CONFIG_LIMIT, "comparison configuration")
    configuration = _parse_json(raw_config)
    normalized = compile_comparison_configuration(configuration)
    captured: list[bytes] = []
    seen_files: set[tuple[int, int]] = set()
    remaining = _TOTAL_DATA_LIMIT
    for index, window in enumerate(normalized["windows"]):
        if remaining <= 0:
            raise ValueError("captured comparison data exceeds the total size limit")
        source = path.parent / window["data"]["path"]
        raw, identity = _read_stable_regular(
            source, min(_DATA_LIMIT, remaining), f"window {index} data",
        )
        if identity in seen_files:
            raise ValueError("each comparison window must identify a distinct data file")
        seen_files.add(identity)
        captured.append(raw)
        remaining -= len(raw)
    return configuration, tuple(captured)


def _build_candidate(spec: dict[str, Any]) -> tuple[Candidate, MovingAverageConfig | CandidateConfig]:
    if spec["type"] == "moving_average_regime":
        strategy_config: MovingAverageConfig | CandidateConfig = MovingAverageConfig(**spec["parameters"])
        factory = functools.partial(MovingAverageRegimeStrategy, spec["symbol"], strategy_config)
        required = strategy_config.slow_period
    else:
        strategy_config = CandidateConfig(kind=spec["type"], **spec["parameters"])
        factory = functools.partial(CandidateStrategy, spec["symbol"], strategy_config)
        required = strategy_config.history_required
    recorded_parameters = {
        "type": spec["type"], "symbol": spec["symbol"], **spec["parameters"],
    }
    return Candidate(
        spec["name"], recorded_parameters, max(256, required), factory,
        eligible=spec["eligible"],
    ), strategy_config


def prepare_comparison(
    configuration: dict[str, Any], captured_windows: tuple[bytes, ...],
) -> PreparedComparison:
    """Construct a fresh, deterministic comparison plan without running an engine."""
    if type(captured_windows) is not tuple or any(type(raw) is not bytes for raw in captured_windows):
        raise TypeError("comparison windows must be a tuple of captured bytes")
    normalized = compile_comparison_configuration(configuration)
    if len(captured_windows) != len(normalized["windows"]):
        raise ValueError("captured window count does not match configuration")

    windows: list[ResearchWindow] = []
    for spec, raw in zip(normalized["windows"], captured_windows, strict=True):
        digest = hashlib.sha256(raw).hexdigest()
        if spec["evidence"]["source_sha256"] != digest:
            raise ValueError("evidence source_sha256 does not match captured window bytes")
        loaded = _load_data(spec["data"], raw)
        evidence = Evidence(**spec["evidence"])
        windows.append(ResearchWindow(
            spec["name"], loaded.dataset, spec["role"], spec["regime"], evidence,
        ))

    candidates_and_configs = tuple(_build_candidate(spec) for spec in normalized["candidates"])
    candidates = tuple(item[0] for item in candidates_and_configs)
    for spec in normalized["candidates"]:
        if any(spec["symbol"] not in window.data.symbols for window in windows):
            raise ValueError(f"candidate symbol {spec['symbol']!r} must exist in every window")

    window_tuple = tuple(windows)
    if normalized["workflow"] == "historical_experiment":
        _validate_windows(window_tuple)
        if sum(window.role == "test" for window in window_tuple) != 1 or window_tuple[-1].role != "test":
            raise ValueError("historical experiment requires one untouched final test")
        experiment = normalized["experiment"]
        experiment_config: ExperimentConfig | None = ExperimentConfig(
            **{
                **experiment,
                "required_regimes": tuple(experiment["required_regimes"]),
                "base_costs": qte.ExecutionCosts(**experiment["base_costs"]),
                "stressed_costs": qte.ExecutionCosts(**experiment["stressed_costs"]),
            }
        )
    else:
        train, test = window_tuple
        if train.data.end_ns > test.data.start_ns:
            raise ValueError("train/test splits must be chronological and non-overlapping")
        if train.data.interval_ns != test.data.interval_ns:
            raise ValueError("train/test intervals must match")
        if train.data.currency != test.data.currency:
            raise ValueError("train/test valuation currencies must match")
        experiment_config = None

    return PreparedComparison(
        copy.deepcopy(normalized), candidates, window_tuple, experiment_config, source_identity(),
    )
