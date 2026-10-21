"""Bounded synthetic replay workload measurements.

The workload is a mechanics benchmark, not historical data or evidence of
strategy merit.  It deliberately takes one sample without a warm-up.
"""

from __future__ import annotations

import math
import os
import platform
import time
from typing import Any

import qte
from qte.provenance import source_identity
from qte.strategies import MovingAverageConfig, MovingAverageRegimeStrategy


_MAX_BARS = 1_000_000
_TOP_LEVEL_KEYS = {
    "schema_version", "artifact_kind", "kind", "workload_version",
    "workload_label", "bars", "strategy", "load_seconds", "replay_seconds",
    "bars_per_second", "fills", "final_equity", "source_identity", "machine",
    "timing",
}
_MACHINE_KEYS = {"system", "machine", "python", "cpu_count"}
_TIMING = {
    "sample_count": 1,
    "warmup_count": 0,
    "clock": "time.perf_counter",
    "load_scope": "synthetic_generation_and_dataset",
    "replay_scope": "strategy_construction_and_engine_run",
}


class _Hold(qte.Strategy):
    def on_bar(self, ctx, bar):
        pass


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        qualifier = "positive and " if positive else "non-negative and "
        raise ValueError(f"{label} must be {qualifier}finite")
    return result


def validate_measurement(value: object) -> dict[str, Any]:
    """Validate and return an owned, JSON-safe benchmark measurement."""
    if not isinstance(value, dict) or set(value) != _TOP_LEVEL_KEYS:
        raise ValueError("benchmark measurement has unexpected fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    expected_literals = {
        "artifact_kind": "benchmark_measurement_v1",
        "kind": "synthetic_not_historical",
        "workload_version": "synthetic-scale-v1",
    }
    for key, expected in expected_literals.items():
        if value[key] != expected:
            raise ValueError(f"{key} must be {expected!r}")
    bars = value["bars"]
    if type(bars) is not int or not 1 <= bars <= _MAX_BARS:
        raise ValueError("bars must be an integer in [1,1000000]")
    strategy = value["strategy"]
    if strategy not in ("hold", "sma") or not isinstance(strategy, str):
        raise ValueError("strategy must be 'hold' or 'sma'")
    expected_label = "no_op" if strategy == "hold" else "sma"
    if value["workload_label"] != expected_label:
        raise ValueError("workload_label does not match strategy")
    load_seconds = _finite_number(value["load_seconds"], "load_seconds")
    replay_seconds = _finite_number(value["replay_seconds"], "replay_seconds", positive=True)
    throughput = _finite_number(value["bars_per_second"], "bars_per_second", positive=True)
    if not math.isclose(throughput, bars / replay_seconds, rel_tol=1e-12):
        raise ValueError("bars_per_second does not match bars and replay_seconds")
    fills = value["fills"]
    if type(fills) is not int or fills < 0:
        raise ValueError("fills must be a non-negative integer")
    final_equity = _finite_number(value["final_equity"], "final_equity")
    if strategy == "hold" and (fills != 0 or final_equity != 100000.0):
        raise ValueError("no-op workload must have no fills and unchanged equity")
    identity = value["source_identity"]
    if (type(identity) is not str or len(identity) != 71 or not identity.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in identity[7:])):
        raise ValueError("source_identity must be a sha256 identity")
    machine = value["machine"]
    if not isinstance(machine, dict) or set(machine) != _MACHINE_KEYS:
        raise ValueError("machine has unexpected fields")
    for key in ("system", "machine", "python"):
        if (type(machine[key]) is not str or not machine[key] or len(machine[key]) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in machine[key])):
            raise ValueError(f"machine.{key} must be a bounded non-empty string")
    cpu_count = machine["cpu_count"]
    if cpu_count is not None and (type(cpu_count) is not int or cpu_count <= 0):
        raise ValueError("machine.cpu_count must be null or a positive integer")
    timing = value["timing"]
    if (type(timing) is not dict or set(timing) != set(_TIMING)
            or any(type(timing[key]) is not type(expected) or timing[key] != expected
                   for key, expected in _TIMING.items())):
        raise ValueError("timing does not describe the supported single sample")
    return {
        "schema_version": 1,
        "artifact_kind": "benchmark_measurement_v1",
        "kind": "synthetic_not_historical",
        "workload_version": "synthetic-scale-v1",
        "workload_label": expected_label,
        "bars": bars,
        "strategy": strategy,
        "load_seconds": load_seconds,
        "replay_seconds": replay_seconds,
        "bars_per_second": throughput,
        "fills": fills,
        "final_equity": final_equity,
        "source_identity": identity,
        "machine": dict(machine),
        "timing": dict(timing),
    }


def run_benchmark(bars: int = 10000, strategy: str = "hold") -> dict[str, Any]:
    """Run one bounded synthetic generation and replay measurement."""
    if type(bars) is not int or not 1 <= bars <= _MAX_BARS:
        raise ValueError("bars must be an integer in [1,1000000]")
    if not isinstance(strategy, str) or strategy not in ("hold", "sma"):
        raise ValueError("strategy must be 'hold' or 'sma'")

    start = time.perf_counter()
    generated = []
    for index in range(bars):
        price = 100 + (index % 80 if index % 80 < 40 else 80 - index % 80) * 0.1
        generated.append(qte.Bar(
            "SPY", index * 60_000_000_000, (index + 1) * 60_000_000_000,
            price, price + 1, price - 1, price, 1000,
        ))
    data = qte.Dataset.from_bars(
        generated, interval_ns=60_000_000_000, source_id="synthetic-scale-v1")
    loaded = time.perf_counter()
    workload = (_Hold() if strategy == "hold" else
                MovingAverageRegimeStrategy("SPY", MovingAverageConfig(5, 20, 1)))
    result = qte.BacktestEngine(
        qte.BacktestConfig(100000, history_capacity=64)).run(data, workload)
    done = time.perf_counter()
    load_seconds = loaded - start
    replay_seconds = done - loaded
    measurement = {
        "schema_version": 1,
        "artifact_kind": "benchmark_measurement_v1",
        "kind": "synthetic_not_historical",
        "workload_version": "synthetic-scale-v1",
        "workload_label": "no_op" if strategy == "hold" else "sma",
        "bars": bars,
        "load_seconds": load_seconds,
        "replay_seconds": replay_seconds,
        "bars_per_second": bars / replay_seconds if replay_seconds > 0 else math.inf,
        "strategy": strategy,
        "fills": len(result.fills),
        "final_equity": result.equity_curve[-1].equity,
        "source_identity": source_identity(),
        "machine": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "timing": dict(_TIMING),
    }
    return validate_measurement(measurement)
