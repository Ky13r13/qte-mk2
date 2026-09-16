from __future__ import annotations

import pytest

import qte
from qte.research import compare_moving_average
from qte.strategies import MovingAverageConfig, MovingAverageRegimeStrategy


HOUR_NS = 3_600_000_000_000


def make_dataset(prices, source_id):
    bars = []
    for index, (open_price, close_price) in enumerate(prices):
        bars.append(
            qte.Bar(
                "SPY",
                index * HOUR_NS,
                (index + 1) * HOUR_NS,
                open_price,
                max(open_price, close_price) + 1.0,
                min(open_price, close_price) - 1.0,
                close_price,
                1_000.0,
            )
        )
    return qte.Dataset.from_bars(
        bars, interval_ns=HOUR_NS, source_id=source_id
    )


IN_SAMPLE = (
    (100.0, 100.0),
    (101.0, 101.0),
    (103.0, 103.0),
    (104.0, 110.0),
    (108.0, 108.0),
    (104.0, 104.0),
    (105.0, 105.0),
)
OUT_OF_SAMPLE = (
    (200.0, 200.0),
    (201.0, 201.0),
    (203.0, 203.0),
    (204.0, 210.0),
    (208.0, 208.0),
    (204.0, 204.0),
    (203.0, 203.0),
)


def report():
    return compare_moving_average(
        in_sample=make_dataset(IN_SAMPLE, "synthetic-in-sample"),
        out_of_sample=make_dataset(OUT_OF_SAMPLE, "synthetic-out-of-sample"),
        symbol="SPY",
        strategy_config=MovingAverageConfig(2, 3, 10),
        initial_cash=100_000.0,
    )


def test_reference_report_is_reproducible_labeled_and_cost_sensitive() -> None:
    first = report()
    second = report()
    assert first == second
    assert first.parameter_status == "pre_registered_not_optimized"
    assert [item.sample for item in first.scenarios] == [
        "in_sample",
        "in_sample",
        "out_of_sample",
        "out_of_sample",
    ]
    assert [item.cost_scenario for item in first.scenarios] == [
        "baseline_zero_cost",
        "stressed_costs",
        "baseline_zero_cost",
        "stressed_costs",
    ]
    for scenario in first.scenarios:
        assert scenario.order_count == 2
        assert scenario.fill_count == 2
        assert scenario.trade_count == 1
    assert first.scenarios[0].total_return == pytest.approx(0.0001)
    assert first.scenarios[2].total_return == pytest.approx(-0.0001)
    assert first.scenarios[1].total_return < first.scenarios[0].total_return
    assert first.scenarios[3].total_return < first.scenarios[2].total_return


def test_strategy_parameters_are_validated() -> None:
    with pytest.raises(ValueError, match="greater"):
        MovingAverageConfig(3, 3, 10)
    with pytest.raises(ValueError, match="quantity"):
        MovingAverageConfig(2, 3, 0)


def test_strategy_uses_only_public_contract_and_avoids_duplicate_pending_orders() -> None:
    data = make_dataset(IN_SAMPLE, "pending-order-check")
    result = qte.BacktestEngine(qte.BacktestConfig(100_000.0)).run(
        data, MovingAverageRegimeStrategy("SPY", MovingAverageConfig(2, 3, 10))
    )
    assert len(result.orders) == 2
    assert len(result.fills) == 2
    assert result.positions[0].quantity == 0
