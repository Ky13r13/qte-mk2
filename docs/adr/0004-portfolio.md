# ADR 0004: One ledger and average-cost inventory

Status: M4a per-symbol positions, M4b aggregate cash/position ledger, and M4c
trade episodes implemented. Depends on [fill](0001-orders-and-fills.md) and
[numeric](0005-numerics.md) contracts.

## Decision and equations

Portfolio alone applies committed fills. First profile uses one currency,
multiplier 1, immediate cash settlement and signed integer-share quantities.
Average cost describes inventory; it is not a tax-lot accounting claim.

For current signed quantity Q, average entry A, signed fill delta d (buy positive,
sell negative), execution price P and commission F:

- `Q_new = Q + d`; `cash_new = cash - d * P - F`.
- If Q is zero, `A_new = P`. If d has Q's sign, weighted average is
  `(abs(Q)*A + abs(d)*P) / abs(Q_new)`; no realized trading PnL.
- If d opposes Q, closing quantity C = min(abs(Q), abs(d)); realized increment
  `C * (P - A) * sign(Q)`. On a reduction, retain A. On flat, set A_new = 0.
  On reversal, residual inventory gets A_new = P; only C realizes old inventory.
- For mark M, `market_value = Q * M`, `unrealized = Q * (M - A)`.
- `equity = cash + sum(market_value)`.
- Maintain realized **gross** trading PnL and commissions separately;
  `net_pnl = realized_gross + unrealized - commissions` and
  `equity - initial_cash = net_pnl` in the absence of external flows/actions.

Fees reduce cash immediately and never modify A. Spread/slippage already enter P.
Marks do not realize PnL or change cash/quantity. Negative short market value
offsets sale proceeds. Gross exposure = sum(abs(market_value)); net exposure =
sum(market_value). Ratios to equity are undefined when equity <= 0, not zero.
Flat positions retain cumulative realized PnL/fees but zero A and unrealized PnL.

M4a represents an open position without a valuation mark explicitly: market
value, unrealized PnL, and net PnL are unavailable until marked, while those
values are known zero for a flat position even without a mark. Marks carry UTC
timestamp and global event sequence, permit a later sequence at the same
timestamp, and reject backward timestamps or non-increasing sequences. Applying
a fill never fabricates a mark from its synthetic execution price. Per-symbol
realized gross PnL and commissions use compensated accumulation; fees do not
alter average cost. The position validates its complete prospective state before
mutation and assumes M4b supplies each committed fill exactly once.

M4b fixes a non-empty instrument universe and one valuation currency when the
portfolio is constructed. Initial cash is finite and non-negative; subsequent
cash may be negative because risk enforcement is a separate layer. The ledger
stores committed fills, requires contiguous fill IDs starting at 1, and requires
globally increasing event sequences with nondecreasing timestamps across marks
and fills. It independently rejects unknown symbols, currency/profile mismatch,
and executed prices outside instrument tick grids. Duplicate IDs are identified
before ordinary event validation.

Each fill stages a copied affected position, compensated cash changes, and all
aggregate invariants before appending history or mutating live state. Cash uses
sale proceeds as positive and purchase notionals as negative, with commission
subtracted once for either side. Aggregate market value, unrealized PnL, equity,
and exposure are unavailable while any non-flat position lacks a mark; becoming
flat restores a known zero valuation without fabricating a market price. Whenever
valuation is available, the ledger checks `equity - initial_cash = realized
gross + unrealized - commissions` within the numeric-policy tolerance.

All arithmetic, IDs and references are validated before commit; exceptions cannot
partially mutate the ledger. Portfolio independently rejects invalid/duplicate
fills, regardless of engine-side checks. Ledger identifiers persist for the run.

## Trades and fees

Fills and trades are different outputs. A trade is a per-symbol flat-to-flat
position episode. Scaling in/out remains one episode. A reversal closes the old
episode and opens a new one at the same fill price. Split that fill's fee by
closing/opening quantity; compute the closing share proportionally and assign
the floating-point remainder to the new episode so allocations sum to F.

Episode net PnL = its realized gross PnL minus all allocated entry/exit fees.
Keep entry fees on unfinished episodes; exclude those episodes from closed-trade
win rate/profit factor but include their fees in portfolio net PnL immediately.
Breakeven episodes count as trades but neither winning nor losing trades. Trades
are derived once from ledger events in the portfolio reporting component; do not
implement a second cost-basis engine in Python/analytics.

M4c exposes position-transition facts (prior/new quantity, opened/closed
quantity, and realized gross PnL) from the authoritative average-cost update.
The portfolio stages the matching trade update in the same transaction rather
than recomputing cost basis. Open episodes remain separate from the globally
closing-fill-ordered closed history and have no outcome. Scaling and partial
reductions stay within one episode; exact zero net PnL is breakeven. A reversal
fill closes the old episode and opens the opposite episode: its closing fee is
proportional to closed quantity and the subtraction remainder goes to the new
episode, conserving the original fee exactly. Compensated episode totals limit
floating-point drift, and a rejected fill cannot mutate trade state.

## Hand-calculated acceptance fixtures

Each row applies to the prior row with initial cash 1,000; mark at fill price.

| Fill | Cash | Q | A | Cumulative realized gross | Fees | Equity |
|---|---:|---:|---:|---:|---:|---:|
| Buy 10 @ 10, fee 1 | 899 | 10 | 10 | 0 | 1 | 999 |
| Buy 10 @ 12, fee 1 | 778 | 20 | 11 | 0 | 2 | 1,018 |
| Sell 5 @ 14, fee 1 | 847 | 15 | 11 | 15 | 3 | 1,057 |
| Sell 20 @ 9, fee 2 | 1,025 | -5 | 9 | -15 | 5 | 980 |
| Buy 8 @ 8, fee 1 | 960 | 3 | 8 | -10 | 6 | 984 |
| Sell 3 @ 10, fee 1 | 989 | 0 | 0 | -4 | 7 | 989 |

Also test an independent short-first round trip, marks without fills, partial
fill fee accumulation, empty portfolio, duplicate fill rejection without any
mutation, unchanged cost on reduction and reversal fee allocation. Generated
deterministic fill sequences must satisfy quantity/cash conservation and the
equity identity within the numeric tolerance. Every accounting defect adds a
regression fixture. These ledger fixtures bypass risk intentionally.

## Consequences

Average cost is compact and handles long/short reversals with one formula. It
does not model FIFO tax lots, external deposits, corporate actions, futures
variation margin or broker settlement. Add those as explicit ledger events and
new invariants before accepting their datasets/instruments.
