# G3b read-only result bindings

This reference records the Python values available from a `BacktestResults`
value returned by `qte.BacktestEngine.run`. It is a consumer reference for later
export and GUI work, not an export schema and not a construction API.
`BacktestResults` has no public Python result constructor or public `qte`
facade name. Its result collections are returned as owned Python values, so a
collected parent result does not invalidate a previously obtained item or
collection.

These values describe one completed engine result. They do not authorize order
submission, portfolio mutation, provider access, or accounting recomputation.

## Exactness and absence

Python receives timestamp, ID, sequence, and quantity fields as Python `int`.
They retain their C++ signed-64 or unsigned-64 values exactly; callers must not
coerce them through floating point. A future web boundary must serialize these
fields as decimal strings, rather than JSON numbers. This binding reference does
not define that future boundary.

`None` is meaningful absence, not zero and not a value to infer. In particular,
optional limit/stop prices, terminal order reasons, order-event IDs, position
marks, trade closing facts, and a trade outcome may be absent.

## Collections on `BacktestResults`

- `equity_curve`: `EquityPoint` values.
- `orders`: final `OrderSnapshot` values.
- `order_events`: ordered `OrderEvent` history.
- `fills`: committed `Fill` values.
- `trades`: closed `TradeEpisode` values.
- `open_trades`: still-open `TradeEpisode` values.
- `positions`: final `PositionSnapshot` values.
- `manifest`: a copied `RunManifest`.

The engine owns all of these completed-result values. The returned Python lists
are ordinary editable Python containers, but editing a returned list never
mutates the native completed result, portfolio, or strategy; nor are they live
views of those objects.

## Value fields

| Value | Read-only Python properties |
|---|---|
| `EquityPoint` | `timestamp_ns`, `sequence`, `equity`, `gross_exposure` |
| `Fill` | `id`, `order_id`, `symbol`, `side`, `quantity`, `effective_ns`, `effective_sequence`, `reference_open`, `executed_price`, `gross_notional`, `commission` |
| `OrderSnapshot` | `id`, `symbol`, `side`, `quantity`, `type`, `limit_price`, `stop_price`, `time_in_force`, `submitted_ns`, `submission_sequence`, `eligible_after_sequence`, `status`, `filled_quantity`, `remaining_quantity`, `stop_triggered`, `rejection_reason`, `cancellation_reason`, `detail` |
| `OrderEvent` | `timestamp_ns`, `sequence`, `order_id`, `kind`, `detail` |
| `TradeEpisode` | `symbol`, `direction`, `opening_fill_id`, `opened_ns`, `opening_sequence`, `closing_fill_id`, `closed_ns`, `closing_sequence`, `opened_quantity`, `closed_quantity`, `remaining_quantity`, `realized_gross_pnl`, `allocated_commissions`, `net_realized_pnl`, `is_closed`, `outcome` |
| `PositionSnapshot` | `symbol`, `quantity`, `mark_price`, `mark_ns`, `mark_sequence` |

Enums remain their bound enum values, including order side/type/status and
reasons, order-event kind, position direction, and trade outcome. Optional
scalars and enums are `None` when absent.

## `OrderRequest` facts held by order snapshots

An `OrderSnapshot` exposes its owned request facts through the properties in the
table above. The same read-only `OrderRequest` properties are available on an
order request made by the existing `qte.market_order`, `qte.limit_order`,
`qte.stop_order`, or `qte.stop_limit_order` factories: `symbol`, `side`,
`quantity`, `type`, `limit_price`, `stop_price`, and `time_in_force`.
Optional prices are `None` when that order type has no corresponding price.
This does not make result orders mutable or provide a way to alter a completed
run.

## Sequences, event time, and sampled time

An event equity point has an engine timestamp and its engine event `sequence`.
The pair preserves deterministic engine ordering; different event points may
share a timestamp while retaining distinct sequences.

`qte.sample_equity` is a display/analytics sampling operation. It chooses the
latest source event at or before each permitted sample time, copies that event's
sequence, then assigns the requested sample timestamp to the returned point.
Thus a sampled point's `timestamp_ns` is grid time, while its `sequence` belongs
to the source event used for the sample. It is not a newly generated sample
event sequence. Sampling requires increasing source sequences and a strictly
increasing sample grid; it does not create fills, trades, or order events.

`Fill.effective_sequence`, order submission/eligibility sequences, and
`OrderEvent.sequence` are their recorded engine facts. `PositionSnapshot`
contains only an optional final valuation `mark_sequence`, not a full position
event history.

## What the values do not mean

A `TradeEpisode` is a flat-to-flat position episode, not a fill. It may contain
multiple fills and aggregates the episode's opened/closed quantities, realized
gross PnL, allocated commissions, and net realized PnL. An open episode has no
closing fill/time/sequence and no outcome; its realized values are activity to
date, not an inferred mark-to-market result.

`PositionSnapshot` is final-state inventory only: symbol, signed quantity, and
an optional final mark price/time/sequence. It does **not** own average entry
price, position PnL, final cash, or a portfolio-wide valuation. Consumers must
not reconstruct those facts from this snapshot.

Likewise, an `OrderSnapshot` is the final recorded order state. An `OrderEvent`
is history of lifecycle facts and may have no order ID for an event type that
does not identify one. Neither collection should be used to invent missing
events, sequence values, or accounting outcomes.
