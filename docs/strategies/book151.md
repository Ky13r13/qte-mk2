# 151 Trading Strategies: SPX and futures research

Source: the user-supplied `151tradingstrategies.pdf`, Kakushadze and Serur,
August 17, 2018 SSRN edition. Section numbers below refer to that file, not the
different pagination of the published book. `book151-catalog.json` records its
SHA-256 and 173 table-of-contents strategy headings including subvariants.
Cataloged does not mean implemented. No publisher source code was copied.

The [publisher listing](https://link.springer.com/book/10.1007/978-3-030-02792-6)
currently calls the published book retracted. The reason was not established in
this review; do not infer a particular defect or extend that claim to every
formula in the supplied preprint. Treat these as hypotheses requiring independent
data and accounting validation, not a list of profitable systems.

## What runs now

The [historical ETF comparison](book151-historical-results.md) and the
[eight-timeframe study and chart guide](book151-schwab-timeframes.md) describe
the completed screens, their inputs and their limitations.

| Component | Implemented scope | Source |
|---|---|---|
| Single moving average | Registered long-only cash-equity/ETF strategy | 3.11 |
| Triple moving average | Registered long-only cash-equity/ETF strategy | 3.13 |
| Channel reversal | Registered long-only adaptation using prior closes | 3.15 |
| Internal bar strength reversal | Registered single-instrument adaptation | 4.4 |
| Time-series futures momentum | Causal inverse-volatility target weights | 10.4 |
| Cross-sectional futures contrarian | Causal relative-return target weights | 10.3 |
| Ten SPX option structures | Expiry payoff/risk and combined gamma scenarios | Chapter 2 |

Two-moving-average strategies already exist; the new work reuses QTE's existing
strategy lifecycle, fills, commissions, risk and accounting. Futures weights are
not contracts or executable orders. SPX cash-index observations are signals, not
tradeable shares. Derivative replay remains gated under ADR 0013; no futures-as-
shares backtest or duplicate derivative ledger is introduced.

## Deterministic price-signal specification

Inputs: canonical OHLCV bars for one cash-equity/ETF instrument, visible only when
available. Default lookbacks count bars, not calendar days. Daily research should
use daily bars; running the same settings hourly changes the hypothesis.

| Registered type | Entry when flat | Ordinary exit when long |
|---|---|---|
| `book_single_ma` | Current close > mean of the previous 21 closes, excluding current | Current close < that mean; equality holds |
| `book_triple_ma` | SMA(3) > SMA(10) > SMA(21), all ending at current close | SMA(3) <= SMA(10) |
| `book_channel_reversal` | Close <= minimum of previous 20 closes; prior channel must have positive width | Close >= maximum of previous 20 closes |
| `book_ibs_reversal` | IBS <= 0.20 | IBS >= 0.80 |

IBS = (close-low)/(high-low); a zero-range bar gives no IBS signal. The channel
source's exact equality becomes touching/crossing a boundary, an explicit
adaptation. The IBS source ranks an ETF universe into long/short deciles; our
single-name threshold variant is **not** that cross-sectional strategy. The
triple-MA source permits both directions; the registered wrapper is long-only.
These changes must remain in the manifest when comparing research results.

Sizing: floor(10% of current equity / signal close), positive whole shares.
Exit controls: close-detected stop at filled entry minus 2.5 times entry ATR(14),
or 20 held bars. ATR is the existing simple mean true range, not Wilder smoothing.
The existing risk manager decides acceptance; signals execute at the next
available observed bar open, with configured costs. Stops are not guaranteed
intrabar stop fills. A pending order blocks duplicates; rejection/cancellation
clears pending state through the existing order feedback. State resets per run.
Optional regime_filter uses the existing timestamped callback and can veto entries
or exit an existing position. No future/news regime is inferred automatically.

Configurable: all periods, IBS thresholds, allocation, ATR period/multiple and
holding limit in `BookConfig`. These materially affect results; defaults were
chosen as research specifications, not optimized to the demonstration.

## Futures policy specification

`futures_weights` consumes an explicit mapping of contract IDs to existing
`FuturesMark` observations. Filter available_ns <= decision time before selecting
history. Each instrument must have sufficient same-source observations; duplicate
vintages require explicit upstream selection. Histories must align across the
whole universe; missing symbols cannot silently disappear.

Defaults: 20-return momentum and volatility windows, population standard deviation
of simple returns, zero permitted mark age. On a regular clock enforce interval
spacing. For daily/session histories pass `expected_timestamps` from a validated
calendar so weekends/holidays are explicit rather than treated as missing bars.
No automatic expiry splice or continuous-futures adjustment is performed.

- Momentum: R_i = P_now/P_lookback - 1; raw weight = sign(R_i)/sigma_i.
  Normalize by sum of absolute raw weights. Equal-to-zero return gives zero;
  zero/near-zero volatility fails rather than creating infinite leverage.
- Contrarian: raw weight = -(R_i - cross-sectional mean(R)); normalize to unit
  gross exposure. Equal returns produce all-zero weights. At least two contracts
  are required. The result is dollar-neutral before rounding, not risk-neutral.
  The source suggests weekly rebalance; a caller must supply the rebalance
  schedule. This function does not rebalance at every tick on its own.

Actual ES/MES/NQ/MNQ contracts, contract multiplier, integer rounding, margin,
roll calendar, funding, settlement and pending-order reserves still require the
existing C++ derivative integration. The volume/open-interest filter in 10.3.1
is deferred until historical open-interest availability is established.

## SPX option structures and gamma

`SPXStructure` supports bull call, bear put, bull put and bear call verticals;
long straddle/strangle; long call/put butterflies; credit iron butterfly/condor.
Supply strictly ascending strikes and one exact UTC expiry. All legs share that
expiry and a 100 multiplier. AM versus PM cash settlement is explicit; the payoff
input is the actual settlement value, not necessarily the last displayed index
price. This module does not model American assignment or delivery into futures.

The source calls credit iron structures “long”; QTE uses **credit** in their
names to make cash-flow direction explicit. Unequal wing widths are supported
by exact piecewise payoff extrema rather than assuming symmetric risk.

Signed premium convention: positive net debit is paid; negative net debit is a
credit. Expiry PnL = 100*(sum signed leg intrinsic values - net debit) - total fees.
Evaluate zero and every strike, plus the right-tail slope, to compute maximum
loss/profit over nonnegative settlement values. An unbounded profit is `None`
with an explicit flag. This is payoff analysis, not broker margin or expected PnL.

`gamma_scenario` reuses the existing Black-76 calculator and sums signed
multiplier-weighted delta/gamma across legs, including a 25x41 gamma grid.
For SPX the supplied forward must be an index forward; do not silently substitute
spot. Common flat IV, calendar/365 and continuous rate are declared model
assumptions. A short-gamma structure is not evidence dealers are short gamma.
Actual trade selection needs contemporaneous strike-specific quotes/IV, spreads,
depth and fees; theoretical prices are not executable fills.

The book's 7.4 short-straddle volatility-risk-premium idea is cataloged but not
enabled: observed IV versus forecast future realized volatility is needed, and
unhedged loss is not capped. Its underlying rehedging changes delta; trading the
underlying does not eliminate option gamma. Existing gamma policies remain a
separate, gated research component. Buying puts/calls after expected-move
reentries still uses the existing reversal research, not a newly assumed edge.

## Run and inspect

```sh
cd /home/glsu6/Documents/qte
.venv/bin/python examples/book151_research.py --output build/book151-demo-new
```

The command refuses existing outputs. It creates four actual QTE C++ cash-equity
backtests on the existing seeded synthetic regime generator, exports fills/equity/
manifests (v2), ten SPX payoff charts with aggregate gamma grids, and two futures
weight examples. Open `report.html`. All prices/regimes are synthetic or modeled;
this is a mechanics demonstration, not in-sample or out-of-sample market evidence.
Individual registered strategies also work in the research command and research/
comparison form metadata. The Library contains this guide; futures policies and
option scenarios are Python interfaces, not live GUI trade tickets.

Next research: freeze rules and costs; evaluate observed SPY/QQQ as index proxies
with explicit adjustment limitations; collect executable SPX option quotes and
dated futures histories; then walk-forward evaluation with an untouched period,
regime attribution, transaction-cost stress and multiple-testing controls. Do not
choose a winner from the synthetic demonstration or use its returns to size live
orders. Broad source families needing fundamentals, borrowing, credit products,
multi-venue order books or other missing data remain cataloged as deferred.
