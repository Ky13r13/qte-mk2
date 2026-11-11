# ADR 0013: Derivative accounting before derivative replay

Status: D1, D2a variation settlement, D2b.1 margin budgeting and D2b.2a
submission admission implemented, 2026-11-10. The user requested both Nasdaq options
gamma scalping and a separate futures-only dealer-gamma hypothesis. This extends
[ADR 0004](0004-portfolio.md); it does not enable either in the backtest engine.

## Decision

Reuse the existing C++ Portfolio authority, average-cost position transitions,
fill journal and trade episodes. No strategy-side ledger or futures-as-shares
shortcut. Preserve the three-argument cash-equity instrument constructor,
multiplier-one behavior, canonical Bar and existing equity dataset hashes.

Named instrument factories distinguish cash equity, futures and European
premium-paid options on one declared futures contract. Metadata includes an
integer positive currency-per-point multiplier, tick grid, currency, last-trade
and expiry timestamps; options additionally carry underlying contract identity,
right, strike and explicit cash-versus-futures-delivery settlement convention.
These are supplied metadata, not exchange verification. Portfolio checks the
declared underlying, currency, multiplier and deadline relationships. Strike
spacing is not inferred from the option premium tick size.

Contract counts use existing checked integer quantity storage. Legacy C++ type
names `ShareAmount`/`ShareQuantity` remain for source compatibility; derivative
documentation and policy APIs call them contracts. Renaming every equity API is
not necessary for this increment. Multipliers are checked on fills and again at
the ledger boundary. Cash and calculated PnL retain ADR 0005 binary64/tolerance
rules; there is no claim of exchange-grade fixed-point settlement accounting.

## Accounting invariants

Let Q be signed contracts, A economic average entry in quoted points, B futures
cash-settlement basis, P execution price,
V valuation price, m currency per point, d signed fill contracts and F cash fee.
On reduction, C = min(abs(Q), abs(d)), and realized increment is
`C * m * (P - A) * sign(Q)`. Existing weighted averages, flat reset, reversal
residual basis and episode fee allocation remain unchanged.

| Instrument | Fill cash change | Marked carrying value | Signed exposure |
|---|---|---|---|
| Equity (m=1) / premium-paid option | `-d*m*P - F` | `Q*m*V` | `Q*m*V` |
| Future | `C*m*(P-B)*sign(Q) - F` on reduction, otherwise `-F` | `Q*m*(V-B)` | `Q*m*V` |

Before any variation settlement, B equals A. Opening/addition/reversal handling
below keeps the economic and cash bases distinct after settlement. Economic
unrealized PnL remains `Q*m*(V-A)`; futures carrying value is **not** economic
unrealized PnL once variation cash has moved. `realized_gross_pnl` is economic
close PnL, not a broker's daily cash-realization or tax classification.

Gross exposure sums absolute signed exposure; net exposure sums signed exposure.
For options this is premium exposure, **not** underlying delta-equivalent risk.
Future notional never enters cash as an equity purchase. For both profiles:

`equity = cash + sum(carrying_value)`

`equity - initial_cash = realized_gross + unrealized - commissions`

An ordinary valuation mark does not transfer cash or realize PnL. The named
`ValuationMark::option_value` permits a worthless option's zero valuation;
ordinary `create` remains strictly positive, and Position rejects zero for
equity/futures even when the option-mark constructor was used. Negative Nasdaq
prices and zero-priced executions are outside this initial profile.

## Deliberate execution gate

This is a **direct-fill and explicit-settlement accounting foundation**, not a
margin or clearing model. Dataset preflight, ResearchProfile and open-only execution
reject derivative metadata. Equity risk rejects derivative instruments and
mixed portfolios, including an equity order against a flat derivative universe.
Pure Python policies can compute decisions; they cannot be selected as runnable
GUI/CLI strategies, submit live orders, or fabricate executions.

## D2a: explicit variation settlement

`VariationSettlement` contains a run-local strong SettlementId, canonical symbol,
reference cutoff, availability and effective timestamps, global event sequence,
positive finite price, multiplier, source ID and vintage ID. Require
`reference <= available <= effective`. All timestamps keep signed UTC nanosecond
semantics. The universe supplies currency/multiplier; provider payloads remain
outside the core. Futures only; options/equities and mismatched multipliers fail.
Reference at or after expiry is rejected: final settlement/exercise is not D2a.

