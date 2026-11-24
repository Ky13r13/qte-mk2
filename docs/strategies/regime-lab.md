# Data-driven pre-open regimes (regime lab)

**Research record, 2026-11-24.** Code: `python/qte/regime_lab.py`. Report:
`build/research/regime-lab-20261124/report.json`. Rerun with
`.venv/bin/python -m qte.regime_lab --output <new dir>`.

## Method (fixed before fitting)

- **Sessions:** fit on 2008-01-02 to 2018-12-31 (2,769 sessions), validate on
  2019-01-02 to 2025-11-20 (1,727). Nothing after the locked holdout start
  (2025-11-21) is loaded.
- **Indicators**, each computed from data through the prior close (11 in the pool):
  - VIX
  - VIX/VIX3M term structure
  - distance of the prior close from the SMA50, in expected moves
  - SMA20−SMA50 spread
  - Bollinger %B (20, 2)
  - Bollinger bandwidth percentile (252 sessions)
  - SPY VWMA20 tilt
  - SPY CMF20
  - SPY MFI14
  - 10-session realized vol / VIX
  - 5-session return in expected moves
- **Target:** the next session's (high − low) / expected move, where the expected
  move is prior SPX close × prior VIX / 100 / √252. This is realized range in
  units of what VIX priced in, which is what decides whether options are rich or cheap.
- **Model:** a regression tree with depth ≤ 4, so each regime uses **at most 4
  indicators** (the user's cap was 5). Splits are at deciles, each leaf has at
  least 120 fit sessions, and a split must explain at least 0.2% of its node's variance.

## Indicator verification

- **VWMA, CMF, MFI and ATR match independent textbook formulas.** The maximum
  difference was 1.1e-13 over 2,487 real SPY daily bars, and they are tested in
  `tests/python/test_indicator_reference.py`.
- **Fixed a defect:** MFI could return 100.00000000000001, because it multiplied
  by 100 before dividing.

## Does it hold up out of sample?

| Classifier | Validation R², next-session range (fit-period means) |
|---|---|
| **12 new regimes** | **0.111** |
| Existing 9 labels (`regime_study`) | 0.045 |
| VIX in 5-point buckets | −0.016 |

The regimes' ordering by mean range is the same in both periods: **Spearman
0.951** between fit and validation.

## The regimes

Range is the mean next-session range in σ. Columns show fit / validation values.

| ID | Rules (≤ 4 indicators) | Sessions | Range σ | Close beyond 1σ | Close beyond 2σ | Trend days |
|---|---|---|---|---|---|---|
| R01 | vix_ts ≤ 0.840, bb_pctb > 0.842 | 239 / 184 | 0.69 / 0.66 | 6.3% / 6.0% | 0.4% / 0.5% | 4.6% / 2.7% |
| R02 | vix_ts ≤ 0.840, 0.720 < bb_pctb ≤ 0.842 | 159 / 109 | 0.77 / 0.72 | 11.9% / 8.3% | 0% / 0% | 7.5% / 7.3% |
| R03 | 0.840 < vix_ts ≤ 0.915, bb_pctb > 0.847 | 360 / 273 | 0.79 / 0.78 | 8.6% / 10.3% | 0.3% / 0.4% | 6.9% / 8.4% |
| R04 | vix_ts ≤ 0.915, bb_pctb ≤ 0.720, VIX ≤ 13.37 | 201 / **13** | 0.85 / 0.97 | 14.9% / 15.4% | 1.5% / 0% | 9.0% / 23% |
| R05 | 0.840 < vix_ts ≤ 0.915, 0.720 < bb_pctb ≤ 0.847 | 240 / 178 | 0.89 / 0.88 | 11.3% / 14.0% | 0.8% / 1.7% | 10.4% / 9.6% |
| R06 | vix_ts ≤ 0.915, bb_pctb ≤ 0.720, VIX > 13.37, rv_iv ≤ 0.587 | 138 / 100 | 0.97 / 0.93 | 18.8% / 20.0% | 0% / 2.0% | 16.7% / 17.0% |
| R07 | 0.915 < vix_ts ≤ 1.024, trend50 > −1.57, rv_iv ≤ 0.711 | 254 / 169 | 0.99 / 0.92 | 22.8% / 21.9% | 2.8% / 3.6% | 20.1% / 13.6% |
| R08 | vix_ts ≤ 0.915, bb_pctb ≤ 0.720, VIX > 13.37, rv_iv > 0.587 | 322 / 244 | 1.06 / 0.97 | 27.6% / 24.6% | 1.6% / 1.2% | 21.7% / 16.8% |
| R09 | 0.915 < vix_ts ≤ 1.024, trend50 > −1.57, rv_iv > 0.711 | 370 / 190 | 1.10 / 0.99 | 25.7% / 24.2% | 4.3% / 2.6% | 23.2% / 14.2% |
| R10 | 0.915 < vix_ts ≤ 1.024, trend50 ≤ −1.57, bb_width_pct ≤ 0.734 | 131 / 68 | 1.12 / 1.12 | 28.2% / 26.5% | 1.5% / 1.5% | 28.2% / 19.1% |
| R11 | 0.915 < vix_ts ≤ 1.024, trend50 ≤ −1.57, bb_width_pct > 0.734 | 134 / 120 | 1.27 / 1.14 | 29.1% / 34.2% | 2.2% / 1.7% | 27.6% / 25.0% |
| R12 | vix_ts > 1.024 (backwardation) | 221 / 79 | 1.40 / 1.29 | 35.7% / 39.2% | 6.8% / 11.4% | 41.6% / 24.1% |

The plain-English groups:
- **R01–R03** are calm uptrends in steep contango near the top of the Bollinger
  band. They move about 0.7σ, and 2σ closes are about 0.4%.
- **R12** (backwardation) moves about 1.3–1.4σ, and 2σ closes are 7–11%.
- **R04** has too few validation sessions (13) to judge.

## Direction: no indicator predicts it

Correlation with the next session's close move. All |t| < 2, and signs flip between periods.

| Indicator | Fit | Validation |
|---|---|---|
| VWMA tilt | −0.012 | +0.042 |
| CMF20 | −0.026 | −0.039 |
| MFI14 | −0.022 | −0.007 |
| Bollinger %B | −0.029 | +0.007 |
| SMA50 distance | −0.018 | −0.020 |
| 5-day return | −0.033 | −0.016 |
| VIX/VIX3M | −0.009 | +0.004 |

R01 shows a small up drift: +0.09σ, t = 2.4 in validation but only 1.4 in fit.
Treat it as weak.

**Conclusion:** QTE's volume-weighted indicators compute correctly, but on daily
SPX they carry no reliable directional or range information. What is predictable
is the **size of the move relative to implied volatility**. So options strategies
should trade volatility, not direction.

## Data fix found during this work

Schwab re-stamped the newest daily candle at midnight Eastern instead of midnight
Central, which created a phantom Sunday 2026-11-22 session. Daily bars are now
keyed by New York trading date (regression test in `test_truth_db.py`), and the
truth database was rebuilt from raw captures. The old copy is kept as
`data/truth/backups/qte-truth.pre-daily-fix-20261124.sqlite3` (moved there on 2026-11-24).
