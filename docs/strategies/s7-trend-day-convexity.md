# S7 trend-day convexity

S7 asks whether a tight opening range, unusually heavy volume and a trend-supporting regime identify index breakouts large enough to pay for a same-day OTM option. The research command implements the long-single configuration in `archive/specs/claude-trading-research/example_config.json`. Run one index per account, either SPX with SPXW options or XSP with XSP options. The spread alternative in the source paper remains unspecified.

## Run it

From the checkout, replay two synthetic sessions with a $10,000 account. Open the resulting `report.md` to see the call scale out and the put exit at the midpoint. Use a new output directory for each run.

```sh
.venv/bin/qte s7-study --demo --output build/my-s7-demo
```

Screen local historical data with the same rules. Supply `--options path/to/tape.json` to enable the regime, implied-move and option execution stages. The JSON report records every outside-range close examined, rejection reasons, fills, account results and input hashes. Use `--symbol XSP` with XSP index captures. Both profiles use SPY as a volume proxy.

```sh
.venv/bin/qte s7-study \
  --index-minute build/daily-regimes/year-20261120/raw/spx-minute-*.json \
  --spy-minute build/schwab-pilot-20261121/SPY-minute-20261012-20261120.json \
  --output build/my-s7-observed
```

## What triggers a trade?

1. Use the high and low of the first 30 regular-session minutes. At 10:00 Eastern, freeze the sum of the midpoints of the nearest-strike same-day ATM call and put. This gives the implied move in index points. Require the opening range to be strictly narrower than 60% of that move. Nearest-strike ties go to the lower strike.
2. Check completed five-minute closes from 10:05 through 13:30 Eastern, ending earlier on shortened sessions. A close strictly above the range selects a call. A close strictly below selects a put. Require SPY share volume for that five-minute slot to exceed 1.5 times its mean over the previous 20 scheduled sessions. Incomplete slots, missing prior observations and zero baselines cannot confirm volume. Capture bars are assumed available at their right edge. Interpolated index minutes are excluded.
3. Require observed negative dealer gamma **or** VIX/VIX3M strictly above 1.02. Use the latest scheduled quarter-hour observation, starting at 09:45. A delayed release becomes usable on receipt within that quarter. An estimated gamma sign alone cannot qualify a trade.
4. Take the first qualifying close each day. Select a strictly OTM option with absolute delta from 0.10 through 0.15, nearest 0.12. Ties go to the narrower spread, then contract ID. Require displayed two-sided size and a spread at most 10% of midpoint. The midpoint, time window, single attempt and whole-lot rules are frozen research choices.

## Execution and risk

At the signal, commit a buy limit at the selection midpoint for 30 seconds, then at the original ask through 60 seconds. Simulate a fill only at a later source quote whose displayed ask meets the limit and spread test. Fill at that ask up to displayed size, then cancel the balance. The marketable-quote model assumes access to displayed liquidity. Real queue priority, broker latency and price improvement require separate evidence.

The monthly premium budget starts at 10% of opening account equity. Each entry can spend the smaller of 1% of current equity and twice the remaining monthly budget divided by remaining scheduled trading days, counting today. Cash and unspent budget also cap the purchase. Buy whole contracts with a multiplier of 100. Entry premium and entry fees consume the budget permanently for that month. Cash receives exit proceeds net of fees. Defaults are `--cash 10000 --fee 0.65` per contract per executed side. A run starts flat and assumes zero earlier spend. When using selected dates, balances describe only those dates.

Place resting sells at 3× and 6× entry premium. At 3× sell floor(N/3) contracts. At 6× cumulative sales reach floor(2N/3). A one-lot trade keeps its runner. Targets fill at the limit, capped by observed bid size. A five-minute close at or through the range midpoint against the position cancels the targets. Liquidation also starts 15 minutes before scheduled close. Use the last positive bid as an exit limit, with up to two downward revisions on later quotes. Remaining contracts cash-settle at intrinsic value only with quote coverage through the close and an official PM settlement, with zero settlement fee assumed.

A missing bar while monitoring the midpoint, a quote gap over two minutes, or missing required terminal evidence leaves the trade unresolved and blocks new entries. Inspect the reported fills in that case. The isolated research sleeve has one position at a time. Combined-account drawdown halts, live routing and capital shared with other strategies belong to a separate portfolio deployment.

## Supplying an options tape

Use JSON with `schema` equal to `qte-s7-quotes-v1`, `symbol` equal to the selected index, `evidence` equal to `observed` or `synthetic`, and `sessions` keyed by local ISO trading date. Each session has three fields.

| Field | Expected value |
|---|---|
| `regimes` | List of quarter-hour records with integer UTC `source_ns` and `available_ns`, `gamma`, `vix` and `vix3m`. Missing inputs are null. Gamma accepts negative, positive, neutral, estimated_negative or estimated_positive. The source timestamp is the scheduled classification time. Supply only readings available for that classification. |
| `snapshots` | Strictly increasing synchronized source and availability timestamps, `kind` (`chain` or `update`) and `books`. Use `chain` when the source covers the ATM strike neighborhood at formation or the eligible delta universe at selection. Use `update` for monitoring. A selection chain must be at most one minute old. |
| `settlement` | Official same-day PM cash settlement in index points, or null. Used only at expiry. |

Each book has a padded 21-character OSI `key`, signed `delta` (nullable outside selection), `bid`, `ask`, `bid_size` and `ask_size` in contracts. For example, `SPXW  261120P06990000` is a November 20, 2026 SPXW 6990 put. Both ATM legs must be quoted at the same nearest strike. Source and receipt timestamps apply to every field in a snapshot. A stale leg carried forward as a fresh quote violates the input contract. The executable fixture is in `python/qte/s7_fixture.py`.

## Evidence so far

Six locally archived SPX dates were screened. Four had usable observed opening ranges. Only November 20, 2026 also had the full SPY same-slot volume history. On that date, 36 of 39 outside-range closes exceeded the volume threshold. These are repeated observations within one session. The matching ATM straddle, intraday regime and tradable option history were unavailable. Historical S7 option P&L and win rate remain unknown. The synthetic run checks mechanics. The captured-date sample has no holdout, walk-forward or transaction-cost sensitivity evaluation.