For settlement price S and the currently held signed contracts:

```text
cash_transfer = Q * m * (S - B)
cash_new = cash_old + cash_transfer
B_new = S if Q != 0, otherwise 0
```

No quantity, mark, average economic entry A, economic realized PnL, fee or trade
episode changes. Keeping valuation V unchanged preserves equity because the
carrying value decreases by exactly the cash transfer. An unmarked position
remains unmarked; settlement never makes a stale quote appear fresh. Flat
settlements are journaled with zero transfer and zero basis.

An addition weights A and B independently using existing and new contract
counts; partial reductions retain both. A closing fill transfers only remaining
PnL measured from B, while its trade episode realizes economic PnL measured from
A. Reversal residual inventory starts with A=B=execution price; flat resets
both to zero. This prevents daily variation from being counted again on close.

Portfolio is the commit authority. It stages the affected Position, compensated
cash and equity invariants, then appends the event/cash delta/quantity/old-new
basis journal before noexcept state commits. Fill IDs and settlement IDs are
separate contiguous sequences starting at 1; timestamps and global event
sequences are shared with marks/fills. Low-level `Position::settle_variation`
alone has no cash, clock or replay authority.

An exact duplicate ID **and every immutable field** returns `already_applied`,
even after later events, and advances no clock. An altered duplicate fails.
New IDs must be contiguous and reference cutoffs strictly increase per symbol;
reusing a cutoff with another ID is not a supported correction. Corrected or
revised historical settlement facts require rebuilding/replaying the ledger;
there is no silent mutation of old history.

The cutoff includes **all already-processed fills at that timestamp**. A later
fill at that same timestamp belongs to subsequent event order. If any committed
fill for the symbol has a timestamp later than the cutoff, reject the settlement
instead of applying old prices to current inventory. Intervening marks or other
symbols' fills do not change that inventory and do not by themselves block a
delayed posting. Historical-position reconstruction is a later feature.

