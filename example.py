import qte
from qte.adapters import load_alpaca_fixture
from qte.strategies import (
    MovingAverageConfig,
    MovingAverageRegimeStrategy,
)

HOUR_NS = 3_600_000_000_000

loaded = load_alpaca_fixture(
    "tests/fixtures/alpaca/stock_bars_complete.json",
    interval_ns=HOUR_NS,
)

engine = qte.BacktestEngine(
    qte.BacktestConfig(
        initial_cash=100_000,
        execution_costs=qte.ExecutionCosts(
            commission_bps=1,
            spread_bps=2,
            slippage_bps=2,
        ),
    )
)

strategy = MovingAverageRegimeStrategy(
    "SPY",
    MovingAverageConfig(
        fast_period=2,
        slow_period=3,
        quantity=10,
    ),
)

results = engine.run(loaded.dataset, strategy)
metrics = qte.analyze(results)

print("Dataset:", results.manifest.dataset_hash)
print("Orders:", len(results.orders))
print("Fills:", len(results.fills))
print("Final equity:", results.equity_curve[-1].equity)
print("Total return:", metrics.total_return.value)
print("Maximum drawdown:", metrics.maximum_drawdown.value)