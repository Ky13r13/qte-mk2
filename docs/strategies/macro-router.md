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

## GUI and CLI profile

Research → New run → **Macro router** uses the ordinary preview, pinned-input
job, explicit registration and result workflow. Enter a symbol, local bars and
an explicit JSON profile. The default empty observation list stays in cash.
The form never downloads macro/dealer data or derives positioning from prices.

The profile requires `evidence` (`synthetic` or `user_declared`), `candidates`
(one to six distinct `CandidateConfig` kinds) and `macro_observations`.
Optional `dealer_observations`, `regime` and `priorities` use the existing
`DealerObservation`, `RegimeConfig` and `RouterConfig` fields/defaults.
At most 128 total observations are accepted; identity strings are bounded to
256 control-free characters. `source_id` must identify the source/transformation;
`vintage_id` identifies each release/revision, not just the current value.
These are caller declarations, not source authentication.

For the **GUI textarea**, timestamps, age durations and candidate lookback/holding
integers must be **quoted canonical decimal strings**. Values and fractions
remain JSON numbers. The browser sends the raw text unchanged; the server checks
duplicate keys, nonfinite values, integer bounds and ambiguity. Limit: 12,000
UTF-8 bytes, within the form's 16,384-byte request limit. Edits require a new
preview. Minimal synthetic profile for the example hourly bars:

```json
{
  "evidence": "synthetic",
  "candidates": [{"kind": "trend", "fast_period": "2", "slow_period": "3", "atr_period": "1"}],
  "macro_observations": [
    {"feature": "growth_z", "value": 3, "reference_ns": "1704204000000000000", "available_ns": "1704204000000000000", "source_id": "synthetic:growth", "vintage_id": "v1"},
    {"feature": "inflation_z", "value": 0, "reference_ns": "1704204000000000000", "available_ns": "1704204000000000000", "source_id": "synthetic:inflation", "vintage_id": "v1"},
    {"feature": "liquidity_z", "value": 0, "reference_ns": "1704204000000000000", "available_ns": "1704204000000000000", "source_id": "synthetic:liquidity", "vintage_id": "v1"},
    {"feature": "volatility_pct", "value": 12, "reference_ns": "1704204000000000000", "available_ns": "1704204000000000000", "source_id": "synthetic:volatility", "vintage_id": "v1"}
  ]
}
```

CLI config files use **native JSON integers**, not the GUI string encoding.
The complete [CLI example](../../examples/macro-router-run.json) adds a later
risk-off release to exercise liquidation. From the repository root, choose a
new output directory (exports never overwrite existing artifacts):

```sh
.venv/bin/python -m qte run --config examples/macro-router-run.json --output build/macro-router-demo --export-version 2
```

Register that folder in Research. Open **Macro routing** to see transition time,
target, active family and phase; open a row for reasons, score, permitted families
and exact as-of inputs. The optional `route_events.json` export records actual
route-state changes, not every bar, fill, or engine event sequence. A change in
an input value that does not change routing state need not create a row.
The full supplied profile remains in the manifest. Availability must not exceed
the decision timestamp; freshness uses economic reference time, not revision
release time. Missing/stale inputs block new entries. Dealer mode defaults to
`ignore`; `required` rejects unqualified proxies as positioning even when their
numeric gamma looks plausible. Neither `observed` nor `user_declared` labels
prove that data is genuine. No synthetic result establishes trading merit.

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
