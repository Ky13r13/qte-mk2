import math

import pytest

from qte import benchmark


def test_hold_measurement_has_legacy_fields_and_explicit_single_sample(monkeypatch):
    ticks = iter((10.0, 10.25, 10.75))
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: next(ticks))
    value = benchmark.run_benchmark(3, "hold")
    assert value["kind"] == "synthetic_not_historical"
    assert value["bars"] == 3
    assert value["strategy"] == "hold"
    assert value["load_seconds"] == 0.25
    assert value["replay_seconds"] == 0.5
    assert value["bars_per_second"] == 6.0
    assert value["fills"] == 0
    assert value["final_equity"] == 100000.0
    assert value["workload_label"] == "no_op"
    assert value["timing"] == {
        "sample_count": 1, "warmup_count": 0, "clock": "time.perf_counter",
        "load_scope": "synthetic_generation_and_dataset",
        "replay_scope": "strategy_construction_and_engine_run",
    }
    assert set(value["machine"]) == {"system", "machine", "python", "cpu_count"}


@pytest.mark.parametrize("strategy,label", [("hold", "no_op"), ("sma", "sma")])
def test_actual_small_strategy_paths(strategy, label):
    value = benchmark.run_benchmark(24, strategy)
    assert value["workload_label"] == label
    assert value["bars"] == 24
    assert math.isfinite(value["final_equity"])


@pytest.mark.parametrize("bars", [True, False, 1.0, "2", 0, -1, 1_000_001])
def test_invalid_bar_count_is_rejected(bars):
    with pytest.raises(ValueError, match="bars"):
        benchmark.run_benchmark(bars)


@pytest.mark.parametrize("strategy", [None, True, "Hold", "buy_and_hold"])
def test_invalid_strategy_is_rejected(strategy):
    with pytest.raises(ValueError, match="strategy"):
        benchmark.run_benchmark(1, strategy)


@pytest.mark.parametrize("ticks", [
    (2.0, 1.0, 3.0),
    (1.0, 2.0, 2.0),
    (1.0, float("inf"), float("inf")),
    (1.0, 2.0, float("nan")),
])
def test_invalid_clock_measurements_are_rejected(monkeypatch, ticks):
    clock = iter(ticks)
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: next(clock))
    with pytest.raises(ValueError):
        benchmark.run_benchmark(1)


def test_validator_returns_owned_nested_values():
    ticks = iter((1.0, 1.0, 2.0))
    original_clock = benchmark.time.perf_counter
    benchmark.time.perf_counter = lambda: next(ticks)
    try:
        value = benchmark.run_benchmark(1)
    finally:
        benchmark.time.perf_counter = original_clock
    checked = benchmark.validate_measurement(value)
    assert checked is not value
    assert checked["machine"] is not value["machine"]
    assert checked["timing"] is not value["timing"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(source_identity="sha256:" + "g" * 64),
    lambda value: value["timing"].update(sample_count=True),
    lambda value: value["machine"].update(system="Linux\nforged"),
    lambda value: value.update(load_seconds=10**10000),
])
def test_validator_rejects_noncanonical_identity_metadata_and_numbers(monkeypatch, change):
    ticks = iter((1.0, 2.0, 3.0))
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: next(ticks))
    value = benchmark.run_benchmark(1)
    change(value)
    with pytest.raises(ValueError):
        benchmark.validate_measurement(value)
