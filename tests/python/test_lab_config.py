"""Pure fixed-lab configuration construction before any artifact generation."""
from __future__ import annotations

import json

import pytest

import qte.strategy_lab as lab
from qte.strategies.candidates import FAMILIES


def spec(**changes):
    return {
        "schema_version": 1,
        "seed": 817,
        "bars_per_window": 128,
        "timeframes": ["hourly"],
        "regimes": ["low_vol_trend", "gap_shock"],
        **changes,
    }


def test_prepare_lab_is_pure_and_retains_the_complete_fixed_family_set(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "generate_case", lambda *_args, **_kwargs: pytest.fail("prepare generated data"))
    monkeypatch.setattr(lab, "run_experiment", lambda *_args, **_kwargs: pytest.fail("prepare ran an experiment"))
    value = spec()
    prepared = lab.prepare_lab(value)
    value["timeframes"].append("daily_24h")
    value["strategy_parameters"] = {"fast_period": 99}
    assert prepared.spec is not value
    assert prepared.spec["timeframes"] == ["hourly"]
    assert prepared.spec.get("strategy_parameters") is None
    assert prepared.timeframes == ("hourly",)
    assert prepared.regimes == ("low_vol_trend", "gap_shock")
    assert tuple(config.kind for config in prepared.configs) == FAMILIES
    assert prepared.experiment_config.required_regimes == ("low_vol_trend", "gap_shock")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("bad", [
    spec(schema_version=True),
    spec(timeframes=["hourly", "hourly"]),
    spec(strategy_parameters={"surprise": 1}),
    spec(bars_per_window=127),
])
def test_prepare_lab_rejects_the_same_invalid_config_before_output_creation(tmp_path, bad):
    with pytest.raises(ValueError):
        lab.prepare_lab(bad)
    config = tmp_path / "configuration.json"
    config.write_text(json.dumps(bad), encoding="utf-8")
    output = tmp_path / "output"
    with pytest.raises(ValueError):
        lab.run_lab(config, output)
    assert not output.exists()
