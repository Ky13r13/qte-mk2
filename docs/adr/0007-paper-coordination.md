# ADR 0007: Durable paper-order coordination

Status: M16 architecture and offline controller; no strategy/account connector.

The backtest engine remains a synchronous deterministic replay. Paper execution
requires a separate coordinator because network acknowledgements and fills may
arrive late, be duplicated, or follow a disconnect. A gateway protocol is the
second execution use case; it does not make the historical execution model an
exchange simulator.

## Ownership and contracts

- The gateway identifies a specific paper account and presents normalized broker
  snapshots, acknowledgements, cancellations, and execution reports. A real
  Alpaca gateway is a subsequent milestone. Live accounts are rejected.
- The coordinator writes intent to SQLite before external submission. Stable
  client IDs survive restarts. Exactly-once external delivery is not promised:
  ambiguous requests are reconciled by ID, never blindly retransmitted.
- SQLite uses FULL synchronous transactions. A durable armed lease prevents a
  second coordinator from submitting against the same journal. Operator takeover
  after an unclean stop requires explicit journal recovery, followed by full
  reconciliation. One process owns a coordinator; parallel strategy calls are
  unsupported.
- Broker order and execution facts are journaled separately from accounting.
  Execution IDs deduplicate incoming facts; conflicting replays fail closed.
  Cash, fees, average cost and PnL retain the existing C++ portfolio authority.
- The controller requires a risk-approval callback and a local-account snapshot
  callback. These are integration ports, not alternate accounting. Before live
  strategy routing, a durable C++ ledger bridge must apply each execution once
  and recover its application watermark transactionally.

## State and reconciliation

Startup and disconnect block submissions. Reconciliation requires a matching
paper account, exact normalized cash/position agreement, no unknown broker open
orders, and broker lookup of every outstanding durable client ID. Unknown or
conflicting states retain the block. A timeout leaves an uncertain durable
intent. Reconciliation may recover its broker ID and current state; absence is
not interpreted as rejection because broker visibility may lag acceptance.

Supported initial statuses are prepared, unknown, open, partially_filled,
filled, canceled and rejected. Filled quantities must be monotonic, nonnegative,
and bounded by requested quantity. Terminal states cannot regress. Partial fills
are broker facts only; historical replay still uses full fills.

## Controls

Submission requires completed reconciliation, an unexpired connection heartbeat,
an explicitly allowed symbol, positive whole shares, allowed side, finite positive
reference price, size/notional limits, and risk approval. Halting is persistent
across restart and never automatically liquidates. Explicit cancellation remains
available while halted if connected. Cancellation failure leaves uncertainty and
blocks further submissions. Clearing the halt requires a fresh reconciliation.

## Validation and remaining integration

Offline tests cover acknowledgement, crash/restart identity, submission timeout,
partial-fill progression, duplicate/conflicting execution facts, external-order
and ledger discrepancies, account mismatch, stale connection, kill controls and
cancel failures. No test or module places an actual brokerage order.

Required before account connection: implement authenticated paper-only gateway,
durable ledger/execution bridge, order-update delivery to strategies, market-data
recovery, calendar/latency policy, and supervised end-to-end paper validation.
