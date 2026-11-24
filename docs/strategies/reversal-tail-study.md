# Expected-move reversal and last-hour put investigation

Status: exploratory research on observed SPX, **modeled derivatives**. This uses
QTE's Python research/pricing layer, not its C++ order-execution engine. No
actual futures backtest, verified tenfold option return, or deployable edge.

Open **Research → SPX regimes**, then “Expected-move reversal and last-hour
puts.” Session selectors show candles, Bollinger bands, daily sigma bounds,
entries/exits and the modeled put payoff path. Red shading marks a causal
intraday stress latch. Put entries/peak exits are selected with hindsight and
explicitly labeled; they are not strategy signals.

## Fixed hypothesis and execution

Daily expected-move proxy: prior SPX close × prior VIX / 100 / sqrt(252).
Prior daily values only; VIX is a 30-day measure, not a measured 0DTE smile.
Compare two fixed bounds, −1σ and −2σ. BB20 uses population standard deviation
of 20 consecutive completed one-minute closes, reset each session.

Buy intent when the preceding close is below its lower BB, the current positive
candle closes back above its lower BB, the last five lows touch the selected
daily bound, and current close is within 0.25 daily sigma of that bound.
Enter at the next complete minute close and hold five minutes. Maximum five
non-overlapping trades per session per variant; exit by 15:59 ET. These are
index reference values with modeled option costs, not executable SPX trades.

Veto prior stress/high-vol-down regimes. Intraday stress latches for the rest
of the session after a 2.5σ decline, or a 2σ decline with prior VIX ≥25. The
fixed no-veto comparator tests whether that guard matters. No intrabar stops
are inferred. Missing entry: no fill; missing exit after entry: retain trade
and charge its modeled maximum loss, excluding its missing points from totals.

Seven-day ATM long calls use QTE Black-76 with European exercise, $100
multiplier, flat prior-VIX IV, 4% rate and 1.3% dividend yield. Base adverse
half-spread is .25 points; wide is 1 point; .10 adverse rounding; fees are
$1.25/$2.50 per side. Hypothetical weekly contracts, not historical listings.

## Initial result: insufficient evidence of positive expectancy

40 eligible captured sessions: April 2025 and recent October–November 2026.
These selected windows are not a random sample, optimized result, or untouched
out-of-sample validation. One contract per trade; no account-return claim.

| Bound / veto | Trades | SPX points | Call P&L, base | Call P&L, wide |
|---|---:|---:|---:|---:|
| −1σ / on | 4 | +4.19 | −$40 | −$640 |
| −1σ / off | 5 | +5.20 | −$52.50 | −$805 |
| −2σ / on | 2 | +0.90 | −$75 | −$390 |
| −2σ / off | 6 | −70.89 | −$3,895 | −$4,830 |

The guard avoided large losses in this small sample; it does not establish a
profitable strategy. The GUI's MES arithmetic multiplies SPX points by $5 and
deducts $5/trade (two .25-point adverse ticks plus $2.50 commissions). It ignores
futures basis, roll, exchange execution and margin and is **not MES performance**.

## Last-hour put discovery

Screened 528 observed 15:00–16:00 ET sessions, October 1, 2024–November 13, 2026,
using Robinhood 30-minute SPX values. Selected the 20 largest final-hour declines
and fetched complete minute sessions. Seventeen have all 60 required completed
marks from 15:00 through 15:59; three incomplete sessions are excluded. This
selection misses large intrahour drops that recovered: not an exhaustive census.

Enumerate purchases at 15:00, 15:05, …, 15:50. Hypothetical same-day PM-expiry
SPXW puts have strikes rounded down to 5 points at .5/1/1.5/2 remaining model
sigma below the then-current spot; duplicate strikes removed. Strike selection
uses no later prices. SPXW is European, unlike American-style equity options.

Trading variance clock is remaining RTH minutes/(390×252). Flat frozen IV is
prior VIX, stressed at .75, 1.25, 2 and 3 times that value. No historical smile,
intraday IV, scheduled-event variance allocation or bid/ask calibration. Assume
.10 adverse half-spread, .05 price tick below 3 and .10 above, $1.25 per side,
and minimum theoretical entry mid .10. All 3,402 retained combinations,
including losers, are saved. Excluded below-floor premiums are counted.

“10×” means net liquidation proceeds / entry outlay ≥10, i.e. 900% profit.
Hindsight peak, fixed 15:59 exit, and first-10×-trigger/next-minute exit are
separate fields. No retrospective optimal price is presented as a strategy fill.

Nine of the seventeen days have a ≥10× **base-model** peak. Examples:

| Day / strike | Base peak | Same contract with 3× prior-VIX IV |
|---|---:|---:|
| 2024-12-18 / 5920 put | 169.10× | 4.60× |
| 2025-03-04 / 5810 put | 191.95× | 4.93× |
| 2025-05-08 / 5680 put | 46.68× | 2.45× |

