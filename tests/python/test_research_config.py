"""G5b.1 shared single-run preparation and CLI parity regressions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import qte
import qte.cli
from qte.cli import run_research
from qte.research_config import capture_research_inputs, fields, prepare_research
from qte.strategies import CandidateConfig, MovingAverageConfig
from qte.strategies.candidates import FAMILIES


ROOT = Path(__file__).resolve().parents[2]
DATA = (ROOT / "examples/research-bars.csv").read_bytes()
START_NS = 1_704_204_000_000_000_000
END_NS = 1_704_229_200_000_000_000
HOUR_NS = 3_600_000_000_000


def sma_config() -> dict:
    return json.loads((ROOT / "examples/research-run.json").read_text())


def candidate_config(kind: str = "trend") -> dict:
    value = json.loads((ROOT / "examples/trend-research-run.json").read_text())
    value["strategy"]["type"] = kind
    return value


def write_run(tmp_path: Path, config: dict) -> Path:
    source = tmp_path / "bars.csv"
    source.write_bytes(DATA)
    value = json.loads(json.dumps(config))
    value["data"]["path"] = source.name
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_fields_remains_the_shared_compatible_symbol():
    assert qte.cli.fields is fields
    assert fields({"required": 1}, {"required"}, {"required"}) == {"required": 1}


@pytest.mark.parametrize("strategy_type", ("moving_average_regime", *FAMILIES))
def test_prepare_constructs_every_registered_strategy_without_running_engine(monkeypatch, strategy_type):
    class ForbiddenEngine:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("prepare_research must not construct or run an engine")

    monkeypatch.setattr(qte, "BacktestEngine", ForbiddenEngine)
    config = sma_config() if strategy_type == "moving_average_regime" else candidate_config(strategy_type)
    prepared = prepare_research(config, DATA, build_identity="g5b1-test")
    assert prepared.strategy_type == strategy_type
    assert prepared.symbol == "SPY"
    assert isinstance(prepared.engine_config, qte.BacktestConfig)
    if strategy_type == "moving_average_regime":
        assert isinstance(prepared.strategy_config, MovingAverageConfig)
        assert prepared.sampling is not None
        assert prepared.sampling_manifest == {
            "interval_ns": HOUR_NS,
            "max_staleness_ns": 0,
            "periods_per_year": 8766,
            "timestamps_ns": list(range(START_NS, END_NS + 1, HOUR_NS)),
            "policy": "last_event_at_or_before_v1",
        }
    else:
        assert isinstance(prepared.strategy_config, CandidateConfig)
        assert prepared.strategy_config.kind == strategy_type


def test_prepare_owns_configuration_and_never_reopens_captured_data(tmp_path, monkeypatch):
    path = write_run(tmp_path, sma_config())
    original_read_text, original_read_bytes = Path.read_text, Path.read_bytes
    config_reads: list[Path] = []
    data_reads: list[Path] = []

    def read_text_once(self, *args, **kwargs):
        config_reads.append(self)
        return original_read_text(self, *args, **kwargs)

    def read_bytes_once(self, *args, **kwargs):
        data_reads.append(self)
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_once)
    monkeypatch.setattr(Path, "read_bytes", read_bytes_once)
    config, raw = capture_research_inputs(path)
    assert config_reads == [path]
    assert data_reads == [tmp_path / "bars.csv"]

    prepared = prepare_research(config, raw, build_identity="captured-test")
    config["engine"]["initial_cash"] = 1
    (tmp_path / "bars.csv").write_text("changed after capture")
    assert prepared.configuration["engine"]["initial_cash"] == 100000
    assert prepared.loaded.provenance.source_sha256 == hashlib.sha256(DATA).hexdigest()
    assert config_reads == [path]
    assert data_reads == [tmp_path / "bars.csv"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"adapter": "unknown"}, "adapter must be alpaca or csv"),
        ({"action_free": False}, "requires action_free=True"),
        ({"interval_ns": 0}, "interval_ns must be a positive integer"),
        ({"schema": {"symbol": "timestamp"}}, "schema mappings must be unique"),
        ({"adapter": "alpaca", "action_free": True}, "Alpaca assumptions must be in fixture envelope"),
    ],
)
def test_invalid_adapter_options_fail_before_missing_source_is_read(tmp_path, change, message):
    config = sma_config()
    config["data"].update(change)
    config["data"]["path"] = "missing-input"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=message):
        capture_research_inputs(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"strategy": {"fast_period": 0}}, "fast_period must be positive"),
        ({"engine": {"history_capacity": 1}}, "history capacity is smaller"),
        ({"sampling": {"interval_ns": HOUR_NS + 1}}, "must divide run duration"),
        ({"sampling": {"periods_per_year": 10**400}}, "invalid annualization configuration"),
    ],
)
def test_shared_prepare_and_cli_reject_the_same_invalid_configuration(tmp_path, change, message):
    config = sma_config()
    section, values = next(iter(change.items()))
    config[section].update(values)
    with pytest.raises(ValueError, match=message):
        prepare_research(config, DATA, build_identity="invalid-test")
    path = write_run(tmp_path, config)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match=message):
        run_research(path, output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("sampling", "message"),
    [
        ({"timestamps_ns": [START_NS]}, "at least two"),
        ({"timestamps_ns": [START_NS, START_NS, END_NS]}, "strictly increase"),
        ({"timestamps_ns": [START_NS + 1, END_NS]}, "retain dataset start and end"),
        ({"timestamps_ns": [START_NS, END_NS + 1]}, "retain dataset start and end"),
        ({"timestamps_ns": [START_NS, END_NS], "max_staleness_ns": -1}, "nonnegative int64"),
        ({"timestamps_ns": [START_NS, END_NS], "periods_per_year": 0}, "invalid annualization"),
        ({"timestamps_ns": [START_NS, END_NS], "annual_risk_free_rate": -1}, "invalid annualization"),
        ({"timestamps_ns": [START_NS, END_NS], "annual_risk_free_rate": 10**400}, "invalid annualization"),
    ],
)
def test_config_only_sampling_errors_are_rejected_before_engine_execution(tmp_path, monkeypatch, sampling, message):
    config = sma_config()
    config["sampling"] = {
        "max_staleness_ns": 0,
        "periods_per_year": 8766,
        **sampling,
    }
    path = write_run(tmp_path, config)
    calls = 0

    class TrackingEngine:
        def __init__(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1

        def run(self, *_args, **_kwargs):
            raise AssertionError("invalid sampling must fail before engine execution")

    monkeypatch.setattr(qte, "BacktestEngine", TrackingEngine)
    with pytest.raises(ValueError, match=message):
        run_research(path, tmp_path / "output")
    assert calls == 0


def test_result_dependent_staleness_remains_with_cpp_analytics(tmp_path, monkeypatch):
    config = sma_config()
    config["sampling"] = {
        "timestamps_ns": [START_NS, START_NS + HOUR_NS // 2, END_NS],
        "max_staleness_ns": 0,
        "periods_per_year": 8766,
    }
    prepared = prepare_research(config, DATA, build_identity="runtime-check")
    assert prepared.sampling is not None

    real_engine = qte.BacktestEngine
    calls = 0

    class TrackingEngine:
        def __init__(self, engine_config):
            self._engine = real_engine(engine_config)

        def run(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return self._engine.run(*args, **kwargs)

    monkeypatch.setattr(qte, "BacktestEngine", TrackingEngine)
    with pytest.raises(ValueError, match="staleness"):
        run_research(write_run(tmp_path, config), tmp_path / "output")
    assert calls == 1
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    ("config_name", "export_version", "expected"),
    [
        (
            "research-run.json",
            1,
            {
                "report.json": "d5a9d53cbd48f264102c4abed1a5085ab380ae321f9e86518def45dad1adef29",
                "equity.csv": "386b4c7e256ae858f712450ead9db816a72dc45203ee2b0206ebb7f05acb9116",
                "fills.csv": "d4781fd08661399a2446efb7929dc7f3135b47c110afe56d23c79a0086711fef",
                "orders.csv": "8c239c0eeee690b873c8530bcf3d37d8d197a70c90bf7668376b3f940efb0b2b",
                "sampled_equity.csv": "28f0e749a8bdfec98627b1637a5a903149fabbda8d9d1a0cdba14d6506991f0d",
            },
        ),
        (
            "research-run.json",
            2,
            {
                "closed_trades.csv": "7516003f998fd2482222967f79cb4fd623a44a9ed9112f19cb0a1f13afa59ac7",
                "equity_events.csv": "3d5475e986dd8482665b5f8d66d87c56dba3f2b76e9ab2c99324a18d14fc7bab",
                "fill_events.csv": "6b18677ae48a2b2becdc9f956bca083ec71fd4498296c224bfeef9cfeae5b647",
                "open_trades.csv": "48878fe142e4147150aefd788ca1f8d9e7a9e5c6206e36cce05855c2cd80921b",
                "order_events.csv": "edda406db343983ba7e2cc81dc63486cb2c1ee08f59b98486ada769dc6e5f928",
                "order_snapshots.csv": "a54c86edc0c4cb14f4acbb57ab96f5166b711b9f5923157b7405b932734d7ca3",
                "positions.csv": "be921b80b010c3ce8283b2407900130f5233f6b549afb29a03845d7465e0b0b3",
                "sampled_equity_events.csv": "19ffd970e6f5026edc7fff0a6a8b0d01f64547f0b1dac978d0bba889f8fbbfdd",
            },
        ),
        (
            "trend-research-run.json",
            2,
            {
                "report.json": "6e5dbc3a17f1ac360fb5701ba684368fd90cb6e18ef60301ab13c98ccb1d4fc6",
                "equity.csv": "bfe1b66a3fae67ab6970f8919478340adddaac0e9397ce13e6863f1f87c3f74d",
                "fills.csv": "b549ac417e842c6dfd674c5ee106dcd9413b8532084c7b2e2fec2318962c3572",
                "closed_trades.csv": "c15d775adf02d1d30db13293c3dd115cb67b543b49df2d8b3274e525e9bba1de",
                "fill_events.csv": "9c48c7e166ac8048fde594261390483f5d20c2b0dd9385b248feb37f0456f83a",
                "order_snapshots.csv": "ebcde63ba32c4e37061c1bc526b1467e15cffca41766bd2054fac05af5b63c33",
            },
        ),
    ],
)
def test_pre_extraction_financial_export_goldens(tmp_path, config_name, export_version, expected):
    output = run_research(ROOT / "examples" / config_name, tmp_path / "output", export_version=export_version)
    assert {name: sha256(output / name) for name in expected} == expected
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["normalized_engine_config"].endswith(f"build_identity={manifest['source_identity']}")
    if export_version == 2:
        wrapper = json.loads((output / "research_export.json").read_text())
        assert wrapper["lineage"]["source_identity"] == manifest["source_identity"]
