# Strategy-research and broker-boundary validation — 2026-09-22

## Recovery and scope

Resumed after usage interruptions and a computer shutdown. Source files, `.venv`
and the preliminary 2026-09-20 artifacts survived. That preliminary run has 14
entries and predates the router; its 3,092 checksums were revalidated. It is kept
intact, not overwritten. The final reference path is
`build/strategy-lab-20260922` and is valid only with its `complete.json` marker.

The development task list now includes R2–R6 research/data/robustness/performance
work, M17–M20 durable paper integration, and M21 broker connection stages.
No existing user work or Git history was discarded. Many older M13–M16 files
were already modified/untracked before this effort; they remain preserved.

## Delivered files and behavior

| Area | Files |
|---|---|
| Strategy families and single-book routing | `python/qte/strategies/candidates.py`, `router.py`, updated `__init__.py` |
| Point-in-time macro/dealer policy | `python/qte/regimes.py` |
| Frozen-slate comparison and evidence gates | `python/qte/experiments.py` |
| Seeded scenarios and artifact orchestration | `python/qte/synthetic.py`, `strategy_lab.py` |
| Usable commands/configurations | Updated `python/qte/cli.py`; `examples/strategy-lab.json`, `trend-research-run.json` |
| Read-only broker boundary | `python/qte/brokers/__init__.py`, `readonly.py` |
| Compatible comparison metadata | Updated `bindings/module.cpp`, `python/qte/research.py` |
| Behavioral regressions | New `test_candidates.py`, `test_regimes.py`, `test_router.py`, `test_experiments.py`, `test_synthetic.py`, `test_strategy_lab.py`, `test_candidate_cli.py`, `test_brokers.py`; updated binding and SMA comparison tests |
| Specifications and task list | `docs/strategies/*.md`, ADR 0009, `docs/broker-connections.md`, updated README, architecture, roadmap and research workflow |

Six long-only hypotheses have exact formulas, configurable parameters, actual-fill
state, causal entry/exit signals, whole-share sizing and lifecycle recovery. The
macro router is its own strategy/portfolio, with exit-before-switch behavior.
The macro filter respects release/vintage availability and rejects missing/stale
inputs. Dealer proxies never masquerade as observed positioning. Baselines and
failed/flat trials remain visible; synthetic results cannot promote a strategy.

Broker clients perform only explicit GET calls. Robinhood methods cover official
crypto account/product/holdings/order reads, using an injected Ed25519 signer.
Schwab operations are resolved from a caller-supplied official OpenAPI export;
no endpoint schema or successful account connection is claimed without that
export and authenticated validation. No strategy is wired to either client.

## Build and tests

Native toolchain: `/usr/bin/g++`, GCC 16.2.1. Python 3.14.7 and all imports/tests
use `/home/glsu6/Documents/qte/.venv`. CMake cache confirms the native compiler
and `build/local-dev`; the stale `build/dev` path is unused.

Commands executed from repository root during this work/recovery:

```sh
PATH="$PWD/.venv/bin:$PATH" CXX=/usr/bin/g++ cmake --preset dev
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
PIP_NO_CACHE_DIR=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-final-20260922
.venv/bin/python -m qte run --config examples/trend-research-run.json --output build/trend-reference-20260921
time .venv/bin/python -m qte lab --config examples/strategy-lab.json --output build/strategy-lab-20260922
git diff --check
```

The editable install rebuilt only this project's package with existing local
tools; it did not download dependencies. Final recovered suite: **419 Python
tests passed** (9.16 seconds), including **109 offline broker tests**; **15/15
C++ test executables passed**. `git diff --check` passes. No tests were weakened
or removed. Expanded slate assertions explicitly cover the new router.

The lab declares nine scenarios on two synthetic clocks, 512 bars per window,
separate train/validation/test seeds, 12 standalone gated/ungated variants,
one macro router and two benchmarks. It attempts 540 train/validation cost runs
per clock; final holdout remains unused when synthetic evidence prevents selection.

The final run completed in **1m56.195s**: **1,080 scenario runs, zero failures**,
13,798 recorded fills including 1,168 in router runs. Both clocks retain all 15
slate entries, select nobody and leave the final holdout unused. All **3,308
artifact checksums** and the complete artifact inventory were verified; both
reports' source identities match the current package/native fingerprint:

`sha256:4c33fe48c153e322a6d47746bdf845e17f3d45694228e5a66fd0738f61b80a03`

These counts demonstrate execution and artifact integrity, not economic merit.
Full results: `build/strategy-lab-20260922/summary.json`, each clock's `report.json`
and `comparison.csv`, with raw fills/orders/equity under `runs/`.

## Inspection findings and remaining limits

- Fixed an existing comparison omission: mismatched valuation currencies now
  fail explicitly, supported by read-only dataset metadata and a regression.
- Reviewed event phases, portfolio ownership, Python callback lifetime copying
  and representative guarded C++ container checks. No new blocking core defect
  was found in this targeted review; this is not a complete correctness audit.
- Profiling identified redundant contraction ATR ranking. Short-circuiting it
  when the prerequisite breakout is false reduced the measured 512-bar fixture
  from 2.626 to 0.354 seconds under cProfile (~7.4x), with identical six fills and
  full equity. Seeded-prefix, quantile-boundary and engine equivalence tests were
  added. This is a workload-specific measurement, not a general speed guarantee.
- The core still scans historical terminal orders at each opening event. R6
  records benchmarking/active-order iteration before large high-turnover runs.
- Broker review found and fixed nonfinite JSON exponent acceptance, permissive
  header names, malformed contract templates and coerced required flags.
  Extreme oversized integer clock/query inputs may raise `OverflowError`; they
  fail before transport. No authenticated broker validation occurred.
- Synthetic macro values intentionally relate to generated regimes. They cannot
  demonstrate predictive value. No live prices, observed dealer inventories,
  economic significance, walk-forward optimization or Monte Carlo claim is made.
- 24-hour synthetic bars are not US exchange sessions. Corporate actions,
  point-in-time macro normalization and licensed dealer data remain prerequisites
  for credible long-horizon real ETF research. Liquidity is unlimited in the
  current execution model, including zero-volume stress bars.

## Next action requiring user input or approval

Provide a permitted real-data source and scope, and an official Schwab OpenAPI
export if Schwab verification is wanted. Keep credentials local, never in chat.
Robinhood requires an approved external Ed25519 signer; installing a missing
signing package needs approval. Account reads require deliberate user direction;
broker order submission and live/paper strategy integration remain separate work.
No system package, compiler, new dependency, account access, trade, sudo, or Git
history/remote operation was performed for this milestone.
