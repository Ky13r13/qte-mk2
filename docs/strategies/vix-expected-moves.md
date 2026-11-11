# VIX futures expected-move scenarios

Status: implemented **offline analysis**, not a calibrated Nasdaq forecast,
option-pricing model, executable strategy or gamma-scalping backtest. Available
through `qte.volatility` and `examples/vix_expected_moves.py`; this document is
in the GUI library. There is no new interactive analysis form or broker path.
Research/source verification: November 4–5, 2026.

## Interpretation and inputs

VX futures reflect expectations of the VIX index at future expirations; VIX
itself concerns SPX option-implied volatility, not NQ gamma. A selected VX price
does **not** identify Nasdaq volatility, dealer positioning or an option book's
convexity. [Cboe VX overview](https://www.cboe.com/tradable-products/vix/vix-futures),
[VIX methodology](https://cdn.cboe.com/resources/vix/VIX_Methodology.pdf).

This implementation treats one explicitly selected contract as an annualized
volatility **scenario**, multiplied by a required target-market scale. The
example's scale of 1.0 is illustrative, not an estimated NQ/SPX relationship.
No automatic front-month roll, curve interpolation, or multiplication of VX² by
time-to-expiry to claim a recovered variance curve. Curve shape is diagnostic;
asynchronous reference dates are disclosed.

Required inputs:

- `VolatilityFutureObservation`: expiry-qualified contract ID, volatility points,
  reference/availability timestamps, expiry cutoff, source and vintage identity.
- `ProxyAssumption`: target symbol, positive scale, assumption identity and
  reference/availability timestamps. Historical calibration must be fitted and
  frozen using only information available before the forecast.
- `CandleSnapshot`: one completed, available target candle for the anchor close;
  optionally completed OHLC candles at each named timeframe for reference levels.
  `from_observation()` copies the existing canonical `BarObservation` contract.
- Optional `GammaSensitivity`: independently supplied signed **USD per point²**,
  same target symbol, source and reference/availability timestamps. For a
  qualified option book this is `sum(quantity * multiplier * option_gamma)`;
  per-option gamma must differentiate delta against this same futures price.
  Do not substitute dealer-gamma USD-per-1%-move fields or infer gamma from VX.

All times are signed UTC nanoseconds. Default staleness limits: VX reference
three calendar days; anchor end and gamma reference five minutes. Availability
must be at or before the decision. Latest visible reference is primary, latest
visible revision secondary; conflicting duplicates fail closed. Selected
contracts stop being usable at their exclusive expiry cutoff; missing/stale
contracts fail rather than roll. Old reference candles remain usable as levels,
with their age reported; they are never treated as fresh anchor prices.

## Exact calculations

“All candle levels” currently means **both** configurable forecast durations and
open/high/low/close levels of supplied completed candles. It does not mean
predicting every future candle or using the unfinished candle's eventual range.

For anchor close `S`, selected VX quote `V` in volatility points, explicit scale
`a`, elapsed horizon `h` and a 365-calendar-day year `Y`:

```text
annualized scenario volatility = (V / 100) * a
sigma_fraction = annualized scenario volatility * sqrt(h / Y)
sigma_points = S * sigma_fraction
k-sigma band = [S - k*sigma_points, S + k*sigma_points], k = 1, 2, 3
normal expected absolute move = sigma_points * sqrt(2/pi)
```

The return model is arithmetic-normal, zero drift and constant volatility.
Normal endpoint mass inside ±1σ/2σ/3σ is approximately 68.27%/95.45%/99.73%.
These are mathematical model masses, **not empirically calibrated likelihoods**.
“One-sigma move” and expected absolute move are reported separately.
Calendar365 matches the declared convention, not the engine performance-metric
year clock. [Cboe calendar-day explanation](https://www.cboe.com/insights/posts/inside-volatility-trading-breaking-down-the-vix-index-and-its-correlation-to-the-s-p-500-index/).

Defaults: 1m, 5m, 15m, 30m, 1h, 4h, and **24 elapsed hours** (`1d_calendar`).
Pass `MoveHorizon(label, duration_ns)` for another duration. A 390-minute session
is not a full calendar day, and this analyzer neither invents session calendars
nor reallocates overnight volatility into trading minutes. Short-horizon
square-root scaling ignores volatility seasonality and jumps.

For each already-known candle level `L`, report `z=(L-S)/sigma_points` and normal
probabilities of the **future endpoint** finishing above/below `L`. These are not
probabilities of touching/crossing the level during the interval, nor estimates
of the future candle high/low. Probabilities require validation against actual
forward outcomes before use as forecasts.

If external dollar gamma `G` is supplied, the local gross convexity scenario is
`0.5 * G * move_points²`; under the declared model its expected value is
`0.5 * G * sigma_points²`. Gamma is held constant locally. Theta, vega/repricing,
delta PnL, fees, spread, hedge path, futures basis and financing are excluded.
**This is not option price, hedge profit, net PnL or a trading edge.** Overflow,
unrepresentable nonzero gamma products and nonpositive three-sigma bands fail
explicitly instead of clipping values or returning NaN/infinity.

## Cboe saved-data adapter and provenance

`qte.adapters.cboe_vix.parse_cboe_vx_settlements()` consumes saved bytes only;
no network side effects. Supported source:
[dated Cboe settlement CSV](https://www-api.cboe.com/us/futures/market_statistics/settlement/csv/?dt=2026-11-03).
The exact header is `Product,Symbol,Expiration Date,Price`. Monthly and weekly
VX rows are accepted; other products are excluded. IDs include symbol **and ISO
expiry**, because short exchange symbols can repeat in later decades.

The adapter commits exact raw-byte SHA-256, dated URL, retrieval time and
assumptions to provenance; observation vintage commits all of these. A trailing
asterisk means discretionary settlement; absence is **unknown**, not false:
the public HTML can mark discretionary prices when the CSV does not.
[Cboe settlement table](https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily).

Reference time is next UTC midnight after the settlement date (a conservative
date convention, not the exchange's exact settlement instant). **Availability
is retrieval time only**. A newly downloaded old settlement cannot be used in
an earlier historical decision. Expiry cutoff is UTC midnight at the start of
the stated expiry date, conservatively excluding that entire date; this is not
an execution/settlement rule for VX trading. A separate, verified publication
archive and exact contract calendar are needed before historical signal use.

## Run locally

From the repository root, using the existing virtual environment:

```sh
.venv/bin/python examples/vix_expected_moves.py
.venv/bin/python -m pytest tests/python/test_volatility.py tests/python/test_cboe_vix.py tests/python/test_vix_example.py -q
```

The default JSON contains synthetic VX, synthetic NQ-demo hourly/daily candles,
scale 1.0 and illustrative gamma 0.02 USD/point². No order, fill, network request
or historical performance claim is made. On Windows use
`.venv\Scripts\python.exe` after building the native extension.

Saved-data mode requires **all four** arguments together, with your actual
recorded retrieval timestamp (not a historical time invented for the backtest):

```text
--vx-csv PATH --settlement-date YYYY-MM-DD --retrieved-at-ns INTEGER
--contract-id SYMBOL@YYYY-MM-DD
```

Optional `--volatility-scale` and `--gamma-dollars-per-point-squared` change the
illustrative assumptions. Saved mode still uses **synthetic target candles and
gamma**, labeled `saved_cboe_vx_mixed_with_synthetic_target`. It demonstrates the
adapter, not a real NQ forecast. Full output includes the selected observation,
visible curve, proxy, anchor, candle sources, gamma, model ID and warnings.

For real analysis, call `analyze_vix_proxy()` directly with qualified target
observations and your own declared assumptions. Retain the adapter provenance
alongside `dataclasses.asdict(result)`; use JSON `allow_nan=False`.

## Validation and remaining gates

Tests cover hand-calculated bands/normal tails, all OHLC levels, time scaling,
gamma units/sign and extreme numeric products, causal revisions, staleness,
expiry/no-roll, strict CSV domains, contract identity and reproducibility.
A public November 3 Cboe file was parsed successfully during the November 5
smoke check; the associated target data remained synthetic. No live intraday
feed, historical NQ option chain or dealer-positioning data was acquired.

Next: acquire licensed causal target/option data, select a matched horizon and
point-in-time Nasdaq volatility calibration, then evaluate endpoint coverage
and tails on chronological untouched holdouts. Retain all scale/horizon trials;
report regime sensitivity and calibration failures, not just attractive bands.
Gamma trading still depends on the settlement, margin, quote-execution and
lifecycle gates in the [Nasdaq strategy specification](nasdaq-gamma-scalping.md)
and [roadmap](../roadmap.md). This analyzer does not bypass those gates.