These enormous model multiples are **not verified trades**. Tiny assumed entry
premiums plus a strike crossing produce large ratios; higher assumed IV sharply
reduces them. Targeted Robinhood history for the first two real expired contracts
returned 59 interpolated bars and one observed bar each (15:59–16:00), without
an observed entry/exit pair or NBBO. Zero actual tenfold returns are confirmed.

December 18 was an FOMC day: the [Federal Reserve's projections](https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20241218.htm)
raised the following year's rate and inflation forecasts relative to September.
March 4 coincided with tariff implementation, documented by the
[UK Parliament's research briefing](https://commonslibrary.parliament.uk/research-briefings/cbp-12094/).
These are retrospective macro context, not proven causes of individual minutes
or evidence that the options were cheap. No dealer inventory is inferred.

## Futures quotes

Saved delayed [CME MES](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.quotes.html)
and [NQ](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.quotes.html)
snapshots are displayed with contract, source timestamp and delay. They do not
refresh. Robinhood MCP currently exposes no futures quote/history tool; no
futures data feed or brokerage connection was added.

## Reproduce and next evidence

From the repository root, use a new empty output directory:

```bash
.venv/bin/python -m qte.reversal_tail_report \
  --captures build/robinhood-regimes-20261115 \
  --tail-captures build/reversal-tail-captures \
  --output build/reversal-tail-study-new
```

The GUI reads the fixed `build/reversal-tail-study` export. Captures and reports
are local ignored artifacts: preserve them separately when moving computers.
Exports include complete screen, all put candidates and reversal trades,
per-day chart JSON, assumptions and SHA-256 input/code/output manifest.

Next: independently sampled sessions; filter ablations; timestamped
scheduled-event inputs; then observed option bid/ask and smile calibration.
Evaluate growth, drawdown and target-hit probabilities only after those
distributions survive chronological testing.
The desired $10k→$50k month must not determine parameter selection or sizing.

## Fixed-horizon underlying study — November 17, 2026

Implemented separate offline research functionality, reusing `reversal_signal`
and its causal stress latch without changing the strategy. Run from the repo:

```bash
.venv/bin/python -m qte.forward_report \
  --captures build/robinhood-regimes-20261115 \
  --output build/my-forward-outcomes
```

Use a new output directory. This measures **underlying movement**, not executable
SPX, option, or futures returns. No engine fills or price models are introduced.

- Four fixed variants: -1/-2 sigma reversal, each with/without the selloff veto.
  Retain the common five-intent cap and five-minute cooldown plus entry delay;
  longer evaluation horizons may overlap. Missing future anchors do not remove
  selected intents or change subsequent selection. This differs deliberately
  from a filled-trade report, which can skip missing entries.
- Anchor at the next completed minute close. Score 1/5/15/30 minutes later,
  with the existing 15:59 ET cutoff. Retain missing entry/exit/interior-path and
  cutoff statuses; require consecutive observations rather than filling gaps.
- Controls are warmed-up clock moments from strictly earlier captured sessions,
  matched by effective causal regime and 30-minute ET bucket. Average within
  each control session, then weight sessions equally. Require three prior control
  sessions; show unavailable when there are fewer. Controls can include signal
  moments, and are not a causal experiment or a clean ablation of one feature.
- Report session-weighted and event-weighted return distributions in basis
  points, paired excess versus prior controls, and post-anchor close-only adverse/
  favorable excursions. No future intrabar high/low is used as an executable exit.
- Exports: `report.md`, `summary.json` with all variants/regimes and exclusions,
  `outcomes.jsonl` with prior control dates, `baseline-sessions.jsonl` with control
  counts/means, and `manifest.json` hashing captured inputs, code and outputs.

The initial output is `build/forward-outcomes-20261117`. Forty usable sessions
(15,600 bars) produce these **descriptive, previously inspected** results:

| Guarded signal | Events / sessions | 1-minute mean | 5-minute mean | 15-minute mean | 30-minute mean |
|---|---:|---:|---:|---:|---:|
| -1 sigma | 4 / 1 | +1.62 bp | +1.37 bp | +8.06 bp | -0.89 bp (3 events) |
| -2 sigma | 2 / 2 | +2.45 bp | +0.59 bp | -28.58 bp | -39.44 bp |

The guarded -1 sigma sample has insufficient earlier control sessions. For the
two guarded -2 sigma observations, session-weighted excess over prior controls
is +2.14/-0.87/-33.33/-44.89 bp at the four horizons. These counts cannot support
promotion, horizon optimization or a claim of a reliable one-minute reversal.
Both veto states and all outcomes remain in the report. Prior-only reference
construction does not turn this event-selected, already-inspected dataset into
a fresh holdout; historical publication vintages also remain unavailable.
