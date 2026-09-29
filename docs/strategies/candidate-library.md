# Causal candidate strategy library

Status: executable, deterministic research hypotheses with synthetic behavioral
tests. No candidate is a demonstrated profitable strategy. These six families
were specified before real-data comparison; defaults are not optimized. Record
all candidates and failed trials. Selection using validation data consumes that
data: a later untouched chronological holdout is still required.

## Shared contract

`CandidateConfig` and `CandidateStrategy` live in
`qte.strategies.candidates`. `FAMILIES` is the ordered registry:
`trend`, `breakout`, `contraction`, `pullback`, `mean_reversion`, `rebound`.
Configuration is frozen and supports `dataclasses.asdict` and reconstruction.

```python
from qte import BacktestConfig, BacktestEngine
from qte.strategies.candidates import CandidateConfig, CandidateStrategy

config = CandidateConfig(kind="contraction", allocation_fraction=0.10)
engine = BacktestEngine(BacktestConfig(
    100_000, history_capacity=config.history_required))
result = engine.run(data, CandidateStrategy("SPY", config))
```

Inputs are canonical positive-price OHLCV bars for one whole-share instrument.
Volume is not a signal in these candidates; no order-book, options, or corporate
action information is inferred from it. Bar frequency is chosen in the dataset;
lookbacks count observed completed bars, not elapsed time. Gaps are neither
interpolated nor treated as extra observations. Market closures can therefore
extend elapsed holding time. Use the documented core USD/raw/action-free data
contract; do not silently run raw ETF data across dividend or split events.

All signal calculations occur at the current bar's close. Orders are market
orders, executed by QTE at the next eligible open, with configured spread,
slippage, commission, and risk checks. A signal may be rejected, canceled, or
never filled. Final open positions remain marked, not forcibly liquidated.
History capacity must be at least `config.history_required`; insufficient
capacity raises explicitly once enough bars should have been available.

Each candidate is long-only, flat or one long position, with no pyramiding.
Entry size is `floor(current_equity * allocation_fraction / signal_close)`
whole shares. Zero shares means no order. This equalizes **target notional
allocation**, not volatility or maximum loss across families. Opening gaps and
costs can change actual allocation; the independent core risk layer can reject
or cancel execution. The strategy cannot mutate accounting.

Only one strategy order is pending at once. Rejection/cancellation clears that
state; a subsequent bar may retry if its conditions still hold. Fill feedback
records actual entry price. All strategy state resets in `on_start`; for full
reproducibility an optional external regime filter must also be deterministic
and avoid retaining run-specific state.
The implementation targets the current all-or-none backtest fill contract.
Live/paper partial-fill aggregation and reconciling an existing account position
are not implemented here; do not connect these classes to an account directly.

### Common exits and state

Exit the full position if an ordinary family exit fires, the regime gate denies
the family, a close-based stop fires, or the maximum holding period is reached.
Each exit is a market order for the next eligible open, **not an intrabar stop**.

- Freeze the signal-bar ATR when entry is submitted, and attach it only after an
  actual buy fill. Stop when `close <= actual_entry_price - stop_atr_multiple *
  signal_ATR`. A gap can create a larger loss; this is not a guaranteed loss cap.
- Count one holding bar at the close of the first bar whose open filled entry.
  Exit when the count reaches `max_holding_bars`. Count does not start at signal
  creation and is not elapsed-clock-time-based.
- State: pending/last order IDs, signal ATR pending execution, actual fill price,
  entry ATR, completed holding-bar count, and own-symbol warmup count.

`regime_filter(timestamp_ns, family) -> bool` optionally gates the strategy.
The timestamp is the close's UTC nanosecond availability time. `False` prevents
entries and requests exit from an existing long. The callable must supply only
information actually available by that timestamp; loading a future macro value
into a closure is still look-ahead bias. Default `None` means an **ungated**
comparison, not an inferred macro classification. The family-specific policies
belong in a separate regime layer, not in accounting or execution.
The gate is polled only at this instrument's observed bar closes. Missing bars
or a closed market delay both ordinary exits and a macro risk-off response;
these are not tick-driven or live emergency trading controls.

## Formulas and rules

Notation: `C_t`, `H_t`, `L_t` are current completed close/high/low; `SMA(n,t)`
includes `C_t`. True range is
`TR_t = max(H_t-L_t, abs(H_t-C_(t-1)), abs(L_t-C_(t-1)))`.
`ATR(n,t)` is the arithmetic mean of the last `n` true ranges, **not Wilder
smoothing**; one preceding close is required. Comparisons are strict unless
shown with `<=` or `>=`. No candidate requires a crossover unless specified.

