# qte

**Start here: [START-HERE.md](START-HERE.md)** (one page: goal, status, five commands). Detail: [docs/README.md](docs/README.md) has the current capability map, the
path-to-live status and which documents are current or historical.
[CONTINUITY.md](CONTINUITY.md) holds the latest session handoff.

**Weekly data routine** (Schwab drops 1-minute history after about 48 days):

```sh
cd ~/Documents/qte
.venv/bin/python -m qte.brokers.schwab_auth refresh   # prompts privately for the Schwab app key and secret
.venv/bin/qte truth collect                           # within ~30 minutes: download every interval into data/truth
.venv/bin/qte truth derive                            # rebuild 1h/4h bars from the new 5-minute data
```

Core research loop (Nov 2026):

```sh
.venv/bin/qte truth coverage                                   # what data the truth database holds
.venv/bin/qte search describe --dataset SPY:5m:alpaca          # locked dev/validation/holdout split
.venv/bin/qte search sweep --dataset SPY:5m:alpaca --costs spy_as_es --spec '{...}' --grid '{...}'
.venv/bin/qte search leaderboard --dataset SPY:5m:alpaca --costs spy_as_es
```

Start with the [test build guide](docs/test-build-guide.md) for setup and the [development audit](docs/development-audit.md) for verified workflows, local evidence and outstanding integration work.

Open **Research → Daily brief** for the scheduled post-close news, regime rationale and market data. [Daily agent workflow](docs/daily-brief-agent.md)

Run the [S1 VWAP reversion and S2 opening gap fade studies](docs/strategies/core-reversion.md) with `qte s1-study` or `qte s2-study` in the project venv. Both screen SPX minutes and replay synchronized SPXW debit spreads.

Replay the [S7 0DTE trend-day strategy](docs/strategies/s7-trend-day-convexity.md) with `.venv/bin/qte s7-study --demo --output build/my-s7-demo`. The report shows each simulated option entry, scale-out and exit.

The [strategy lab](docs/strategies/strategy-lab.md) adds six documented long-only
hypotheses, causal macro/volatility filtering, explicit dealer-data boundaries,
and all-candidate cost/regime comparisons:

```sh
.venv/bin/python -m qte lab --config examples/strategy-lab.json --output build/my-strategy-lab
```

This command uses **synthetic data only**. It tests software behavior, does not
establish profitability, and cannot promote a strategy for trading.
The [broker adapters](docs/broker-connections.md) support captured data and
explicit market-data reads. Schwab OAuth and selected read-only market-data
routes have been exercised. Broker tickets can be staged locally for review.

Launch the local GUI with `.venv/bin/python -m qte gui` from the checkout.
Open the printed loopback URL and enter the terminal code. The Library has
the architecture and research guides. Research can preview and launch fixed
jobs, register results and display recorded candles, fills and equity.
[GUI guide](docs/gui-usage.md) · [Windows capabilities](docs/windows-gui.md)

Run a complete synthetic research example from the installed venv package:

```sh
.venv/bin/python -m qte run --config examples/research-run.json --output build/my-research-run
```

This writes a report, fills/orders, event and sampled equity, and a reproducibility
manifest. Output directories cannot be overwritten. See the
[research workflow](docs/research-workflow.md) for configuration, authenticated
Alpaca historical downloads, cache behavior, and benchmarks. The new
[paper coordinator](docs/adr/0007-paper-coordination.md) is tested offline;
account/strategy connectivity is a later integration step.

`qte` is a modular quantitative-trading research and backtesting engine. The
performance-sensitive engine is written in C++20; Python provides the
research, configuration, analysis, and strategy-facing API through pybind11.

