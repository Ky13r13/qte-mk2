"""American option sensitivities on recorded ETF signal paths, using model marks."""
import math

from .american_option import american_option_price

MODEL_SCENARIOS = {
    'base_frozen_vol': (.05, .65, 0.0),
    'wide_frozen_vol': (.15, 1.30, 0.0),
    'wide_vol_crush': (.15, 1.30, -.02),
}
MAX_DEBIT = 650


def value_trades(trades):
    results = {}
    for scenario, (half_spread, fee, vol_change) in MODEL_SCENARIOS.items():
        closes = []
        capped = 0
        for row in trades:
            direction = row['sign']
            right = 'call' if direction > 0 else 'put'
            spot = row['entry_open']
            # Hypothetical $1 strike lattice, 0.5% OTM, fixed once at entry.
            strike = math.ceil(1.005*spot) if direction > 0 else math.floor(.995*spot)
            iv = row['vol_proxy']
            holding_days = (row['exit_ns']-row['entry_ns'])/86_400e9
            entry_mid = american_option_price(spot, strike, iv, 7/365, .04, .01, right, steps=64)
            cost = math.ceil(100*(entry_mid+half_spread)) + 2*fee
            if cost > MAX_DEBIT:
                capped += 1
                continue
            exit_mid = american_option_price(row['exit_open'], strike, max(.01, iv+vol_change),
                                             (7-holding_days)/365, .04, .01, right, steps=64)
            proceeds = math.floor(100*max(0.0, exit_mid-half_spread))
            closes.append(dict(day=row['day'], sign=direction, strike=strike, entry_ns=row['entry_ns'],
                               exit_ns=row['exit_ns'], model_pnl=round(proceeds-cost, 2), model_debit=cost))
        results[scenario] = dict(model_pnl=round(sum(r['model_pnl'] for r in closes), 2), trades=len(closes),
                                 rejected_by_debit_cap=capped, ledger=closes)
    return results
