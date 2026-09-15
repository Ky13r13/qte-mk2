# ADR 0001: Orders and immutable fills

Status: request/order state implemented in M2a; immutable fill facts, consistency
validation, cumulative updates, and fill-ID replay protection implemented in
M2b. Portfolio-level atomic commit and transition audit logging remain future
integration work. Depends on [numerics](0005-numerics.md).

## Decision

Separate user intent (`OrderRequest`) from engine-owned state (`OrderRecord`).
Requests contain Symbol, BUY/SELL, positive integer-share quantity, order type,
and optional limit/stop. MARKET requires neither price, LIMIT only limit, STOP
only stop, STOP_LIMIT both. Reject extraneous price fields. Required prices are
finite, positive and on the instrument tick. No constraint on stop versus limit
relative value: adverse gaps may legitimately leave a triggered order unfilled.

Initial time-in-force is GTC only. Unsupported TIFs fail; DAY expiry requires a
session calendar later. No amendments, OCO, brackets, trailing stops or implicit
position-closing semantics. Cancel-and-resubmit uses a new ID and new priority.
BUY/SELL describes signed inventory change; selling can reduce or open a short
only if risk permits. A close helper snapshots current quantity and submits the
opposite order, without promising a fill or canceling other orders implicitly.

The engine assigns monotonically increasing uint64 order IDs starting at 1 per
run (overflow aborts). Records include submission timestamp and event sequence,
eligible-after sequence, cumulative fill quantity, remaining quantity, status,
rejection/cancellation reason, and a persistent stop-trigger flag. A timestamp
does not replace event sequence for deciding eligibility.

| Current state | Event | Next state |
|---|---|---|
| NEW | Validated and risk accepted | OPEN |
| NEW | Invalid request / risk rejection | REJECTED |
| OPEN | Positive fill smaller than remaining | PARTIALLY_FILLED |
| OPEN or PARTIALLY_FILLED | Fill equals remaining | FILLED |
| PARTIALLY_FILLED | Another partial fill | PARTIALLY_FILLED |
| OPEN or PARTIALLY_FILLED | Effective cancel | CANCELED |
| Any terminal state | Fill / amendment | Error; no mutation |

NEW is transient within command processing. A stop triggering changes its flag,
not status. Before any fill, an execution-time risk failure cancels an accepted
order with `execution_risk`; NEW rejection is distinct. After a partial fill,
cancel the remainder and preserve the executed quantity/history. End-of-data
cancels all outstanding orders with `end_of_data`; do not liquidate positions.
Duplicate cancel commands return an explicit no-op result; unknown IDs return
an error result. Neither creates a new status transition.

`Fill` is immutable after commit: fill ID, order ID, Symbol, side, positive
quantity, effective timestamp/sequence, reference open, executed price, gross
notional and commission. Spread/slippage are embedded in executed price and may
also be disclosed as attribution fields; never charge them twice. IDs are
sequential per run. Order transitions form a separate append-only audit log.

M2b's `FillJournal` is the order/fill commit boundary: it requires contiguous
fill IDs starting at 1, rejects duplicate and skipped IDs before mutation, then
validates identity, instrument grid, temporal eligibility, terminal state, and
remaining quantity. Equal timestamps are permitted only when the fill event
sequence is later than order eligibility. The raw reference open must be finite
and positive but is observational data and need not be tick-aligned; the
executed price must match the instrument grid. Gross notional is positive
`quantity * executed_price`, and commission is finite and non-negative.

The engine prepares a fill and the resulting order/portfolio state, validates
both, then commits them together before notifying strategies. Invalid fills or
allocation/validation failures cannot leave only one side updated. A fatal
internal error ends the run; it cannot return a successful backtest.

## Invariants and acceptance

- `filled + remaining == requested` exactly; both non-negative; fill > 0 and
  <= remaining. Status and quantities agree; cancellation may retain remainder.
- Fill order/symbol/side agree with the order. Fill IDs may never be applied
  twice. Duplicate/replayed fills are errors with unchanged state.
- No fill at/before its submission sequence or after terminal status.
- Table-driven tests cover every legal transition and invalid state/event pair,
  malformed requests, partial then cancel, duplicate fill, ID overflow, and
  repeated cancellation. Rejected requests still have an ID and audit reason.

## Tradeoff

Simple GTC and explicit state enable audit and unit tests without exchange
protocol details. Live acknowledgements, cancel-in-flight and broker IDs require
additional adapter/state decisions before paper trading; do not pretend local
synchronous cancellation reproduces a live venue.
