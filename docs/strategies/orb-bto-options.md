# Opening-range breakout with buy-to-open 0DTE SPXW calls and puts

**Research record. Pre-registered 2026-11-24, before any results were computed.**
The user's preference is BTO calls and puts only: no spreads, no selling. The signal
is the most robust region from [search round 1](search-round1-20261124.md), the
futures opening-range-breakout (ORB) plateau around trial 915/1643, here traded with options.

## Data and isolation

- **Underlying:** SPY 5-minute regular-session bars (Alpaca, truth database), scaled to
  SPX by the prior-day SPX/SPY close ratio. Sessions 2016-01-04 to 2025-11-20.
  - Dev: through 2024-11-18.
  - Validation: 2024-11-19 to 2025-11-20.
- **The holdout (2025-11-21 onward) is not read.**
- **Expected move:** EM = prior SPX close × prior VIX / 100 / √252.
- **Options are modeled:** Black-76, rate 0, IV = prior VIX × m, frozen per trade,
  time = minutes to 16:00 / (390 × 252).
  - **m = 0.60 is the primary.** Real chain readings on 2026-11-24, converted from
    Schwab calendar IV, were 0.54 ATM and 0.60–0.66 at OTM strikes.
  - **Tick size:** $0.05 below $3, $0.10 at or above (SPXW rules).

## Primary spec

| Element | Rule |
|---|---|
| Opening range | High and low of the first two 5-minute bars (09:30–09:40 ET) |
| Range filter | Range width < 0.4 EM |
| Signal | The first 5-minute close above the range high (call) or below the range low (put), from bar 3 through bar 48 (decided by 13:30 ET) |
| Volume filter | SPY volume of the signal bar > 2.0 × the mean volume of the same bar slot over the previous 20 sessions (exactly 20 required) |
| Entry | Next bar's open. BTO 1 ATM 0DTE SPXW (nearest 5-point strike) |
| Exit | First 5-minute close back through the range midpoint (stop), otherwise the 15:55 ET close |
| Costs | Pay mid + $0.05, sell at mid − $0.05, rounded adversely to the tick; $1.25 fees per contract per side |
| Limit | One trade per day |

Exits use 5-minute closes only.

## Robustness grid (reported in full; not used to pick a winner)

- strike: ATM, or the next OTM strike
- exit: as primary, a +100% premium target added, or a 60-minute time stop added
- last entry: bar 48, or bar 24 (by 11:30 ET)

That's 12 variants. **Stress tests on the primary spec:** m ∈ {0.54, 0.66, 1.0} and a $0.10
half-spread.

**Controls:**
1. **Coin flip:** the same entry bars with a random call/put choice (20 seeds), to test
   whether the breakout direction carries information.
2. **Underlying only:** the same trades in SPX points, to see the signal without the
   option layer.

## Pass criteria (primary spec)

1. Net P&L is positive in **both** dev and validation.
2. There are at least 100 dev trades, and the dev mean per trade has t ≥ 2.
3. At least 60% of calendar years are profitable.
4. The mean per trade beats the coin-flip control.
5. It stays positive at **m = 0.66** and at a **$0.10 half-spread**.

## Results (computed 2026-11-24, after pre-registration)

Report: `build/research/orb-bto-options-20261124/report.json`. Sessions 2016-01-04 to 2025-11-20.
The holdout was not read.

**Verdict: FAIL. The primary spec fails 2 of 5 checks** (t ≥ 2, and the stress tests).

| | Trades | Mean per trade | t | Win rate |
|---|---|---|---|---|
| Primary (entries by 13:30, hold to close), all | 1,496 | +$8.10 | 0.21 | 27% |
| Dev / validation | 1,349 / 147 | +$5.10 / +$35.70 | 0.15 / 0.16 | |
| Daily-expiry era (2022-05-11 onward) | 498 | +$95.88 | 1.02 | |
| Coin-flip direction control (20 seeds) | | −$16.33 | | |
| Underlying only (SPX points per trade) | 1,496 | +1.96 points | | 45% |

- **Stress** (mean per trade): IV 0.54× +$72; IV 0.66× **−$55**; IV 1.0× −$400; $0.10 half-spread −$0.93.
- **By year:** 6 of 10 profitable. Max drawdown $82k, losing streak 42 trades, worst trade −$2,393.

**What it shows:** the breakout direction does carry a little information. It beats the
coin flip, and the underlying makes +1.96 points per trade. But that's too small to
reliably pay for ATM option decay and spread in the afternoon.

**Grid lead (chosen after seeing results, so it is not a pass):**

| Variant | Mean per trade | t | Dev / validation |
|---|---|---|---|
| **Entries by 11:30 ET, hold to close** | **+$111** | 1.93 | +$68 / +$494 |
| **Entries by 11:30 ET, 60-minute time stop** | **+$86** | **2.98** | +$51 / +$407 |
| Entries by 11:30, +100% target | +$57 | 1.52 | +$41 / +$200 |
| Entries by 13:30 with a +100% target | −$25 | −0.98 | negative |

Early breakouts plus a short hold look meaningfully better, but only 50–60% of years are
profitable, and validation is much stronger than dev, so recent years carry the result.

**Next (pre-register before testing):** an "early ORB" primary with entries by 11:30 ET,
a 60-minute time stop and the midpoint stop, re-priced at m ∈ {0.54, 0.60, 0.66}. Confirm it
on data it hasn't seen: either paper trading forward with real quotes (`session-read` and the
recorder), or the single human-run holdout.

## Forward paper trial: early ORB (pre-registered 2026-11-24, before any forward data)

The rule comes from the grid lead above and is fixed here. It runs on live Schwab data with
**real quotes**, so no option prices are modeled for the fills.

| Element | Rule |
|---|---|
| Opening range, range filter | As primary: first two 5-minute bars, width < 0.4 EM (prior SPX close × prior VIX / 100 / √252) |
| Signal | First 5-minute close outside the range whose bar starts at or before **11:30 ET** |
| Volume | Signal-bar SPY volume > 2.0 × the mean of the same slot over the previous 20 sessions, **all from Schwab SPY 5-minute bars** (the backtest used Alpaca volume; the threshold is relative within one provider) |
| Entry | Within 10 minutes of the signal bar's close: BTO 1 ATM 0DTE SPXW (nearest 5-point strike to the live index) **at the quoted ask**. A signal seen more than 10 minutes late is recorded as `missed`, with no entry |
| Exit | First 5-minute close back through the range midpoint, or 60 minutes after entry, or 15:55 ET, **at the quoted bid** on the next run. If no quote can be had before expiry, settle at intrinsic value from the SPX close |
| Fees | $1.25 per contract per side |
| Ledgers | `data/truth/forward/orb/entries.ndjson` and `exits.ndjson` (hash-chained, one record per session each). The modeled price is recorded next to each fill |

**Decision rule, fixed now:** after **30 paper trades**, continue toward live only if the mean
quoted P&L per trade is above $0 **and** the median quoted-minus-modeled entry gap is within
$0.50. Otherwise reject the lead. No rule changes during the trial.
