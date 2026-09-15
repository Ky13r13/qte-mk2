# QTE architecture and decision index

## Status and baseline

These are recommended implementation decisions for the first research release,
not claims of implemented behavior. They are the defaults for the roadmap; a
change to them requires a focused decision update and tests, not a rewrite.

The repository currently contains `qte_core`, canonical `Bar`/`Symbol`/
`Timestamp`, validation, checked share quantities, strongly typed sequential
IDs, currency and tick-price policies, initial instrument/profile metadata, and
validated order requests/state transitions. Immutable fill facts, fill/order
consistency validation, cumulative quantity updates, and a sequential replay-
protected fill journal are implemented. The `open_only_v1` execution model
now evaluates market and limit orders from a `MarketOpen` value, applies explicit
spread/slippage/commission assumptions, and returns non-mutating fill candidates.
It now supports persistent open-triggered stop and stop-limit behavior on the
engine-owned order record; market and limit evaluation remains non-mutating.
Per-symbol average-cost positions now handle long/short scaling, reductions,
closures, reversals, cumulative gross realized PnL and commissions, and ordered
valuation marks. The fixed-universe, single-currency portfolio ledger atomically
applies fills to compensated cash and position state, independently rejects
duplicate/out-of-order fills, and exposes aggregate equity and exposure only
when all open positions are marked. Flat-to-flat trade episodes are derived from
position transition facts during the same staged ledger transaction; scaling
stays in one episode and reversal fees are conserved across the closing and new
episodes. Nine CTest executables cover the foundation. Feeds, risk, engine,
Python package, and bindings remain unimplemented. Preserve the existing Bar
layout and schema version 1. The dev preset uses `build/local-dev`, native C++20,
and tools in `.venv`. Git inspection currently works; history-changing Git
operations still require explicit user direction.

## First supported research profile

- Synchronous, single-threaded historical simulation; one strategy per run,
  multiple symbols, one configured fixed-duration bar interval per run.
- Cash equities/ETFs with multiplier 1, integer shares, configured positive price
  tick, one quote/valuation currency (default USD). Instrument metadata maps the
  existing normalized Symbol to these properties; no provider fields in Bar.
- Bar's existing finite, non-negative doubles and fractional volume remain valid.
  Tradable datasets require strictly positive OHLC; zero volume is valid. This
  additional eligibility check belongs to dataset preflight, not Bar validation.
- Use unadjusted bars and a declared action-free interval initially. Unknown
  adjustment/action coverage fails preflight. Corporate actions, FX, borrow costs,
  interest, settlement delays, margin calls, live revisions and exchange calendars
  are unsupported. User declarations are recorded assumptions, not proof.
- Long-only by default; opt-in shorts use the accounting below, with an explicit
  assumption of borrow availability. Gross exposure is limited to equity by
  default. Results from this profile are research simulations, not paper trading.

## Modules and ownership

| Module | Owns / consumes | Does not own |
|---|---|---|
| `market_data` | Existing types/validation, instrument and dataset contracts; later ordered feed | Vendor parsing, strategy state |
| `orders` | Requests, identifiers, transition rules, immutable fills | Prices, portfolio balance |
| `execution` | Open-price rules, triggers, costs, optional liquidity budget; emits fill proposals | Cash or position mutation |
| `portfolio` | Fill ledger, cash, positions, marks, realized and unrealized PnL | Signals, order matching |
| `risk` | Checks snapshots and pending exposure; approval/rejection reasons | Accounting or strategy logic |
| `strategy` | Lifecycle, intent submission, read-only context contract | Broker or mutable ledger access |
| `engine` | Clock, order registry, sequence allocation, commit coordination, results | Duplicated accounting, strategy-specific logic |
| `analytics` | Derived metrics from immutable results | Execution decisions |
| Python package | Bindings, config, research, plotting, adapters | An alternative accounting implementation |

Use one C++ library initially with separate headers/sources as components land.
Value types flow between modules; callbacks cross a Strategy interface. Only add
an execution/feed base interface when its second implementation arrives. Do not
introduce an event bus, dependency-injection framework, or broker SDK dependency.

## Decisions

1. [Orders and fills](adr/0001-orders-and-fills.md)
2. [Time and deterministic ordering](adr/0002-time-and-events.md)
3. [Execution and risk](adr/0003-execution.md)
4. [Portfolio invariants](adr/0004-portfolio.md)
5. [Numeric representation](adr/0005-numerics.md)
6. [C++ and Python boundary](adr/0006-python-boundary.md)

The [roadmap](roadmap.md) maps these contracts to independently testable changes.

## Choices requiring later evidence or a user preference

| Choice | Recommended default now | Revisit / required decision |
|---|---|---|
| First provider | None selected; synthetic fixtures until adapters | Choose actual provider and access before network work; no installation assumed |
| Initial instruments / currency | Cash equities, integer shares, USD, metadata-supplied tick | Confirm intended research universe before obtaining data |
| Intrabar execution | Open-only sampling, explicitly named in results | Select a path/ambiguity policy before adding OHLC-touch execution |
| Partial fills | Disabled; unlimited-liquidity assumption recorded | Enable shared volume cap explicitly; see execution ADR |
| Costs | Zero commission/spread/slippage for unit fixtures; emit these values in every result | Research configurations must supply and vary estimates; no realism claim for zero costs |
| Price adjustment / actions | Unadjusted and declared action-free windows | Design split/dividend events before action-bearing backtests |
| Instrument IDs | Normalized non-empty Symbol, reject whitespace/control characters at preflight | Stable security IDs before ticker changes/multi-venue datasets |
| Short selling | Off | Opt in only with explicit borrow/cost assumptions; brokerage fidelity is later work |
| Metrics annualization | No implicit periods-per-year | Require interval/calendar assumptions before annualized output |
| Python API | Snapshot values and callback-scoped context | Optimize views only after measuring copies |
| Reproducibility across machines | Same pinned build/input: exact logical replay | Cross-platform floating-point equivalence is tolerance-based, not promised bitwise |
| Git metadata | Preserve visible directory | User-approved repair or workspace integration before versioned development |

None of the deferred choices blocks the next orders/fills milestone. Unsupported
features fail explicitly; adding a configuration key is not an implementation.