The project is being built in small, independently tested increments. Implemented
foundations include the canonical OHLCV bar and validation boundary, checked
whole-share quantities, deterministic strongly typed identifiers, canonical
currency codes, tick-price policy, and the initial single-currency instrument
profile. The order domain now validates market, limit, stop, and stop-limit
requests and enforces explicit lifecycle transitions without assuming execution.
It also provides immutable fill facts and a sequential fill journal that rejects
metadata mismatches, ineligible or terminal fills, overfills, and replayed IDs
without partial state mutation. The initial `open_only_v1` execution component
evaluates market and limit orders using only an observed open, applies explicit
spread, slippage, tick rounding, and commission assumptions, and emits a
fill candidate without portfolio mutation. Stops trigger inclusively from an
eligible open, remain triggered across later opens, and stop-limits still enforce
their final cost-adjusted limit. Per-symbol positions now implement average-cost
long and short inventory, reductions, closures, reversals, realized and
unrealized PnL, commissions, and ordered valuation marks. The portfolio ledger
now atomically coordinates positions with compensated cash, validates a fixed
single-currency universe, protects against duplicate/out-of-order fills, and
calculates aggregate equity and exposure without inventing missing marks.
Flat-to-flat trade episodes preserve scaling and partial reductions, classify
closed net outcomes, exclude unfinished episodes from closed results, and split
reversal fees exactly between closing and newly opened episodes. The standalone
risk layer now evaluates immutable portfolio and pending-order snapshots at
submission and fill time, enforcing order size, cash, long-only, symbol
allocation, and gross-leverage policies without mutating accounting state. C++
strategies now have explicit start/bar/fill/end callbacks, callback-scoped
read-only snapshots, and deterministically buffered submit/cancel intents whose
receipts remain pending until engine command processing.
Validated datasets now declare their fixed interval, currency, adjustment and
corporate-action assumptions, reject malformed or non-tradable streams, record
gaps without fabricating observations, and produce a canonical content hash.
The deterministic scheduler batches completed bars by timestamp, publishes the
whole batch before callbacks, and exposes only bounded completed history plus
open-only execution observations. The backtest engine integrates strategy,
risk, execution, and portfolio state for one or many symbols; it returns equity,
order/fill audits, closed and unfinished trade episodes, final marked positions,
and a manifest containing normalized configuration and input/build identity.
The analytics layer derives returns, total return, drawdown, closed-trade
statistics, turnover, and elapsed-time-weighted exposure without influencing
replay. Annualized metrics require an explicit periods-per-year convention and
regular returns or an explicitly supplied session sampling grid. Undefined cases
retain a reason instead of producing an arbitrary zero. The strategy lab reports
nonannualized metrics only.
The optional pybind11 package exposes typed datasets, configuration, results,
analytics, and synchronous Python strategy callbacks while the C++ core retains
engine ownership. Python timestamps cross the boundary as exact integer
nanoseconds, returned snapshots are owned values, and callback contexts expire
immediately after each callback.
Provider ingestion remains outside the core. The first adapter reads complete
saved Alpaca historical stock-bar responses with explicit raw/action-free
assumptions and hashed provenance; a separately mapped RFC3339-UTC CSV adapter
provides a portable path. Neither performs network access. The reference
SMA-regime strategy is an integration and research-integrity example: it labels
in-sample and out-of-sample results, compares baseline and stressed costs, and
does not optimize parameters or claim profitability.

The [architecture decisions](docs/architecture.md) define the recommended next
contracts. The [development roadmap](docs/roadmap.md) breaks implementation into
small milestones with explicit acceptance tests. These documents distinguish
planned behavior from the currently implemented foundation.

## Canonical bar contract

- Times are UTC instants with nanosecond precision; adapters must resolve source
  timezones before constructing a canonical bar.
- A completed bar covers `[start_time, end_time)`. Legacy datasets publish at
  `end_time`; the opt-in [explicit-availability profile](docs/adr/0012-explicit-availability.md)
  publishes no earlier than end, without changing Bar fields. Strategy
  `ctx.now_ns` is callback time, not necessarily observation end.
- Symbols are non-empty, provider-normalized instrument identifiers. The first
  engine slice assumes instruments priced in one configured valuation currency
  and does not perform foreign-exchange conversion.
- OHLC prices are finite, non-negative `double` values in the instrument's quote
  currency. Zero is accepted at the data-model boundary; an adapter or universe
  policy may reject it for a particular instrument class.
- Volume is a finite, non-negative `double`; fractional quantities are permitted.
  Its unit is recorded in dataset metadata.
- Provider adapters must validate and normalize data before it enters the feed.
- Provider field names, raw payloads, authentication details, and vendor-specific
  schemas remain outside the core.
- Price adjustment and corporate-action handling are not implemented. Dataset
  metadata must explicitly declare the initially supported unadjusted,
  action-free contract; unsupported or unknown declarations fail preflight.

## Build

The following setup commands target Linux. Native Windows GUI support is being
ported incrementally: Library and offline Connections first, not research/job
parity. See [Windows setup and acceptance](docs/windows-gui.md); use a native
Windows venv/build, never copied Linux binaries.

```sh
./scripts/bootstrap.sh
source .venv/bin/activate
cmake --preset dev
cmake --build --preset dev
ctest --preset dev
```

All Python packages and Python-provided developer tools are installed in the
project-local `.venv`. A native C++20 compiler must be available on `PATH` or
selected through the standard `CXX` environment variable/CMake option.

After the matching system Python development headers are present, build and
test the optional Python package entirely inside the existing venv:

```sh
PIP_NO_CACHE_DIR=1 .venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest tests/python
```

`qte.adapters.load_alpaca_fixture` and `load_csv_bars` consume local files only.
See [the adapter contract](docs/adapters.md) and the formal
[moving-average reference specification](docs/strategies/moving-average-reference.md).

The Linux GUI includes a Live desk for Schwab SPX/SPXW observation, paper execution quality, position scenarios and session cutoffs. Run `.venv/bin/python -m qte live-demo` from the checkout to generate the offline example. [Live desk guide](docs/live-desk.md)
