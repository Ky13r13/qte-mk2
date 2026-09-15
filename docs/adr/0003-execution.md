# ADR 0003: Open-only execution and independent risk

Status: M3a market/limit matching and costs plus M3b stop/stop-limit behavior
implemented. Independent risk checks remain recommended for M5. Model ID:
`open_only_v1`.

## Decision

Evaluate accepted orders at subsequent eligible opens for their own symbol.
Let O be that observed open, L the limit and S the stop. Inclusive comparisons
use the raw observation without an epsilon.

| Type | Buy rule | Sell rule |
|---|---|---|
| MARKET | Candidate at O | Candidate at O |
| LIMIT | O <= L | O >= L |
| STOP | Trigger permanently if O >= S; then market | Trigger permanently if O <= S; then market |
| STOP_LIMIT | Trigger if O >= S; once triggered apply limit rule | Trigger if O <= S; once triggered apply limit rule |

A stop-limit can trigger and fill at the same eligible open if its limit also
permits it. Otherwise its trigger persists across future opens, including gaps.
High/low touches do not trigger or fill orders. This model can miss intrabar
trades and must be named prominently in research output; it is not an exchange
order-book simulation. A stop does not guarantee its stop price on a gap.

Costs: non-negative commission_bps, full quoted spread_bps and slippage_bps,
default all zero. No fixed/minimum fee initially. For a candidate, buy price is
`O * (1 + (spread_bps/2 + slippage_bps)/10000)`; sell uses minus. Round buys up
and sells down to the instrument tick. Non-positive/non-finite outcomes are
configuration/execution errors, never fills. Commission equals executed
notional times commission_bps / 10000, once per fill.

The implementation exposes only a validated Symbol/timestamp/sequence/open value
to execution; it cannot inspect an in-progress Bar's future high, low, close, or
volume. Its
evaluation returns an unlimited-liquidity candidate for the order's entire
remaining quantity without assigning a fill ID or mutating portfolio state. An
ineligible sequence, unmet stop, or unsatisfied limit returns no candidate.
Invalid component metadata and unrepresentable calculations are explicit errors.
Market and limit paths do not mutate orders. Once an eligible open inclusively
reaches a stop, execution persists that trigger on the engine-owned order even
when a stop-limit's raw or cost-adjusted price does not satisfy its limit. The
trigger is written only after a valid candidate/no-fill calculation, preserving
order state when evaluation throws.

Limit and triggered stop-limit candidates additionally require the final price
to respect L. If modeled costs breach L, leave the order open; do not clamp the
price to L. Open-only synthetic costs can move the execution price outside the
bar's traded OHLC range; do not use future high/low to clip it. Disclose the
synthetic cost-adjusted price. Later quote-based models can use actual quotes.

Default liquidity is unlimited: fill remaining quantity at one eligible open,
subject to risk. Record the assumption even on zero-volume bars. Optional
`previous_bar_volume_cap_v1` (later milestone) uses only the most recent completed
bar volume for that symbol: budget = floor(participation * volume) shares, with
participation in [0,1]. No completed bar means zero budget. Share that budget
across all orders at this open in order-ID priority; unused budget does not carry
over. It is an estimated liquidity proxy, not observed opening liquidity. Never
read current bar's eventual volume. Partial fills retain status/trigger; each
gets its own commission. At most one fill per order per open.

## Risk boundary and defaults

Risk reads immutable marked portfolio plus accepted pending orders. It approves
or rejects; order resizing is deferred. Default max order quantity is int64 max,
max absolute symbol allocation 100% equity, max gross leverage 1.0, shorting
disabled, cash floor zero. Config values and disabled limits are always recorded.
Maximum-loss/automatic-liquidation rules are deferred, not silently enabled.

At submission, require a visible positive reference mark, positive equity and
valid instrument/request. For each symbol, let Q be current quantity, B all
remaining buys and S all remaining sells including the new request. Conservative
potential exposure is `max(abs(Q+B), abs(Q-S)) * mark`. Sum by symbol for gross
exposure. Long-only requires Q-S >= 0. Reserve remaining buy notionals plus their
estimated costs against cash, giving no credit for pending sells. These are
checks recomputed from state, not cash ledger mutations. Orders competing for
capacity are accepted in command order. Quantities must not overflow sums.

At execution, recheck projected portfolio for each candidate at its actual
cost-adjusted price/fee and current marks, including other outstanding orders.
Gap changes can invalidate approval: cancel the failing order's remainder with
`execution_risk` and reason; do not fabricate an affordable fill or resize it.
Liquidity-based partial quantities are explicit, not risk resizing. Permit a
pure reducing order despite an already-breached exposure cap if it does not
cross zero, lowers exposure, and passes cash/short constraints. This exception
applies to submission and fill checks; it also allows reductions at nonpositive
equity. Risk does not force liquidation if the market alone breaches a cap.

## Acceptance / alternatives

Exact fixtures cover every rule for buys/sells, equality, gaps, persistent
triggers, cost-breached limits, price ticks, pending reservations, short rejection,
fill-time cancellation and reducing positions after an exposure breach. Tests
vary future OHLCV to detect leakage. Later partial-fill tests cover shared budget,
priority, zero volume, cancellation and multiple commission charges.

An OHLC-touch model is deferred until it has explicit ambiguity rules (notably
stop-limit touch order), fill timestamps and notification timing. An assumed
open-high-low-close path and a conservative ambiguity rule produce different
results; neither is hidden inside this baseline.