| Family | Entry while flat | Ordinary exit while long |
| --- | --- | --- |
| `trend` | `SMA(fast,t) > SMA(slow,t)` | `SMA(fast,t) < SMA(slow,t)`; equality holds |
| `breakout` | `C_t > max(H_(t-breakout_period)..H_(t-1))` | `C_t < min(L_(t-exit_period)..L_(t-1))` |
| `contraction` | Same breakout **and** a qualifying contraction in the previous `contraction_memory` bars | Same prior-low breakout exit |
| `pullback` | `SMA(fast,t) > SMA(slow,t)` **and** `C_(t-1) < SMA(fast,t-1)` **and** `C_t > SMA(fast,t)` | `SMA(fast,t) < SMA(slow,t)` **or** `C_t < SMA(slow,t)` |
| `mean_reversion` | `C_t <= prior_mean - entry_z * prior_std` **and** `prior_std > 0` **and** `ATR/C_t <= max_atr_fraction` | `C_t >= prior_mean` |
| `rebound` | `C_(t-1)/C_(t-2)-1 <= -shock_fraction` **and** `C_t > C_(t-1)` **and** `ATR/C_t >= min_atr_fraction` | `C_t >= SMA(fast,t)` |

Contraction at prior bar `s` means its positive `ATR(atr_period,s)` is `<=`
the nearest-rank `contraction_quantile` of the preceding
`contraction_window` ATR values, excluding bar `s`. Sort those past values and
take one-based rank `ceil(q * window)`. Check `s=t-memory..t-1`, never the
current breakout bar. Equal positive volatility can qualify; zero ATR cannot.
This measures low **absolute** ATR within one recent history, not cross-asset
comparability or an option-implied volatility forecast.

For mean reversion, `prior_mean` and population `prior_std` use exactly the
previous `slow_period` closes, excluding current close. Zero variance disables
entry. The name describes the hypothesis; its low ATR ceiling alone does not
prove the market is range-bound. An external macro/regime gate may further
restrict it.

Rebound means an observed negative close-to-close shock followed by a higher
close in sufficiently high realized range. It does **not** identify dealers,
gamma exposure, absorption, short covering, or buy/sell order flow. Its rapid
mean exit can coincide with the entry signal; entry is evaluated only when flat
and an ordinary exit only while long, so the first possible exit signal follows
the actual fill. Short-gamma trend-following versus long-gamma stabilization is
a separate, data-dependent hypothesis, not established by these bars.

## Parameters

| Parameter | Default | Used by |
| --- | --- | --- |
| `allocation_fraction` | 0.10 | All entries |
| `atr_period` | 14 | All close stops; volatility filters |
| `stop_atr_multiple` | 2.5 | All close stops |
| `max_holding_bars` | 20 | All time exits |
| `fast_period`, `slow_period` | 10, 50 | Trend, pullback; rebound uses fast; mean reversion uses slow |
| `breakout_period`, `exit_period` | 20, 10 | Breakout and contraction |
| `contraction_window`, `contraction_quantile`, `contraction_memory` | 50, 0.20, 5 | Contraction |
| `entry_z` | 2.0 | Mean reversion |
| `max_atr_fraction` | 0.03 | Mean reversion |
| `min_atr_fraction` | 0.02 | Rebound |
| `shock_fraction` | 0.03 | Rebound |

Positive integer lookbacks and holding limits, finite positive numeric
thresholds, `fast < slow`, `allocation <= 1`, `quantile <= 1`,
`shock_fraction < 1`, and `min_atr_fraction <= max_atr_fraction` are validated.
The shared config retains unused family fields for reproducible serialization;
they have no effect on that family's signal.

History requirements: trend `max(ATR+1, slow)`; breakout
`max(ATR+1, breakout+1, exit+1)`; contraction additionally
`ATR+window+memory+1`; pullback and mean reversion `max(ATR+1, slow+1)`;
rebound `max(ATR+1, fast, 3)`. Warmup trades are never synthesized.

## Evaluation and limitations

Use chronological development/validation/untouched-test partitions. At this
stage each run starts flat with independent warmup, so boundary liquidation and
continuous portfolio carry are not simulated. Record parameter variants and
the number of trials; testing six families already introduces multiple-testing
risk. Compare turnover, net returns, drawdowns, exposure, sample size, and
cost sensitivity across predeclared low/high-volatility regimes. Selection on a
validation return alone is not robustness. Require adequate independent trades,
an untouched test, and realistic data provenance before promoting any candidate.

`tests/python/test_candidates.py` exercises exact next-open fills and PnL for
every family, warmup/zero variance, future-data perturbation, repeatability,
rejection/cancellation recovery, true-range gap handling, macro gate exits,
actual-fill holding/stop state, and inadequate history capacity. These are
software correctness tests, not statistical or economic validation.
