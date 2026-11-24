# Live desk

Open **Research → Live desk → Explore synthetic replay** in the Linux GUI. From the repository root, generate the demo once for each new checkout, then start the GUI and sign in with the terminal code.

```sh
.venv/bin/python -m qte live-demo
.venv/bin/python -m qte gui
```

The replay follows two contracts across a disconnect. Expand **buy-at-ask** to trace the decision midpoint of 5.55 to the later ask of 5.70. On two contracts, the 0.15 premium difference costs $30 and the modeled commissions add $1.30. The larger eight-contract probe exceeds the five contracts on display. The reconnect probe has no valid book. The desk shows the final disconnected state and the position scenario as of its earlier valuation time. All demo quotes and holdings are synthetic.

## Observed quotes to paper execution

1. Select exact SPXW contract keys from the chain. Start a read-only recorder with a new archive directory and `--desk`. The live GUI reads updated market status every three seconds. Completion and disconnect clear its books. [Recorder and replay commands](tick-data-acquisition.md)
2. Export a schedule covering decision and expiry. Publish the session clock with `qte.market_sessions check --desk`. Supply any earlier broker or contract cutoff that applies. [Calendar commands and product policy](market-sessions.md)
3. Copy `examples/live-desk/probes.json` for the intended session. Supply a unique probe ID, exact contract, buy/sell side, quantity and UTC decision time in nanoseconds. Set the evidence label to `observed` only for recorded market data. Each probe can include absolute `contract_cutoff_ns` and `broker_cutoff_ns`. Set the assumed delays, freshness, wait, hold, exit buffer and commission explicitly.
4. Run the analyzer on the sealed normalized journal. Use a dated output for the archive or publish the desk's replaceable report. Expand an order in **Paper execution** to inspect the exact decision and execution journal lines. The JSON includes SHA-256 identities for the journal, configuration and schedule.

```sh
.venv/bin/python -m qte execution-quality \
  --config examples/live-desk/probes.json \
  --schedule build/live-desk/demo/schedule.json \
  --quotes build/live-desk/demo/capture/quotes.ndjson \
  --output build/live-desk/quality.json
```

The decision uses the last valid quote received strictly before decision time minus extra modeled feed delay. The simulated fill uses the first fresh update whose source time is after the decision and at or after modeled order arrival. It rechecks the session at receipt, requires the entire size on the contra side, and expires at the wait limit. Each probe is an independent counterfactual. Simultaneous probes can inspect the same displayed liquidity. Shared capital, queue priority, competing fills and broker acknowledgements require separate evidence.

Shortfall is positive for adverse execution. For a buy it equals (fill − decision midpoint) × 100 × contracts + fees. For a sell the price difference reverses. Groups use the Eastern decision hour and the bid/ask spread in premium points. Medians and nearest-rank p95 use filled probes. Provider-source-to-local-receipt age includes clock skew and feed processing. The order-entry delay is an assumption. [HftBacktest latency models](https://hft.readthedocs.io/en/latest/latency_models.html) · [QF-Lib commission and slippage models](https://qf-lib.readthedocs.io/en/stable/tutorials/customize_your_backtest.html)

## Position scenarios

Start with `examples/option-position-risk.json`. Enter net signed quantities, listed expiry time, decimal volatility and financing assumptions. Each scenario specifies a spot change in percent, an IV shift in percentage points, and elapsed calendar minutes. The demo uses a five-minute horizon. Each leg uses a standard multiplier of 100.

```sh
.venv/bin/python -m qte.live_risk examples/option-position-risk.json --desk-snapshot
```

Select **View saved risk snapshot** to inspect the result. Add `--quotes JOURNAL` to replace the sample marks with recorded SPX/SPXW market data available at the configured valuation time. For a streaming book, supply current unexpired SPXW holdings and run `.venv/bin/python -m qte.live_risk MY-HOLDINGS.json --desk-live`. The live panel uses the active recorder epoch and current clock. Quantities, IV, rates and dividend yield come from your declaration. Missing or stale marks and insufficient close-side size suppress a full liquidation total.

The portfolio gamma grid shows signed dollar gamma per squared SPX point under each supplied shock. The P&L table reprices the book relative to its unshocked model value. Cash liquidation uses the bid for longs and ask for shorts before fees. European SPXW uses Black–Scholes–Merton and American ETF snapshots use QTE's early-exercise tree. Model values and displayed marks have different uses. The existing forward-study ledger continues to own its candidate fills and P&L. [Forward study](five-agent-research.md)
