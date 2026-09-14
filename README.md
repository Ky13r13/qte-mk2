# qte

`qte` is a modular quantitative-trading research and backtesting engine. The
performance-sensitive engine is written in C++20; Python will provide the
research, configuration, analysis, and strategy-facing API through pybind11.

The project is being built in small, independently tested increments. Implemented
foundations include the canonical OHLCV bar and validation boundary, checked
whole-share quantities, and deterministic strongly typed identifiers.

The [architecture decisions](docs/architecture.md) define the recommended next
contracts. The [development roadmap](docs/roadmap.md) breaks implementation into
small milestones with explicit acceptance tests. These documents distinguish
planned behavior from the currently implemented foundation.

## Canonical bar contract

- Times are UTC instants with nanosecond precision; adapters must resolve source
  timezones before constructing a canonical bar.
- A completed bar covers `[start_time, end_time)` and, for historical backtests,
  becomes available to a strategy at `end_time`. The future event/feed layer is
  responsible for enforcing that boundary.
- Symbols are non-empty, provider-normalized instrument identifiers. The first
  engine slice assumes instruments priced in one configured valuation currency
  and does not perform foreign-exchange conversion.
- OHLC prices are finite, non-negative `double` values in the instrument's quote
  currency. Zero is accepted at the data-model boundary; an adapter or universe
  policy may reject it for a particular instrument class.
- Volume is a finite, non-negative `double`; fractional quantities are permitted.
  Its instrument-specific unit must be recorded with the future dataset metadata.
- Provider adapters must validate and normalize data before it enters the feed.
- Provider field names, raw payloads, authentication details, and vendor-specific
  schemas remain outside the core.
- Price adjustment and corporate-action handling are not yet implemented. A
  future dataset contract must declare its adjustment mode explicitly.

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
