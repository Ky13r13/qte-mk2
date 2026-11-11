# Nasdaq gamma research: two distinct strategies

Research dates: 2026-10-28 and 2026-11-03. The user selected **both** true
options-plus-futures gamma scalping and a futures-only dealer-gamma signal.
Status: **D1 policies, D2a settlement and D2b.1 margin budgets; not executable strategies
or historical backtests**. These remain different strategies with separate results.
No historical options dataset, account connection or profitability evidence was
obtained. Existing Windows GUI work is preserved and paused for this request.

## What the research supports

True gamma scalping holds positive option gamma and trades the underlying to
reduce changing delta. Hedge trading gains are not the strategy's total profit:
option decay, repricing, spreads, fees and financing must also be counted.
Buying dips/selling rallies with futures alone does not create an option's
convex payoff. See [CME's gamma explanation](https://www.cmegroup.com/education/courses/option-greeks/options-gamma-the-greeks)
and [Schwab's gamma-scalping primer](https://www.schwab.com/learn/story/gamma-scalping-primer).

NQ futures have a $20/index-point multiplier and 0.25-point tick ($5); MNQ
futures have a $2/index-point multiplier and 0.25-point tick ($0.50).
These are contract metadata, not equity share quantities.
[NQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html),
[MNQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html).

The proposed options are European-style NQ weeklies. They can deliver an
underlying futures position at exercise; do not treat their expiration as
ordinary equity-option expiration or assume a cash premium payoff is sufficient.
The first experiment should exit before expiration instead of pretending to
handle delivery. [CME weekly-option specifications](https://www.cmegroup.com/trading/equity-index/e-mini-nasdaq-100-weekly-options.html).

Micro-option product versions must not be mixed: CME's current Micro FAQ
describes financially settled European options, unlike physically delivered
E-mini options. Historical contract identity and settlement rules must travel
with data. This proposal uses MNQ **futures for hedging**, not an assumption that
legacy Micro option series remain available.
[Current Micro-option FAQ](https://www.cmegroup.com/articles/faqs/faq-options-on-micro-e-mini-s-p-500-and-nasdaq-100-futures.html).

Frequent hedging is not free: discrete hedge error and transaction costs trade
off against one another. Leland's original paper supports modeling this
explicitly, not assuming continuous, costless rebalancing.
[Leland (1985)](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1985.tb02383.x).

## A. True gamma scalping: first full-strategy specification

This is a deliberately mechanical baseline, not a claim of trading edge.
Parameter choices below are proposed research defaults, not fitted values.

**Inputs.** Contract-specific NQ option call/put bid and ask, underlying NQ and
hedge MNQ quotes, point-in-time deltas/gammas with model identity, quote sizes,
expiry/exercise/underlying metadata, explicit session calendar, fees and margin
assumptions. Timestamps are signed UTC nanoseconds with separate reference and
availability times. No continuous back-adjusted futures series for fills. No
OHLCV-derived dealer inventory. No revised/end-of-day Greeks used earlier.

**Contract selection.** At the session's configured entry time, consider only
European NQ weekly expiries 5–10 calendar days away, nearest to seven days;
tie-break by earlier expiry. Choose a common call/put strike nearest the visible
NQ midpoint; tie-break by lower strike, then canonical contract ID. Both legs
must reference the same specific NQ futures contract. MNQ must have an explicit
same-quarter mapping; the assumed hedge sensitivity ratio is 1, with basis risk
retained and measured rather than treated as a guaranteed arbitrage identity.
Missing mappings or eligible contracts mean no entry; never guess a front month.

**Entry.** One attempt per full regular US equity session, proposed 09:35 New
York time. The adapter supplies exact UTC entry/exit times, including DST and
holidays; skip shortened sessions initially. Buy one call and one put only if
both quotes are valid, fresh, positive and non-crossed, requested displayed size
is available, and configured spread, premium and margin caps pass. Required caps
and per-contract fee schedules have no invented brokerage defaults. No naked
short options. This baseline has no ex-post volatility/regime selection filter.

**Features and hedge.** Delta is per option in futures-equivalent units, not
percent values or dollar delta. For signed filled option contracts q_i,
option multipliers M_i and visible deltas delta_i:

`option_delta_dollars_per_point = sum(q_i * M_i * delta_i)`

`target_hedge = round_half_away_from_zero(-option_delta_dollars_per_point / hedge_multiplier)`

The numerator is USD per index-point change, not notional dollar exposure.
Greek provenance must identify differentiation against the referenced NQ futures
price; spot delta, percent-scaled delta and vendor dollar-delta fields are not
interchangeable inputs.

For one NQ straddle hedged with MNQ this becomes
`round_half_away_from_zero(-10 * (call_delta + put_delta))`.
Gamma is a diagnostic/eligibility check, not a substitute for recomputing delta.
Rebalance on an explicit five-minute decision grid when target differs from
filled hedge quantity by at least one contract; keep one pending hedge request.
After an option fill, also request a hedge recalculation at the next eligible
event using then-visible Greeks; do not assume both entry legs filled together.
Partial fills affect the next target through authoritative positions. Rejection
or cancellation permits a later retry; it never fabricates a fill. Preserve
long-gamma inventory and honor separately configured hedge/risk caps.

**Exit.** Start liquidation at proposed 15:45 New York time; no new entry after
the entry attempt. Also request liquidation on configured maximum loss, invalid
contract mapping or imminent expiry. Cancel pending risk-increasing orders
first. Legging failures require explicit unwind/reconciliation, not an assumed
atomic straddle fill. Missing executable quotes may prevent liquidation: report
the remaining exposure and invalidate a supposedly completed intraday run.
Do not declare flat, delete positions or invent a closing fill.
Unresolved positions or pending orders prohibit the next session's entry.
Proposed entry-leg timeout is 30 seconds: cancel remaining entry orders and
unwind filled exposure. A cancel request is not a cancellation acknowledgement;
late fills still update authoritative positions and the liquidation target.

**Execution.** Signals consume only observations available at the decision.
Orders cannot fill on the quote used to generate them; require a subsequent
eligible quote and explicit latency. Buys cross ask, sells cross bid, with
configured adverse slippage, contract tick rules, sizes and per-contract fees.
Do not clip costs to future bar ranges or use the current open-only equity
simulator to represent quote-based option execution.

**Parameters/state.** Record all selection, session, freshness, spread, premium,
margin, latency, hedge-band and loss-limit settings. State includes selected
contract identities, session/entry-attempt identity, owned pending order IDs,
latest consumed data identities, and liquidation state. Positions, cash, fills,
PnL and margin remain engine-owned, never a second strategy-side ledger.

## B. Dealer-gamma futures: separate directional hypothesis

This policy does not own option convexity and is not true gamma scalping. Its
regime assumptions are hypotheses to test, not evidence of actual dealer trades.
Neither price bars nor unsigned open interest reveal who holds which positions.

**Inputs.** One explicit contract/underlying mapping; causal futures marks and
qualified `DealerObservation` values with reference/availability timestamps,
source, vintage, methodology, underlying and `usd_per_1pct_move` unit. Model
estimates require opt-in; proxies are rejected. No implicit SPX-to-Nasdaq mapping
or continuous/back-adjusted series as executable contract prices.

**Features.** Last 20 lagged visible marks by default. Use their population
standard deviation and mean; current mark is excluded. Positive dealer gamma
uses `z = (current - lagged_mean) / lagged_std`. Negative dealer gamma uses
strict breaks above the lagged maximum or below the lagged minimum. Values and
timestamps must be finite/valid and point-in-time; future revisions cannot alter
an earlier decision. Zero variance does not supply a mean-reversion signal.

**Entry/exit.** Positive gamma: buy at z <= -1, sell short at z >= 1; exit at the
lagged mean cross. Negative gamma: buy above the lagged maximum, sell below the
lagged minimum; exit at the lagged mean cross. Close on a gamma-regime flip,
unknown/stale/unqualified dealer input or stale price input. A reversal first
targets flat; a later event may enter the opposite side. A pending order
suppresses new intents; it never implies a fill or successful cancel.

**Sizing/state.** Default one contract, explicit cap one; these are research
defaults, not capital or margin advice. Policies consume authoritative current
contracts, pending target and the position's entry-regime sign. They return a
target/reason only. Session exits, loss caps, margin reservations, lifecycle
callbacks and actual execution belong to D2–D4, not this pure signal calculation.

**Evaluation.** Freeze positive-gamma mean-reversion and negative-gamma breakout
as separately labeled candidates before testing the router. Compare them with
unfiltered versions, cash and directional baselines on chronological holdouts,
including all failed/no-trade runs. Dealer classification/model provenance and
cost sensitivity must appear in every eventual report.

## Shared implementation gates

The new instrument metadata distinguishes futures/options and carries actual
multipliers. The existing Portfolio supports multiplier-scaled option premiums,
futures carrying PnL and explicit variation-settlement events with separate cash
and economic bases. Settlement does not double-count trade PnL or refresh marks. See
[ADR 0013](../adr/0013-derivative-research-foundation.md). Equity replay remains
unchanged. Derivative datasets, equity risk and open-only execution are explicitly
blocked until margin, settlement-source integration, quote execution and lifecycle gates pass.
Renaming an equity symbol NQ is not a supported futures backtest.

Implement in independently testable increments:

1. Canonical derivative metadata and point-in-time option/quote observations;
   pure hedge-target math tests, including multiplier, sign and tie rounding.
2. Extend the **existing** portfolio authority for premium-valued options and
   futures variation settlement; checked multipliers, settlement idempotence,
   reversals, fees and explicit margin/financing assumptions. Preserve the
   existing equity profile and its accounting invariants.
3. Contract-aware risk and subsequent-quote execution; entry-leg failures,
   partial fills, stale data and liquidation failure regressions.
4. Strategy callbacks and configuration, then deterministic synthetic scenarios
   through the real engine and normal report/export surfaces. Keep unavailable
   derivatives out of executable strategy choices until these gates pass.

Pure decision code lives in `python/qte/strategies/nasdaq_gamma.py`.
`GammaHedgePolicy` calculates the options hedge and `DealerGammaPolicy` calculates
the separate directional target. Neither inherits the executable Strategy API or
appears in runnable GUI/CLI choices yet. A missing Greek must not silently remove
an existing hedge while leaving options exposed; it blocks a hedge decision and
requires the future lifecycle controller to reconcile/liquidate the whole book.

### Try the implemented policy layer

From the repository root, using the existing virtual environment:

```sh
.venv/bin/python examples/nasdaq_gamma_policy.py
.venv/bin/python -m pytest tests/python/test_nasdaq_gamma.py -q
```

The example is explicitly synthetic: a hand-specified option pair targets -3
MNQ contracts, and the separate negative-gamma breakout targets +1 future.
It makes no orders, fills, performance report, network call or profitability claim.
On Windows use `.venv\Scripts\python.exe` with the same script path once the
native QTE extension is built; this example has no platform-specific I/O.

`target_contracts=None` means no new intent (already at target, pending, or
blocked—inspect `reason`); zero means a request to become flat, not a fill.
`desired_contracts=None` marks an unavailable/suppressed decision.
`uncapped_contracts` preserves the calculated hedge before clipping, and
`hedge_target_capped` makes residual delta risk explicit. Option quantities come
from filled positions; short options are rejected by this baseline. Zero option
inventory can request closing an orphan hedge without requiring stale Greeks.

Pure-policy freshness defaults are five minutes from reference time for Greeks
and futures marks, one day for dealer observations. Latest visible reference
is primary, latest visible revision secondary; ambiguous duplicates fail closed.
The lagged price baseline chooses revisions available at or before the current
mark's reference time. These policies do not schedule the five-minute hedge grid
or manage sessions; those remain controller responsibilities. Production
provenance must identify real sources, not reuse the example's synthetic labels.

## Evaluation and remaining choices

The implemented [VIX expected-move sidecar](vix-expected-moves.md) supplies
explicit volatility scenarios, daily/intraday sigma bands and completed-candle
OHLC endpoint distances. VX is an SPX-volatility proxy, not Nasdaq gamma or a
calibrated trade probability. Optional external dollar-gamma analysis excludes
theta and costs; it does not implement or validate either strategy above.

Synthetic paths test correctness only. Report full combined option-plus-hedge
PnL, separate option/hedge contributions, fees, turnover, residual delta, peak
margin and unclosed exposure. A rising/falling/choppy path must not be selected
as evidence of profitability. No synthetic option prices represented as trades.

Freeze the baseline before chronological training/validation/untouched-test
splits. Compare unhedged straddle, fixed-cadence hedging and threshold hedging at
base/stressed executable costs. Any realized-versus-implied entry filter is a
separate preregistered experiment: estimate the same holding-window variance
using past sessions only, not an inconsistent comparison of trading-time and
calendar-time annualized volatility. Then run walk-forward, neighboring-band,
latency/spread and regime sensitivity, retaining failed/no-trade candidates.

The strategy-type choice is resolved: **both**, developed separately.
Before real-data evaluation: obtain licensed historical quotes/contract history,
choose Greek provenance, fees, margin policy and capital limits. No account,
data purchase, live execution or profitability claim is authorized by this note.

## Repository checkpoint

D1 adds direct-fill derivative accounting, pure causal decision rules and
execution gates, preserving prior uncommitted Windows persistence work. The
latest validation record and remaining work are in
[the task log](../gui-task-log.md). The specifications above include future
full-strategy behavior; do not mistake policy unit tests for a completed backtest.

Existing-baseline verification, not gamma-strategy validation:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build/local-dev: no work; 15/15 CTest passed
.venv/bin/python -m pytest tests/python/test_research_config.py tests/python/test_lifecycle.py tests/python/test_explicit_availability.py -q --maxfail=1 --tb=short --basetemp=build/test-gamma-research-baseline-20261028
# 55 passed
git diff --check
```

At the initial research checkpoint, only public research pages were consulted.
The later VIX sidecar smoke check downloaded one public Cboe settlement file;
no NQ/options/dealer dataset, broker request, package installation or Git
history/remote operation was made.
