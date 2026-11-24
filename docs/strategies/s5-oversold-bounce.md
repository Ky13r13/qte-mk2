# S5 oversold bounce

This implements the SPY price hypothesis and an optional SPXW call-spread replay from the Claude research specification. Three outputs answer different questions: the price event study measures the signal, the native equity simulation accounts for overlapping trades and costs, and the options replay requires captured chain deltas and both legs' quotes.

## Run

From the QTE checkout, use the local historical Schwab captures and a new output directory:

```sh
.venv/bin/python -m qte s5-study \
  --daily build/schwab-daily-20261121/SPY.json \
  --minute build/schwab-pilot-20261121/SPY-minute-20261012-20261120.json \
  --output build/my-s5-study
```

Read the comparison in `report.md`. `report.json` includes all session decisions, features, outcome denominators, native fills, cost assumptions and source checksums. Optional `--config` accepts S5 thresholds, e.g. `{"rsi_entry":10,"ibs_entry":0.15,"rsi_exit":65}`. Each configuration change is a new experiment.

## Frozen rules

- Evaluate once at 15:45 Eastern on standard XNYS sessions. Construct the partial daily candle from all regular-session minutes ending by the decision. Historical minute bars are assumed available at interval end. Publication latency and historical adjustment vintages are unverified.
- SMA200 uses 199 completed daily closes plus the partial close. Wilder RSI(2) uses changes in daily closes, seeded with two changes and smoothed with weight ½. Evaluate the latest partial close provisionally. Commit the provider's final daily close for subsequent sessions. A flat RSI series scores 50.
- Internal bar strength (IBS) is (last − session low) / (session high − session low). A zero range leaves IBS undefined. Signal when last > SMA200 and either RSI(2) < 10 or IBS < 0.15. Missing prefix minutes suppress the decision. A missing daily session resets the warmup.
- The underlying event-study mark is the 15:46 opening price. Compare all eligible uptrend sessions, the S5 subset, and its complement. The 1-, 3-, and 5-session outcomes use an equivalent mark on the target session. Also inspect the first subsequent session with RSI(2) > 65 or partial close > prior completed high, otherwise exit after five sessions. Exit observations on half days are 15 minutes before the actual close. Missing outcomes remain censored. Right-censored S5 events with a final mark also report their interim return.
- The native proxy runs one SPY share at a time on $10,000, at the same modeled entry/exit clocks. Additional signals while long are skipped. QTE's base and stressed minute-replay execution costs apply, including commissions. End equity includes open positions at the last bar close. The independent event marks exclude costs and dividends.

## SPXW call spreads

Add `--spread-data PATH` to the same command. `examples/s5-synthetic-spread.json` is a complete hand-priced input for one October 14 signal; the marks in that file are invented. The tape declares an all-in per-leg fee and one-lot maximum dollar risk including round-trip fees. Each signal date has a call-chain snapshot with separate UTC nanosecond source and availability times, plus synchronized QTE quote rows. Supply standard European PM-settled SPXW calls expiring 2–5 calendar days later. The chain must have been received by decision time and be at most one minute old.

Choose the longest listed expiry in that range. Buy the call closest to 0.50 delta within [0.45, 0.55], breaking ties by lower strike. Sell the same-expiry call five points higher. These choices resolve the original unspecified width and expiry tie-break. Entry requires a later synchronized, sized pair from 15:46 through 15:55 with relative bid/ask spreads ≤5% on both legs. A simulated one-lot complex order pays long ask minus short bid. Exit value is long bid minus short ask. The QTE paired-quote model enforces its 30-second source-age limit. Displayed liquidity is a model assumption.

Trigger the half-debit stop on an observed executable close-side credit ≤50% of the debit. Execute on a subsequent eligible pair. S5 indicator exits, the fifth-session deadline and PM expiry also require a quote sourced strictly after the trigger. Expiry-day liquidation is requested 15 minutes before cash close. During an open position, a gap exceeding two minutes between monitored executable pairs during regular hours leaves the result unresolved. The gap check restarts at the next regular open; there is no overnight stop monitoring. Stops can fill through their trigger. Reports retain skips, unresolved entries and closed-only P&L. These independent probes do not share capital or enforce portfolio-wide sleeve limits.

## Historical result

The local 2026-10-12 through 2026-11-20 window contained 30 sessions, 26 uptrend dates and 7 S5 signals. Mean one-session gross SPY returns were +0.061% for S5 and −0.231% for all uptrend dates. The rule-exit mean was +1.479% for six completed S5 events; the unresolved November 16 entry was down 6.153% at the final 15:46 mark. The native one-share base proxy finished at $9,991.19 with four closed trades and one open. No matching observed multi-day SPXW quote tape was identified for these signals.

This is retrospective, with overlapping events and a small sample. The two native cost cases measure transaction-cost sensitivity. The run contains no parameter optimization, independent out-of-sample or walk-forward period, Monte Carlo analysis, or multi-regime robustness test.
