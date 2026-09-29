# Strategy comparison and selection protocol

`qte.experiments` compares a declared candidate slate using the existing canonical
dataset, execution, risk, accounting, and analytics contracts. It is a research
screen, **not evidence of profitability** and not permission to trade an account.

## Chronology and ownership

Declare every `ResearchWindow(name, data, role, regime, evidence)` before running.
Roles occur in this order: one or more `train` windows, one or more `validation`
windows, then one or more final `test` windows. Windows must be chronological and
nonoverlapping, with identical symbol universe, currency, and bar interval. Names
must be unique. A boundary at the previous window's `end_ns` is permitted. Gaps
are recorded, not repaired. This does not create an exchange-session calendar or
make unsupported provider timeframes safe.

Training is diagnostic only; this version does not estimate parameters. Each
candidate/cost/window receives a **fresh strategy, engine, cash balance, and
indicator warmup**. Positions and history do not cross window boundaries. Open
positions are marked using the engine's equity invariant; there is no invented
last-bar liquidation. Closed and open trade counts are reported separately.
This is independent-window robustness testing, **not rolling walk-forward
optimization** and not the return of one continuous portfolio.

`Candidate(name, parameters, history_capacity, factory, eligible=True)` contains
an immutable deep snapshot of finite JSON parameters. Supply a factory returning
a new `qte.Strategy`; reuse of a still-existing object is a recorded scenario
failure. Weak references enforce freshness without retaining strategy objects.
The factory itself must be deterministic and must not read future/global market
data, mutate its parameters, or retain state between invocations. Python callable
closures cannot be sandboxed by this harness. Declare all behavior-changing
parameters, including external feature-source identities. Regime labels are
reporting strata, not automatically provided to the strategy as oracle signals.

Use `eligible=False` for cash and risk-matched buy-and-hold benchmarks. Their
results remain visible, but they cannot become a selected strategy. Benchmark
superiority is **not** currently an automatic gate; inspect it explicitly before
claiming strategy value beyond market exposure.

## Frozen policy and validation selection

The slate, window identities, gate settings, cost assumptions, source identity,
and random seed are hashed before the first run. The manifest is reproducible
but is **not externally timestamped preregistration**. Do not change the slate or
thresholds after inspecting validation results and then call that run confirmatory.
Changes create another research trial that must remain in the research ledger.

Default base costs are 1 bp commission, 2 bps spread, and 1 bp slippage; stress
costs are 5/10/5 bps. These are illustrative declared assumptions, not calibrated
ETF transaction-cost estimates. Spread retains the core's half-spread-per-side
semantics. Base costs must not all be zero; stress must be componentwise at least
base, and strictly larger in at least one component. Both scenarios use the
same long-only, unlevered risk settings. The engine decides whether to accept
orders and how they execute; this module does not fill orders itself.

Default promotion-screen gates are:

- At least three validation windows and twenty closed trades across **stress**
  validation runs; counts are not doubled by including the base scenario.
- Coverage of every predeclared regime (default labels `low_vol`, `high_vol`).
- Arithmetic mean of stress window returns strictly greater than
  `min_stressed_return` (default zero).
- Every stress window return at least `worst_stressed_return` (default -3%).
- Every base and stress window drawdown at most `max_drawdown` (default 20%).
- No failed train/validation scenario, no unavailable factory source identity,
  and no synthetic data anywhere in the declared experiment.

Zero-return windows are permitted: a macro-aware strategy may intentionally
hold cash in an adverse regime. An all-cash strategy fails the average-return
and closed-trade gates. Equal-weight mean window return is a screening statistic,
not compounded performance; lengths and exposure should be comparable. Thresholds
are configurable research choices, not universal economic standards.

Eligible candidates are ranked by descending **worst stress-window return**, then
descending median stress-window return, then ascending candidate name. The full
mean, score, and all exclusion reasons remain in the report. A failure, no trades,
or no passing candidates is a valid result, never a reason to erase a trial.

Only the locked validation winner runs against final test windows. The same
trade, regime, mean-return, worst-window-return, and drawdown gates then determine
its holdout result; the validation-window-count gate does not apply to holdout.
A failed holdout **does not select or test the runner-up**. Passing is labeled
`passed_research_screen_only`, not "profitable" or "ready for production."
Synthetic runs select nobody and do not inspect the final holdout.

## Evidence, artifacts, and uncertainty

`Evidence` requires `kind` (`synthetic` or `historical`), source description,
SHA-256 of the source artifact, availability policy, and corporate-action policy.
These are caller declarations; recording a digest does not authenticate a market
data vendor, verify that the digest belongs to the bars, establish point-in-time
macro availability, or account for dividends/splits. Verify adapter outputs and
retain original artifacts. Obvious `synthetic` dataset source IDs cannot be
promoted simply by labeling the evidence historical, but arbitrary false source
claims cannot be detected automatically. Fabricated unit tests exercise gates
using explicitly documented declaration fixtures; they are never real evidence.

Each report retains every attempted scenario and its errors, cost label, return,
drawdown, exposure, turnover, closed/open trades, fills, orders, rejection and
cancellation counts, final equity, and normalized engine configuration. Window
records contain data hash, original source ID, source digest, timeframe, currency,
universe, bounds, count, and gaps. Candidate records contain canonical parameters,
factory identity, and source-file SHA-256 (unwrapping `functools.partial`). The
loaded native binary and current package source are fingerprinted at run start.
Their identities and external factory source identities are checked again before
returning a report. A mid-run source change aborts the experiment; any exported
artifacts remain incomplete and must not be treated as a completed result.
Use a fresh process after editing source: this end check cannot prove that
arbitrarily retained Python objects match files edited before the run began.

`run_experiment(..., on_result=callback)` optionally supplies each successful
`ScenarioResult` and raw engine result for fills/equity artifact export. It does
not retain raw results. Callback I/O failures abort the experiment rather than
becoming misleading candidate failures; callers should write a completion marker
only after the full report and all artifacts succeed. Reports can be serialized
with `json.dumps(dataclasses.asdict(report), allow_nan=False)`.

No annualized statistics are manufactured from irregular event timestamps. This
harness reports nonannualized metrics only; use the separate explicit sampling
contract when adding session-aware annualization. It also does not implement
parameter optimization, rolling walk-forward fitting, bootstrap confidence
intervals, multiple-comparison-adjusted significance, or Monte Carlo analysis.
Validation ranking creates selection bias even without a parameter grid. Add
independent historical holdouts, transaction-cost calibration/sensitivity,
benchmark comparison, regime coverage, and multiple-testing controls before
making economic claims. Actual dealer positioning requires suitable timestamped
options/position data; neither regime labels nor synthetic paths establish it.

## Minimal integration

```python
from qte.experiments import Candidate, ExperimentConfig, run_experiment

slate = [Candidate("my-hypothesis", {"lookback": 20}, 64,
                   lambda: MyStrategy(lookback=20))]
# windows are predeclared ResearchWindow objects with audited Evidence.
report = run_experiment(
    slate, windows,
    ExperimentConfig(required_regimes=("low_vol_trend", "high_vol_range")),
)
print(report.selected_candidate, report.holdout_status)
```

Use a source-file-defined factory for a promotable historical experiment; an
interactive lambda without inspectable source is explicitly excluded. A missing
winner is an informative answer, not a broken experiment.
