# The last 10 minutes (15:50–16:00 ET): measurement

**Research record, 2026-11-24.** This is descriptive, not a strategy test. It came from the
user's observation that moves at 15:50 ET outpace expected volatility. 15:50 is when NYSE
and Nasdaq publish closing-auction (MOC) imbalances.

- **Data:** SPY 5-minute bars (Alpaca), 2016-01-04 to 2025-11-20, 2,462 complete sessions. The holdout was not read.
- **Expected move:** 10-minute σ = prior VIX × √(10 / (390 × 252)).

## Volatility: the observation holds

| 10-minute window ending | Realized / VIX-implied (RMS) | Moves beyond 1σ |
|---|---|---|
| 09:40 | 0.92 | 25.6% |
| 12:00–15:00 (average) | 0.51 | ~5% |
| 15:50 | 0.63 | 10.3% |
| **16:00 (15:50–16:00)** | **0.86** | **19.5%** |

- **The last 10 minutes is the second most volatile window of the day,** about 1.7× midday.
- **It's getting stronger:** 0.91 and 23% beyond 1σ since 2022 (daily 0DTE era), against 0.83 and 17% in 2016–2021.
- **By pre-open regime:** stress_selloff 1.51, recovery 1.22, high_vol_range 1.04, low_vol_trend 0.74.

## Direction: close to a coin flip

- **The final 10 minutes closes up 50.1% of the time.** On days that moved more than 0.5 EM by 15:50, it continues that direction 50.0% of the time.
- **Only one weak, consistent tilt:** the 15:20–15:50 move predicts the final move's direction with correlation +0.06 (t 2.3) in 2016–21 and +0.09 (t 2.7) since 2022. That explains under 1% of the variance.
- **Day-so-far and close-location signals** were positive before 2022 but are not significant since.

## What this does and doesn't show

It shows the closing move is large **relative to VIX**. A bought option profits only if the
move beats **the option's actual price at 15:50**. Market makers know the close is volatile, so
the real 0DTE IV at 15:50 may already price it in. QTE has no 15:50 chain captures yet.

**Next measurement (no fitting):** capture the 0DTE chain at 15:49–15:50 ET daily. Compare the
ATM straddle's implied 10-minute move with the realized 15:50–16:00 move, and record a paper
BTO entry at the ask, settled at the official close (PM cash settlement, so there's no exit
spread). After 20–30 sessions this says whether real prices are cheap.
