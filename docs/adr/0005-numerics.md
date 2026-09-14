# ADR 0005: Numeric types and reproducibility limits

Status: recommended for initial profile. No change to canonical Bar schema 1.

## Decision

- Timestamp remains `sys_time<nanoseconds>` with UTC interpretation. Reject
  conversion overflow, invalid intervals and unit ambiguity before constructing
  events. Use checked duration operations; never infer source units by magnitude.
- Internal order/fill/position quantities are signed int64 share counts wrapped
  in domain types. Request/fill magnitudes are positive. Forbid INT64_MIN and
  check addition/product bounds before mutation. Fractional order quantities
  fail; Bar's fractional double volume remains permitted.
- Prices, cash, average costs, fees, PnL and analytics use finite IEEE binary64
  doubles initially, with named domain wrappers at financial API boundaries.
  Require `is_iec559` and expected binary64 precision in numeric tests. Do not
  serialize C++ object memory as a public format.
- Instrument tick is a finite positive double. Validate order price alignment by
  converting price/tick to nearest integer tick count, with tolerance
  `1e-8 * tick` in price units; store accepted order price as that canonical tick
  price. This tolerates representation error, not arbitrary off-tick requests.
  Reject tick counts exceeding 2^53-1, since binary64 cannot distinguish all
  larger integers. Conversion is explicit and tested.
- Round synthesized execution prices adversely: buy ceil(price/tick), sell floor.
  Before ceil/floor, snap within the alignment tolerance to the nearest integer
  tick to avoid charging an extra tick for multiplication noise. Check the same
  tick-count bounds; reject invalid intermediate/final numbers.
- Do not quantize source OHLC, marks, average costs or cash to tick/cent values.
  Fees accrue at full binary64 precision; formatting rounds for display only.
  Trigger/limit comparisons use raw observations and canonical order prices
  with exact <=/>= comparisons, never a tolerance that changes a signal.

Accounting tests use `abs(a-b) <= 1e-9 + 1e-12*max(abs(a),abs(b))` in currency
units; quantity and ID tests require exact equality. This test tolerance must
not be used to forgive negative cash, overfills, or invalid risk decisions.
Runtime calculations reject non-finite/overflow outcomes. Zero quantity is
recognized exactly; no epsilon inventory cleanup.

Use deterministic iteration order and compensated summation for long-running
cash/fee/realized accumulations and aggregate exposure/equity. Avoid fast-math
and concurrency-dependent reductions. Exact repeatability is promised for a
fixed build/platform, data, config and strategy; other builds/platforms require
tolerance-based verification. Record compiler/build flags and numeric policy
version in results.

## Tradeoff and acceptance

Binary64 interoperates with existing Bar, indicators and Python without decimal
dependencies. It is not exact decimal money. Integer scaled-money accounting
would need currency scales, checked wide products, rounding policies and rational
average-cost handling. Revisit before brokerage reconciliation; no ABI guarantee
is made for internal wrappers.

Test 0.1-style values, tick neighbors, adverse rounding, fractional-share
rejection, quantity overflow, non-finite fees/prices, large-scale cancellation,
and repeated accumulation. Verify a tiny ledger imbalance is visible in tests
above the stated tolerance. Changing representation requires replaying all
hand-calculated accounting and execution fixtures.
