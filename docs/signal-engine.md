# Signal engine

`python/qte/signal_engine.py` runs the two intraday setups as one causal,
incremental engine: **trend days** (S7 opening-range breakout) and
**σ reversals** (expected-move bounces). The same code runs historical
replay, recorder-journal replay and live shadow mode. Given the same
inputs, it writes byte-identical `signals.ndjson` in every mode.

It emits **signals only**. It does not choose contracts, size positions,
submit orders, read credentials or touch an account. Turning a signal into an
SPXW option or ES/MES futures order is the next (paper) milestone.

## Run it

Replay a Schwab minute capture:

```sh
.venv/bin/qte signals replay \
  --minutes build/.../SPX-minute.json --symbol '$SPX' \
  --volume build/schwab-pilot-20261121/SPY-minute-20261012-20261120.json \
  --context my-context.json --output build/signals/replay-YYYYMMDD
```

Shadow mode during market hours: start the existing recorder (see
[Live desk](live-desk.md)), then follow its journal in a second terminal:

```sh
.venv/bin/qte signals follow --quotes build/live/<capture>/quotes.ndjson \
  --context my-context.json --date YYYY-MM-DD --output build/signals/live-YYYYMMDD
```

Each signal prints to the terminal and is appended to `signals.ndjson`.
`--no-wait` processes an existing journal once (offline audit of a recording).
`--short` also emits the upper-bound short reversal (see below). Output
directories are never overwritten.

## Context file

Facts known before the open, one entry per session:

```json
{"schema": "qte-signal-context-v1", "sessions": [{
  "trading_date": "2026-11-24", "open_ns": 0, "close_ns": 0,
  "prior_close": 6650.12, "prior_vix": 16.4, "regime": "trend_up",
  "straddles": [{"available_ns": 0, "points": 31.5}],
  "regimes": [{"scheduled_ns": 0, "available_ns": 0, "vix": 16.4, "vix3m": 17.9, "gamma": null}]
}]}
```

- `open_ns`/`close_ns`: the XNYS regular session in UTC nanoseconds (early
  closes respected).
- `prior_close`, `prior_vix`: the previous session's close for the signal
  instrument and VIX. Expected move = prior close × prior VIX / 100 / √252.
- `regime`: the pre-open label from `qte.regime_study.features` (one of
  `REGIMES`).
- `straddles` (trend days): ATM straddle mid in index points. The engine
  freezes the latest value available at range formation (open + 30 min).
- `regimes` (trend days): quarter-hour VIX/VIX3M/dealer-gamma observations
  keyed by scheduled time, usable from `available_ns`.

Missing optional inputs never get filled in. Trend-day candidates are then
recorded as `filtered` with reasons such as `missing_atm_straddle`.

## Rules

**σ reversal (long).** These are identical to `qte.reversal_tail` and call its
`reversal_signal`. BB20 is the population standard deviation of 20 consecutive
one-minute closes, reset each session. The previous close is below the lower band, and the current positive candle
closes back inside. One of the last five lows touched `prior_close − k·move`
(k = 1, 2), and the close is within 0.25·move of that level. The first 20
bars are skipped, and each variant gets at most 5 signals with no overlap.
The entry is at the next minute close, held 5 minutes, and the hold must end
by the session close. Longs are vetoed after a prior stress or high-vol-down
regime, or once the intraday stress latch trips (a 2.5σ decline, or a 2σ
decline with prior VIX ≥ 25).

**σ reversal (short, `--short`).** This is the mirror image at `prior_close + k·move`. It is
**untested**. The research only studied the lower bound, and no veto applies.

**Trend day.** These are identical to `qte.strategies.trend_day_convexity.screen`. The
opening range is six complete five-minute slots. From the 35-minute slot
through min(240, session − 15), a five-minute close outside the range is a
candidate. It is actionable only when all three hold:

- Proxy volume for that slot exceeds 1.5× the mean of the prior 20 sessions.
- The range is narrower than 0.6× the frozen straddle.
- The regime is eligible: negative gamma or VIX/VIX3M > 1.02.

The first actionable candidate ends the session, and a missing slot halts
it. Signals carry `target_abs_delta` 0.12 (0.10–0.15 band) and a range-midpoint
stop reference.

## Signal record

Each line is `qte-signal-v1`: `signal_id`, `strategy`, `variant`, `status`
(`signal` = actionable, `filtered` = diagnostic, with `reasons`),
`decided_ns`, `direction` (+1/−1), `option_right` (C/P), `futures_side`
(buy/sell), `reference_price` and strategy `details`.

## Timing and data assumptions

- A minute bar is usable only at its right edge. `decided_ns` is that edge.
- Live minutes are built from `$SPX` last-price ticks keyed by **provider
  source time**. A minute completes when a later-minute tick arrives, or 2 s
  after its edge by the receipt clock. Late ticks are dropped and counted, and
  emitted bars are never revised. Minutes with no ticks are gaps. A disconnect
  or unavailable/stale underlying discards the in-progress minute.
- Live bars come from ticks and can differ slightly from provider minute
  candles. Replay against the provider's bars is the reference.
- Batches are processed strictly in time order. Every bar ending at a
  timestamp is supplied together, matching the C++ scheduler's batching.

## Verification

`tests/python/test_signal_engine.py` (87 tests) covers:

- **σ reversals match the research code:** the same signal times as
  `reversal_tail.reversals` on its fixture and on 75 random sessions
  across three regimes.
- **Trend days match the research code:** the same signal time, side,
  price and relative volume as `s7_study` on its two-session fixture.
- **No look-ahead:** changing future bars never changes earlier signals.
- **Live and offline agree:** following a recorder journal reproduces the
  offline replay exactly.
- **Bar building:** source-time minutes, late-tick and gap handling, and
  disconnect resets.
- **Input checks:** ordering and validation guards.
- **Deterministic CLI:** repeated replays produce identical output.

## Open work toward live trading

1. **Live data for trend days.** The recorder subscribes `$SPX` and SPXW only.
   Live trend days also need SPY (or ES) minute volume, a live ATM straddle
   and VIX/VIX3M. The likely source is the Schwab `CHART_EQUITY`,
   `CHART_FUTURES` and `LEVELONE` services. Until then, live trend-day
   candidates are `filtered`.
2. **Context builder.** Generate the context file automatically from daily
   SPX/VIX history and `regime_study.features`.
3. **Futures.** Run the engine on ES/MES minutes with that contract's own prior
   close as the anchor (basis differs from SPX). This needs a futures minute
   adapter.
4. **Paper trading.** Map signals to SPXW contracts or ES/MES orders, fill them
   against recorded quotes with `execution-quality`, and track P&L in the paper
   ledger. Lock the rules before the first forward session.
5. **Schwab order gateway**, with limits and a kill switch, only after paper
   results justify it.
