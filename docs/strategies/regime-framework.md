# Point-in-time macro and dealer research filter

Status: executable, deterministic research policy in `qte.regimes`; **not a
validated economic model, an automatic data feed, or an observed dealer-flow
detector**. Current synthetic experiments test wiring and accounting, not trading
merit. The initial scope is long-only liquid US ETFs at hourly/daily research
frequencies. An ETF's economic exposure still matters: an equity-market policy
must not be silently applied to inverse, leveraged, bond, or commodity ETFs.

## Inputs and availability

`MacroObservation(feature, value, reference_ns, available_ns, source_id,
vintage_id)` contains one of `growth_z`, `inflation_z`, `liquidity_z`, or
`volatility_pct`. Times are signed 64-bit UTC nanoseconds; reference time cannot
follow availability. Values must be finite (not booleans). Provider payloads and
normalization belong outside the C++ core. Source identity must identify the
series, units, transformation version, and calibration policy; vintage identity
must identify the actual release or revision used, not merely the download date.

At decision time T, the filter sees only observations with `available_ns <= T`.
For each feature it selects the newest visible reference period, then its latest
visible revision. A later release revising an older period cannot overwrite a
newer reference period. Ambiguous same-period/same-release records, reused
period/vintage identities, and mixed sources for a feature are rejected rather
than resolved by input order. The constructor copies inputs; queries neither
mutate them nor consult wall-clock time. Only consumed visible observations are
returned in the immutable `explain(T)` decision.

Missing/stale required features prohibit entries; they are not silently neutral
or forward-filled forever. Default maximum reference ages are 45 calendar days
for the three macro scores, four days for volatility, and one day for dealer
input. Equality is permitted; greater age is stale. Reference-based age prevents
a revision from making an economically old observation appear fresh. These are
configurable conservative research assumptions, not exchange calendars or
release schedules. Quarterly inputs require a deliberately different policy.

Same-time releases are available to a bar-close decision by explicit zero-latency
convention, matching the canonical engine boundary. For historical releases with
date-only provenance, the adapter must assign a conservative **later known
availability** (e.g. next eligible session) instead of inventing a publication
time. Hourly research especially needs genuine release/ingestion timing.

