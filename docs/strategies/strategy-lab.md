# Strategy lab: macro context to testable hypotheses

This offline lab executes strategies through the existing C++ engine. It does
not create a second Python accounting system. No strategy has passed a real-market
economic validation gate. All current lab prices and macro inputs are fabricated.

## Run it

From `/home/glsu6/Documents/qte`, using the installed repository environment:

```sh
.venv/bin/python -m qte lab --config examples/strategy-lab.json --output build/my-strategy-lab
```

Choose a new output directory for each experiment; overwrites are rejected.
The configuration fixes seeds, window length, clocks, shared strategy parameters,
allocation/risk limits, costs and screening rules. It does not run a parameter
search. Defaults allocate 10% of equity without leverage: equal target notional,
**not equal volatility or loss risk**. Effective defaults are recorded in reports.

For a single candidate on the small existing CSV fixture:

```sh
.venv/bin/python -m qte run --config examples/trend-research-run.json --output build/my-trend-run
```

`run` supports `trend`, `breakout`, `contraction`, `pullback`, `mean_reversion`,
`rebound`, and the existing `moving_average_regime`. The original SMA example
and API remain supported. Single runs are ungated; macro-aware comparisons use
the lab or the explicit Python filter API.

## Specifications

- [Candidate library](candidate-library.md): inputs, exact formulas, entry/exit
  predicates, sizing, close-based stops, parameters and lifecycle state.
- [Macro/dealer policy](regime-framework.md): point-in-time inputs, thresholds,
  missing/stale behavior, routing and qualified dealer overlays.
- [Single-portfolio router](macro-router.md): declared priority and an actual
  exit-before-switch lifecycle, with no retrospective winner selection.
- [Selection protocol](selection-protocol.md): chronological development,
  validation selection, locked holdout, costs and full trial retention.

The macro policy permits families; each strategy must still generate an entry
signal. Strategies submit intent, core risk decides permission, execution emits
fills, and only the C++ portfolio accounts for them. Stops and macro exits are
bar-close/next-open decisions, not intrabar protection or live emergency controls.
Rejection/cancellation feedback permits recovery without duplicate pending orders.

## What synthetic evidence means

Nine scenarios exercise low/high-volatility trends and ranges, contraction,
selloffs, rebounds, gaps and regime switching. Each has a distinct seed and
chronological train/validation/test windows. Fixtures include positive OHLC,
zero-volume observations and intentionally missing bars. Gaps are not repaired.
The current open-only model assumes unlimited liquidity and can fill even on a
zero-volume bar; these fixtures do not establish executable market liquidity.

`hourly` and `daily_24h` are continuous synthetic clocks. `daily_24h` is **not**
a US exchange session. Daily/session adapters must separately handle publication
availability, holidays, DST and shortened sessions. Long real ETF histories
also need split/dividend accounting or genuinely verified action-free windows.
Today's adjusted ETF history is not a drop-in substitute.

Macro features are scripted from generator state with a one-bar release lag.
The correlation exists by construction: improved filtered returns would not
establish forecasting value. No observed releases, VIX, options inventory,
dealer flow or live market data are used. Dealer routing is disabled in the
lab; sign/availability behavior has separate invented-observation unit tests.

Six families are compared gated and ungated (12 variants), plus one single-book
macro router, cash (zero interest) and single-allocation buy-and-hold: 15 entries
in the complete slate. Benchmarks are not selectable. Every
window starts flat and warms up independently. Open episodes remain marked,
not forcibly liquidated; their counts are separate from completed trades.
Do not compound independent window scores into a continuous portfolio return.
A macro-routed portfolio must be its own engine run, never assembled from
retrospectively chosen per-regime winners.

Synthetic evidence always yields **no promoted candidate**. Final test windows
are declared but not evaluated without an eligible validation selection. All
attempted successful, flat, rejected and failed trials remain visible. Tuning
after reviewing these paths creates a new research trial, not confirmation.

## Artifacts

Root: `configuration.json`, `summary.json` and a last-written `complete.json`
with SHA-256 checksums. A missing marker means the export is incomplete. Each
clock directory contains:

- `slate.json`: all candidates and effective settings.
- `generator.json`, `data/*.csv`, `macro.json`: fabricated data, seeds, source
  identities, availability and filter settings.
- `regime-decisions.csv`: explanatory as-of permissions at each close.
- `report.json`: every scenario, exclusion, source/dataset identity and gate.
- `comparison.csv`: compact nonannualized outcomes and errors.
- `runs/*/{equity,fills,orders}.csv`: actual C++ replay facts for successful
  scenarios. Errors stay in the report, not invented logs.

No credentials belong in configurations/reports. Finish code edits before a
reference run and use a fresh process. Source hashes are not externally
timestamped preregistration or proof an arbitrary closure never read future data.

## Next evidence

Obtain permitted real ETF history and vintage-aware macro observations. Register
ETF mappings and a compact slate before inspecting results; compare validation
against benchmarks at calibrated costs, lock a candidate and test an untouched
holdout. Add rolling walk-forward, neighboring-parameter sensitivity, block-resampled
uncertainty and multiple-testing controls. Paper connectivity requires separate
reconciliation/restart milestones and explicit account approval.