The research model retains ADR 0005 binary64 accounting without cash-cent
rounding. Settlement price is not forced onto the order-execution tick grid.
Actual clearing uses product/currency-specific money rounding and banking
cycles; D2a does not reproduce those operational rules. Closing cash PnL is
posted immediately in this research ledger, not held until a broker's banking
cycle. [CME's variation and money-calculation reference](https://www.cmegroup.com/clearing/files/CME-Money-Calculations-Futures-and-Options.pdf).

Acceptance: hand-calculated long/short settlements, repeated/no-op events,
adds/reductions/reversals/closures after settlement, no duplicate episode PnL,
unchanged equity at an old mark, mixed option/hedge equity, unmarked/flat states,
every-field conflict detection, inclusive cutoff/expiry boundaries, numeric
overflow and transaction rollback. No normal replay path emits these events yet.

## D2b.1: explicit futures margin budgets

`FuturesMarginEvaluator` reads the authoritative Portfolio without mutation.
Its immutable schedule supplies initial margin, maintenance margin and estimated
one-way fee **per contract**, currency/multiplier, source/vintage and
`reference <= available < valid_until`. There are no default broker rates.
Require initial >= maintenance > 0 and fee >= 0, exact universe coverage and
matching ledger metadata. This first budget profile is futures-only, with no
cross-contract offsets; equity/options or mixed universes fail closed.

For each symbol, Q is filled signed inventory; buys/sells are accepted pending
**remaining** quantities, counted separately:

```text
worst_contracts = max(abs(Q), abs(Q + buys), abs(Q - sells))
current_initial = sum(abs(Q) * initial_rate)
current_maintenance = sum(abs(Q) * maintenance_rate)
projected_initial = sum(worst_contracts * initial_rate)
pending_fees = sum((buys + sells) * one_way_fee)
eligible_collateral = min(cash, equity)
available_after_projected = eligible_collateral - projected_initial - pending_fees - cash_buffer
maintenance_excess = eligible_collateral - current_maintenance - cash_buffer
```

Opposing orders never receive netting credit or release current margin before
execution. Pending fees are reserved on both sides. Collateral conservatively
excludes unsettled profits but includes unsettled losses; this is a declared
research policy, not a claim about any broker's buying-power rules. Negative
availability/excess is retained and flagged, not clipped to zero. Maintenance
excess concerns current holdings; pending fees/initial requirements appear in
the separate projected availability measure.

Nonflat positions require available, positive, fresh marks. Schedule expiry,
future orders, evaluating current ledger state before its last event, unknown
symbols, duplicate order IDs and numeric/quantity overflow fail closed. The
caller captures pending state after each fill/cancel acknowledgement from the
same current event frontier; a cancel request alone does not release a reserve.
Terminal orders cannot be captured. The evaluator owns no pending-state cache.

The budget report alone is **not order permission**. Positive availability does
not certify that an order is eligible to trade. The submission policy below adds
one lifecycle boundary; fill-time admission, actual contract fees, forced
liquidation, option margin and gap/latency handling remain unimplemented.

## D2b.2a: submission admission

`evaluate_submission(candidate, ledger, pending, now)` is a read-only decision
for a **NEW** future order. It validates the request against the ledger-owned
instrument, rejects future-dated submissions and candidate IDs already reserved,
and requires a positive, visible, fresh target mark even when the position is
flat. Last-trade cutoff is inclusive: `now >= last_trade` denies admission.
The unchanged budget calculation includes the candidate plus every accepted
pending remainder, including estimated fees; the candidate is never temporarily
opened and the Portfolio is never copied or mutated.

The result contains a typed code and an optional projected budget. `approved`
means the projected initial and current maintenance budgets both pass. Otherwise
maintenance denial takes precedence over initial-margin denial. One exception,
`reduction_only`, permits reducing an already underfunded position if:

- the candidate opposes current nonzero inventory;
- candidate contracts plus **all** same-symbol closing reservations do not
  exceed that inventory's magnitude;
- there is no same-symbol opening-side reservation.

This can permit an exit even when its estimated fee worsens a cash deficit; it
does not waive fresh-mark, contract-cutoff or schedule validation. This is only
a submission-frontier permission, **not a persistent reduce-only order flag**.
Fill-time admission must recheck inventory, all remaining reservations and fees;
a later fill must not turn an earlier approved reduction into a reversal.

Error precedence is deterministic: candidate structure/identity first, then
`missing_fresh_mark`, existing schedule/portfolio/reservation validation, then
`contract_closed`, then projected margin. Missing-target-mark denial returns no
projection and short-circuits other input checks. Other malformed inputs throw
when reached; neither errors nor denials change any lifecycle or accounting.

The caller applies the decision through existing order transitions and recaptures
pending remainders after acknowledged fills/cancellations. A cancellation request
alone releases nothing. This increment neither wires derivatives into the
BacktestEngine nor provides a broker acknowledgement or execution price.

### Run the combined synthetic demonstration

From the repository root with the existing toolchain:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --preset dev -DQTE_BUILD_EXAMPLES=ON
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev -j2
./build/local-dev/qte_futures_accounting_demo
```

`examples/futures_accounting.cpp` opens one artificial future, budgets opposing
pending orders, settles cash without refreshing the mark, acknowledges duplicate
delivery, cancels the pending orders and closes the position. Artificial margin
rates are 2000 initial / 1500 maintenance and fee 2.50 per contract per side;
these are **not current NQ rates**. It ends with cash/equity 10195 from initial
10000, economic gross PnL 200 and fees 5. This demonstrates accounting, not
historical performance. A separate flat-ledger example accepts three contracts,
rejects two more for insufficient reserved margin, acknowledges cancellation,
then accepts a fresh two-contract order. Submission decisions create no fills
and leave that ledger's cash at 10000. No network, strategy, broker or live orders
are involved.

## Remaining execution gates

Before removing gates, implement and test:

1. Wire D2a facts through a causal session/settlement event source; implement
   required correction/reconciliation and currency-rounding policies before
   claiming brokerage compatibility.
2. D2b.2b fill-time margin admission, rechecking nonreversing reductions during
   breaches, pending lifecycle integration, actual per-contract execution fees;
   then subsequent-quote fills with availability, latency, size and stale-data rules.
3. Session/last-trade liquidation, unresolved exposure reporting, expiry and
   exercise safeguards, explicit roll/mapping and quote/Greek dataset identity.
4. Engine strategy lifecycle, cancel/late-fill/partial-leg recovery, then normal
   research reports and GUI integration with clearly synthetic test artifacts.

Historical prices/Greeks and dealer observations require provenance; synthetic
tests demonstrate correctness, never economic merit. No broker connection,
licensed data purchase or live trading is part of D1.