ALFRED preserves initial releases and revisions; ordinary FRED defaults expose
today's knowledge of past values. Use point-in-time vintage data, not today's
revised history, in historical signals. A vintage date alone does not establish
an intraday publication timestamp. See [FRED real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
and [ALFRED](https://fred.stlouisfed.org/docs/api/fred/alfred.html).

## Experimental macro specification

The executable policy consumes already-standardized macro features; it does not
claim to derive them from ETF OHLCV. A future provider adapter must preregister
its series and transformation before testing. A proposed baseline specification,
**not yet backed by downloaded vintages**, is:

- Growth input: `100 * (payrolls[t] / payrolls[t-3] - 1)` from monthly total
  nonfarm payrolls; larger means stronger recent employment growth.
- Inflation input: `100 * (CPI[t] / CPI[t-12] - 1)` from monthly CPI; larger is
  greater inflation pressure, not a universal assertion that inflation is bearish.
- Liquidity/financial-conditions proxy: negative weekly NFCI; higher means easier
  measured conditions. This is not a direct measurement of investable liquidity.
- For each input x, `z[t] = (x[t] - mean(x[t-60:t])) / sample_std(x[t-60:t])`,
  using the prior 60 then-available observations and excluding x[t]. All lagged
  values must be reconstructed using the vintage available at decision time.
  Insufficient history or zero standard deviation produces **missing**, not zero.
  Freeze this specification before inspecting validation/test results. Changing
  the series, window, or transformation creates another tested hypothesis.
- Volatility input: an explicitly sourced annualized percentage-point observation,
  e.g. a point-in-time VIX close. `20` means 20%, not `0.20`. Realized volatility
  could instead be a separately identified and tested feature; it must not be
  silently substituted for implied volatility.

The policy is deliberately transparent:

`macro_score = (growth_z + liquidity_z - inflation_z) / 3`

`score >= 0.5` is risk-on; `score <= -0.5` is risk-off; otherwise neutral. Equal
weights, feature signs, and thresholds are **untested preregistered hypotheses**,
not estimates of causal economic effects or a recommended investment model.
Volatility below 15 is low, [15,25) normal, [25,40) high, and >=40 extreme.
Thresholds and age limits live in frozen `RegimeConfig` and belong in run manifests.

## Entry routing and execution

| Macro | Volatility | Permitted long-entry families |
|---|---|---|
| Risk-on | Low/normal | trend, breakout, contraction, pullback, mean_reversion |
| Risk-on | High | trend, breakout, rebound |
| Neutral | Low/normal | pullback, mean_reversion |
| Neutral | High | none |
| Risk-off | any | none |
| any | Extreme, missing, stale | none |

`MacroRegimeFilter(observations, config, dealer_observations=...)` implements
`filter(timestamp_ns, family) -> bool`. Unknown families raise rather than silently
disable a misspelled strategy. `explain(timestamp_ns)` supplies the classification,
score, exact consumed inputs, permitted families, and reasons. The strategy's
own deterministic entry signal must also be true. The gate does not submit
orders, size positions, modify cash, bypass core risk checks, or promise fills.
It is an **entry-permission** policy: exits must remain possible when the gate
blocks new risk. Whether a gate change also triggers an exit must be explicit in
the strategy specification. Execution remains next actual symbol open under the
engine's documented costs/open-only model; no claim of stop execution intrabar.

## Dealer overlay: hypothesis, not hidden knowledge

`DealerObservation` requires reference/availability/source/vintage identity,
underlying, methodology identity, classification, and signed net gamma expressed
as USD delta-exposure change per +1% underlying move. The configured underlying
(default SPX) must match. If it is used as a broad-market proxy for SPY or other
ETFs, that cross-instrument mapping must be explicit in the experiment manifest;
gamma magnitudes for different indices cannot be mixed.

Classifications are:

- `observed`: positioning input reported by a source that can identify dealer
  inventory. Gamma still depends on a valuation model; this does not mean actual
  hedge trades were observed. Retain the source's scope and methodology.
- `model_estimate`: inferred net positioning/Greeks, with explicit assumptions.
  Routing requires `allow_model_estimates=True`, off by default.
- `proxy`: price/volume or another indirect behavior label; **never** qualifies
  for dealer-dependent routing, even when estimates are enabled.

`dealer_mode="ignore"` is the default and makes no dealer claim. `optional` uses
qualified fresh inputs if present and otherwise retains the macro-only gate,
labeling dealer state unknown. `required` blocks all entries without qualified
fresh input. Unknown is never treated as positive gamma. Accepted negative gamma
removes pullback, mean-reversion, and rebound entries; accepted positive gamma
removes breakout and contraction entries; exact zero leaves the macro gate
unchanged. The overlay only removes permissions and never overrides risk-off or
extreme-volatility vetoes. No gamma-magnitude threshold is fitted.

The hedge hypothesis is conditional: a short-gamma dealer maintaining a delta
hedge would sell as prices fall and buy as prices rise; long-gamma hedging acts
in the opposite direction. Magnitude, hedge timing, other risks and participants
matter; these mechanics are not proof of realized flow or predictive profit.
Gross options volume and open interest alone do not identify net dealer inventory.
[Cboe's positioning analysis](https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options)
explains the directional mechanics and why outside positioning estimates rest on
assumptions. QTE does not infer “dealers buying into selling” from an ETF bar.

## Verification and remaining decisions

Deterministic tests cover exact threshold equalities, future visibility,
staleness, revisions, duplicate/source ambiguity, input permutation, immutable
snapshots, qualification, unknown/zero gamma, and macro veto dominance. Synthetic
labels must say synthetic in source identities and reports. Scripted labels are
not observed market regimes and may make routing look useful by construction.

Before economic validation: obtain permitted point-in-time macro and options
data; implement independently tested provider adapters/causal normalization;
preregister the ETF-to-macro/dealer mapping and exposure sizing; choose hourly
session-aware or daily sampling; and evaluate frozen strategy/gate parameters on
chronological validation, untouched holdout, costs and multiple observed regimes.
Comparing gates is parameter/model selection and consumes validation data; keep
failed candidates and do not describe the winning historical variant as proven.
