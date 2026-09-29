# Moving-average regime reference specification

This is a deterministic integration baseline, not evidence of profitability.
Parameters are pre-registered for each report and are not selected from its
in-sample or out-of-sample results.

## Rules

- Universe: one configured cash-equity symbol in an action-free, unadjusted,
  fixed-duration canonical dataset.
- Input: completed OHLCV bars; only close is used. At least `slow_period`
  completed bars are required.
- Features: `fast_sma = mean(last fast_period closes)` and
  `slow_sma = mean(last slow_period closes)`, including the current completed
  bar. `0 < fast_period < slow_period`.
- Entry: `fast_sma > slow_sma`, current quantity is zero, and the strategy has
  no outstanding order. Submit a market buy for `quantity` whole shares.
- Exit: `fast_sma < slow_sma`, current quantity is positive, and there is no
  outstanding order. Submit a market sell for the entire current quantity.
  Equality causes no action. There are no stops or implicit end liquidation.
- Sizing: fixed positive whole-share `quantity`; engine risk limits remain
  authoritative.
- Execution: signal at completed-bar publication; eligible at the symbol's next
  actual open under `open_only_v1`, with the configured costs and risk checks.
- State: one pending order ID, cleared by a filled, rejected, or canceled order
  update (and defensively by its fill). Retrying occurs only on a later bar. This prevents duplicate
  submissions while an accepted order awaits its next open.
- Parameters: symbol, `fast_period`, `slow_period`, quantity, initial cash,
  execution costs, and the engine risk configuration.

## Research labels and limitations

Reports label in-sample and out-of-sample runs separately and include a zero-cost
baseline plus a stressed-cost scenario. The reference performs no parameter
optimization, walk-forward analysis, Monte Carlo analysis, or universe search.
Those are explicitly absent—not implied by a profitable result. The fixture is
synthetic and cannot establish economic merit. Relevant risks include regime
selection, parameter/data snooping, survivorship bias in a chosen symbol,
unmodeled corporate actions, coarse open-only fills, and cost sensitivity.
