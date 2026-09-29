# Macro router: one portfolio, one active strategy

`MacroRouterStrategy` turns the [macro regime policy](regime-framework.md) into
an actual single-book strategy. It composes the existing [candidate
families](candidate-library.md), rather than combining independent backtest
equity curves or choosing whichever strategy later performed best. It is a
research hypothesis, not a demonstrated profitable allocation policy.

## Inputs and exact selection rule

Inputs are canonical completed OHLCV bars for one symbol, one `CandidateConfig`
per configured family, and a `MacroRegimeFilter` with timestamped macro and
optional dealer observations. Callers must establish real source identities,
release availability, revision vintages, and transformation provenance. Missing,
stale, risk-off, or extreme-volatility inputs do not authorize entries.

At each matching-symbol bar close, the router calls
`regime_filter.explain(bar.end_ns)`. The first family that is both configured and
permitted by that decision wins the following ordered priority list:

| Macro state | Volatility | Default priority |
|---|---|---|
| Risk-on | Low | Contraction, breakout, trend |
| Risk-on | Normal | Pullback, trend |
| Risk-on | High | Trend, rebound |
| Neutral | Low or normal | Mean reversion, pullback |
| Other, unknown, or extreme | Any | Cash |

There is **no signal scanning**: a higher-priority family's false entry condition
does not permit trying the next family. Its warmup must finish and its own entry
rule must trigger. Missing families are skipped. An empty priority list means
cash. Custom priorities in frozen `RouterConfig` are research parameters and
must be declared before comparison.

Dealer permissions can remove choices, not override the macro filter. The
existing negative-gamma policy removes mean reversion, pullback, and rebound;
positive gamma removes breakout and contraction. Those labels depend on suitable
external dealer observations. This router neither infers positioning from bars
nor claims to observe dealer buying or selling. Required but unqualified dealer
data blocks entries; default dealer mode remains `ignore`.

## Entry, exit, sizing, and transition semantics

The active child supplies its documented indicator formulas, entry conditions,
ordinary exits, close-based ATR stop, maximum holding period, and equity-fraction
whole-share sizing. The C++ engine retains sole authority over risk approval,
execution prices, commissions, positions, cash, and PnL. Signal calculation is at
bar close and execution uses the next eligible open under `open_only_v1`.

When the target family changes while invested or while an order is pending, the
active child enters **retirement**. Its entry permission is forced false. Once
its pending order becomes terminal, it requests an exit on a subsequent eligible
bar if still invested. The router keeps forwarding only that child's order and
fill callbacks until the position is flat and no order is pending. A new child
is then created during a later bar callback, never from a fill or order-update
callback. Thus an exit fill and replacement entry are not assumed simultaneous.

Retirement stays latched even if the macro regime switches back before an exit
completes. Rejected or canceled exits are retried on later bars. After becoming
flat, selection uses the latest available macro decision, not the originally
requested replacement. If already flat with no pending order, a changed target
may activate at that bar close without an unnecessary exit. Unknown/risk-off
conditions also request a close; they cannot guarantee execution before the next
available price or prevent a previously submitted order from filling first.

The router owns exactly one active child and tracks one pending receipt through
public lifecycle updates. A narrow context wrapper observes `submit_order`
receipts and forwards public portfolio/history access; it never reads or mutates
child private fields. Retiring and replacement strategies cannot submit competing
orders. It neither pyramids independent family positions nor adopts an unrelated
existing position. Short positions are rejected by the long-only contract.

## State and reproducibility

`history_required` is the maximum of the supplied child requirements; configure
engine history capacity accordingly. New children have fresh pending-order,
entry, stop, and holding-period state but may use already-completed canonical
history. No future bars, future releases, or cached indicator state from another
child are supplied. `on_start` clears all routing state, making reuse across
separate engine runs reproducible.

Read-only diagnostics are `active_family`, `pending_order_id`, and `route_events`.
The latter is a tuple of frozen records containing timestamp, target, active
family, phase (`active`, `retiring`, `cash`), macro/volatility/dealer labels, and
reasons. Records are appended on routing-state changes, not on every unchanged
bar. Record these alongside fills and equity when auditing a routing experiment.

```python
import qte
from qte.strategies.candidates import CandidateConfig
from qte.strategies.router import MacroRouterStrategy, RouterConfig

strategy = MacroRouterStrategy(
    "SPY",
    [CandidateConfig(kind=kind) for kind in
     ("trend", "breakout", "contraction", "pullback", "mean_reversion", "rebound")],
    regime_filter=point_in_time_filter,
    config=RouterConfig(),
)
result = qte.BacktestEngine(qte.BacktestConfig(
    100_000, history_capacity=strategy.history_required,
    execution_costs=qte.ExecutionCosts(1, 2, 1),
)).run(data, strategy)
```

The point-in-time filter and canonical dataset in this example must be supplied
by the caller. No download, broker connection, account order, exchange calendar,
corporate-action adjustment, or daily-provider adapter is implied.

## Verification and limitations

Deterministic actual-engine tests verify exact trend-to-mean-reversion handoff
fills and equity, risk-off liquidation, unknown/extreme blocking, delayed-release
and staleness behavior, dealer permission intersections, first-permitted priority,
rejection/cancellation/gap recovery, no pyramiding, state reset, future-data
perturbation invariance, and a retirement latch surviving canceled exits and a
regime reversal. Their paths and macro releases are synthetic and prove behavior,
not economic merit.

The composite is one additional candidate in the frozen research slate. Compare
it against standalone/gated families and risk-matched cash/hold baselines using
the [selection protocol](selection-protocol.md). Synthetic outcomes never justify
promotion. This router does not establish statistical significance, eliminate
multiple-testing risk, or promise a strategy that profits in every regime.
