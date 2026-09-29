from dataclasses import replace
import math

import pytest

from qte.synthetic import DAY_NS, HOUR_NS, REGIMES, SyntheticConfig, generate_case


@pytest.mark.parametrize("regime", REGIMES)
@pytest.mark.parametrize("interval", [HOUR_NS, DAY_NS])
def test_seeded_regime_fixtures_are_valid_reproducible_and_explicitly_synthetic(regime, interval):
    config = SyntheticConfig(regime, seed=92, bars=180, interval_ns=interval)
    first, again = generate_case(config), generate_case(config)
    assert first.data.hash == again.data.hash
    assert first.source_sha256 == again.source_sha256
    assert first.data.source_id.startswith("synthetic:")
    assert first.data.bar_count == 180
    assert all(b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high for b in first.bars)
    assert all(math.isfinite(b.close) and b.low > 0 for b in first.bars)
    assert any(b.volume == 0 for b in first.bars)
    assert first.data.hash != generate_case(replace(config, seed=93)).data.hash
    if regime == "gap_shock":
        assert first.data.gap_count == 2
        assert first.data.end_ns == 182 * interval


def test_generation_is_prefix_causal_and_has_no_global_random_state():
    config = SyntheticConfig("regime_switch", seed=7, bars=140)
    small = generate_case(config)
    large = generate_case(replace(config, bars=240))
    assert [(b.start_ns, b.open, b.close, b.volume) for b in small.bars] == [
        (b.start_ns, b.open, b.close, b.volume) for b in large.bars[:140]]


@pytest.mark.parametrize("kwargs", [
    {"seed": True}, {"seed": -1}, {"bars": 1}, {"bars": False},
    {"interval_ns": 1}, {"start_ns": 2**63 - 1}, {"initial_price": float("nan")},
    {"initial_price": 0}, {"symbol": "bad name"}, {"regime": "actual_SPY"},
])
def test_invalid_synthetic_config_fails_explicitly(kwargs):
    with pytest.raises(ValueError):
        SyntheticConfig(**({"regime": "low_vol_trend", "seed": 1} | kwargs))
