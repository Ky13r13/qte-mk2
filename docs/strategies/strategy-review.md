# Strategy code review (2026-11-22)

Scope: every module in `python/qte/strategies/` at commit 44b5296 plus the
uncommitted working tree. Static review only; tests were **not** executed in
this review session (no shell access was available to the reviewer). No
strategy code was changed.

## Summary

| Module | Causality | Lifecycle | Findings |
| --- | --- | --- | --- |
| `moving_average.py` | OK (completed bars, next-open fills) | No `on_start` reset | L1, L2 |
| `candidates.py` | OK | Resets in `on_start` | L1, N1 |
| `router.py` | OK (current-bar macro decision, latched retirement) | Resets in `on_start` | N2 |
| `book151.py` | OK; `futures_weights` filters `available_ns <= at_ns` first | Inherits candidate lifecycle | M1, N3 |
| `volume.py` | OK | Inherits candidate lifecycle | none |
| `minute_signals.py` | OK (prior-session anchors, lagged windows) | Pure functions | N4 |
| `nasdaq_gamma.py` | Visible part OK | Pure policies | R1 (not fully reviewed) |

No look-ahead defects were found in the code that was reviewed. Every rolling
window that feeds an entry threshold leaves out the current bar where the
docs say it should: the single-MA mean, channel bounds, mean-reversion
baseline, breakout high/low, contraction ATR percentiles, the MFI previous
window and the dealer-gamma lagged window. The current bar is used only as
the completed decision bar, and fills happen at the next open.

## Findings

**L1. A partial fill would clear the pending-order latch early (latent).**
`MovingAverageRegimeStrategy.on_fill` and `_LongOnlyStrategy.on_fill` clear
`_pending_order_id` on the first matching fill. `_LongOnlyStrategy` also
records `_entry_price` from that fill. The engine cannot produce partial fills
today: `src/execution/open_only.cpp` always fills `remaining_quantity()`, and
the FILLED update reaches the strategy before `on_fill`. So nothing breaks
now. If participation caps are ever added, these strategies could submit
duplicate orders and set a wrong stop reference. Suggested fix, to make
together with any partial-fill feature: clear the latch only on terminal
order updates and use a quantity-weighted entry price. Add a regression test
at the same time.

**L2. `MovingAverageRegimeStrategy` has no `on_start` reset.** If an instance
is reused after an aborted run, a stale `_pending_order_id` blocks all trading
without any error. No current caller reuses an instance: `research.py` builds
a new one for each scenario, and `experiments.py` rejects reused factory
instances. Suggested fix: add an `on_start` that clears `_pending_order_id`,
matching `CandidateStrategy`.

**M1. `BookConfig` raises `OverflowError` instead of `ValueError` for huge
integer parameters.** For example, `allocation_fraction=10**400` reaches
`math.isfinite`, which raises `OverflowError`. `CandidateConfig`,
`VolumeConfig` and `nasdaq_gamma._number` catch this case. Suggested fix:
reuse the same guard, and add a test for it.

**N1. Behaviour to be aware of, not a defect.** Sizing uses the decision
bar's close, but the fill happens at the next open, so after a gap the actual
allocation differs from `allocation_fraction`. The ATR stop is checked
against closes, not intrabar lows. `_atr` is a simple mean, not Wilder
smoothing, as its docstring says. `max_holding_bars` counts the fill bar as
bar 1.

**N2. `MacroRouterStrategy.on_end` passes the raw context to the child
instead of `_ChildContext`.** This has no effect today because the candidates
do not override `on_end`. It is still inconsistent with the other callbacks.

**N3. `futures_weights` imports the private `_timestamp` from
`nasdaq_gamma`.** Behaviour is correct. When `expected_timestamps` is
supplied, `interval_ns` is still validated but not used. With
`max_age_ns=0`, `at_ns` must equal the last `reference_ns`.

**N4. `minute_signals.features` shock direction is sticky.** Only the first
|daily_z| >= 1.5 excursion of a session is recorded. The `breakout` family
skips the candle-direction confirmation that `reentry` and `daily_band`
require. Both look intentional but are not documented in the module.

**R1. `nasdaq_gamma.py` was only partly reviewed.** The file reader cut off
the file after `DealerGammaPolicy.decide`'s opposite-signal branch. `_as_of`,
`_marks_as_of` and `_flat` still need to be reviewed for availability
filtering. They should filter by availability before selecting a revision,
the same order `GammaHedgePolicy` uses. In the visible part, `delta_dollars`
holds futures-equivalent units, not dollars; this is a naming issue only.

## Verification still required

```
.venv/bin/python -m pytest -q tests/python/test_candidates.py \
  tests/python/test_router.py tests/python/test_book151.py \
  tests/python/test_volume_strategies.py tests/python/test_nasdaq_gamma.py \
  tests/python/test_minute_refinement.py
```
