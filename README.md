# qte

`qte` is a modular quantitative-trading research and backtesting engine. The
performance-sensitive engine is written in C++20; Python will provide the
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
replay. Annualized return, volatility, Sharpe, Sortino, and Calmar require an
explicit periods-per-year value and an equally spaced positive timestamp grid;
undefined cases retain a reason instead of producing an arbitrary zero.
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
- A completed bar covers `[start_time, end_time)` and becomes available to a
  strategy at `end_time`. The deterministic scheduler enforces that boundary.
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
