"""Offline synthetic policy smoke check. No orders, fills, backtest or market data."""
from dataclasses import asdict
import json

from qte.regimes import DealerObservation
from qte.strategies.nasdaq_gamma import (
    DealerGammaConfig, DealerGammaPolicy, FuturesMark, GammaHedgePolicy,
    HedgeInstrument, OptionPosition, VisibleOptionGreek,
)


def demonstrate() -> dict:
    # Tiny artificial UTC-nanosecond times and prices are correctness fixtures.
    # These identities describe synthetic contracts, not historical observations.
    greek_inputs = [
        VisibleOptionGreek(contract, "NQ-test", delta, .01, 0, 1,
                           "synthetic-fixture", "v1", "hand-specified")
        for contract, delta in (("call-test", .5), ("put-test", -.2))
    ]
    gamma = GammaHedgePolicy().decide(
        10, [OptionPosition("call-test", 1, 20), OptionPosition("put-test", 1, 20)],
        greek_inputs, HedgeInstrument("MNQ-test", "NQ-test", 2, 10),
        current_contracts=0,
    )
    dealer = DealerGammaPolicy(
        [DealerObservation(-1, 0, 1, "synthetic-fixture", "v1", "hand-specified",
                           "NQ-test", "model_estimate")],
        DealerGammaConfig(underlying="NQ-test", rolling_window=2,
                          allow_model_estimates=True),
    ).decide(
        10, [FuturesMark("NQ-test", i, i, price, "synthetic-fixture", f"v{i}")
             for i, price in enumerate((100, 101, 120))], current_contracts=0,
    )
    return {
        "kind": "synthetic_policy_smoke_not_a_backtest",
        "gamma_hedge": asdict(gamma),
        "dealer_gamma_futures": asdict(dealer),
    }


if __name__ == "__main__":
    print(json.dumps(demonstrate(), indent=2, sort_keys=True, allow_nan=False))
