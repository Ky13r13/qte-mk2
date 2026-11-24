# SPX regime study — observed prices, modeled options

## November 19 observation capture

`build/regime-observation-20261119-1725/README.md` describes a separate partial
selloff case through 09:23 Pacific: observed SPX/NDX/VIX, ten equity series,
prior daily context, and 20-session same-clock SPY-volume comparison. Raw and
derived hashes, causal feature timeline and dated catalyst context are retained.
It is not loaded as a completed-session backtest and does not change existing
regime thresholds. News first-seen times remain distinct from publication dates;
dealer positioning is unavailable. See the task log for collection follow-up.

Open **Research → SPX regimes** (`/research/regimes`). Choose a year/month
or a captured minute session. Click a candle for its dated inputs. Expand the
regime table for actual start/end dates and reasons. The daily bands and regime
labels use earlier sessions only. Compare development, validation and later
holdout periods separately, then inspect wide costs and VIX repricing.

## Data acquired November 15, 2026

| Robinhood series | Non-interpolated daily coverage | Bars |
|---|---|---:|
| SPX | 1990-11-09 through 2026-11-13 | 9,042 |
| VIX | 2002-05-10 through 2026-11-13 | 6,159 |
| SPY, split-adjusted OHLCV | 1999-09-15 through 2026-11-13 | 6,833 |

Daily requests scanned 1983 onward in bounded four-year chunks. Earlier ranges
returned no observations. These are observed capture boundaries, not a provider
retention guarantee. Minute probes returned no SPX bars through September 30,
2024, and 390 on October 1, 2024. Older sampled dates were empty. The experiment
uses April 2025 and the last 20 captured sessions, rather than downloading every
minute since that boundary: 41 sessions captured, 40 eligible, 15,600 RTH bars.
October 23, 2026 is excluded because its required prior-day VIX is missing.
Boundary probes are retained for audit and excluded from the experiment.

Options checks covered active November 20, 2026 contracts and expired April 11,
2025 / November 13, 2026 contracts. Every requested minute bar in these samples
was interpolated. Contract metadata and recent quotes work; executable historical
option prices remain unverified. No account data or trading tools were used.

Raw captures: `build/robinhood-regimes-20261115/`. Report, trade ledgers, dated
charts, parameters, input hashes and source hashes: `build/regime-study/`.
Provider-specific parsing is isolated in `qte.adapters.robinhood_research`.
The study lives in QTE's Python research layer and reuses its Black-76 calculator;
it does not run these hypothetical option contracts through the C++ broker engine.

## Frozen rules

Require 300 earlier SPX observations and fresh matching prior-session VIX.
Classify stress first (prior move <= -2 sigma with VIX >=25, or VIX >=35 and
close below SMA20), then recovery (positive prior candle within five stress
sessions), high-volatility down/range (VIX >=25), up/down trend (SMA20/SMA50 and
close agree), and range. Uptrend with VIX <15 is the low-volatility regime.
These are price/volatility regimes, not measured macroeconomic or dealer regimes.

Daily one-sigma distance is `prior SPX close * prior VIX / 100 / sqrt(252)`.
VIX describes 30-day volatility: this conversion is a daily approximation,
not a calibrated reversal probability. Bollinger bands use 20 completed bars
and two population standard deviations. SMA300 uses prior daily closes.

Avoid new bearish positions near SMA300 (within 0.5%), below the lower Bollinger
band, or below the -2-sigma level, except under stress/high-volatility down.
Avoid bullish chasing beyond +2 sigma/upper Bollinger and prohibit bullish
entries in stress/down regimes. Intraday stress latches through the session
after a downside move beyond 2.5 sigma, or 2 sigma with prior VIX >=25, while
below the minute mean. These filters cannot prevent all adverse reversals.

Daily variants: SMA20, EMA20, VWMA20, positive/negative flow-adjusted EMA,
Bollinger re-entry, SMA300 reclaim and stress continuation. Each is compared
using long calls/puts, capped credit verticals, and bullish cash-secured puts.
Minute variants test confirmed band re-entry, SMA300 reclaim, stress continuation,
EMA and volume-adjusted EMA. Enter at the next completed minute; hold five
minutes; at most five entries/day, one position at a time. A missing exit books
full reserved loss rather than deleting an entered trade. Five trades is a cap,
not an objective. Exact formulas are also in the GUI and manifest.

Each candidate has an independent $1m sleeve and trades one contract. Short
puts reserve full strike notional; no uncovered short calls. Option prices are
hypothetical seven-calendar-day European SPX-style premiums, including before
actual weekly listings. Fixed r=4%, q=1.3%, $100 multiplier; model IV is prior
VIX. Base half-spread 0.25 points and $1.25/leg/side; wide 1 point and $2.50.
Prices round adversely to 0.10 points. Vertical risk includes modeled closing
friction in addition to expiry width. No calibrated smile, depth, true historical
listing, execution latency, margin liquidation or financing income is modeled.
Daily opening/closing values need not be simultaneous official settlement marks.

## Findings and recommendations

The following are **retrospective 2021–2026 model results**, one contract per
entry; they are not demonstrated executable profits. The best eligible
candidate by net P&L / cumulative max-loss is shown per regime, using >=30 trades
where available. Cash remains preferable when all candidates lose. Selecting
these rows after seeing the holdout is descriptive, not a deployable selector.

| Regime | Candidate to investigate | Trades | Base net | Wide-cost net | Interpretation |
|---|---|---:|---:|---:|---|
| Range | SMA20 directional long options | 193 | $90,167.50 | $60,555 | Best research lead; verify quotes and subperiod concentration |
| High-vol down | VWMA20 directional long options | 90 | $15,245 | $1,490 | Cost margin is thin; previous-five-year selector did not establish this ex ante |
| Low-vol trend | EMA20 directional long options | 194 | $4,205 | -$25,560 | Fails cost robustness |
| Trend up | VWMA20 directional long options | 603 | -$817.50 | -$92,595 | Stay flat under these rules |
| Trend down | Positive flow-adjusted long options | 113 | -$45,082.50 | -$62,265 | Even the best sufficiently sampled candidate loses |
| Recovery | EMA20 directional long options | 27 | $11,592.50 | $7,495 | Small sample; research only |
| Stress selloff | EMA20 directional long options | 17 | $4,257.50 | $1,715 | Small sample; VIX-repriced result flips to -$3,552.50 |
| High-vol range | VWMA20 directional long options | 27 | -$2,497.50 | -$6,635 | Small sample and negative |

SMA300 reclaim across regimes produced +$9,852.50 in the base long-option
model, but only **11** holdout trades. Keep it as an event study, not a validated
edge. The daily all-regime SMA20 candidate made +$32,735 at base costs and lost
$161,090 at wide costs. More trades and an attractive aggregate are insufficient.

An actual chronological selector uses only the previous five years to choose
among capped-risk candidates (>=30 trades and positive training return on risk),
then scores the next year. Across 2016–2026 it produces **+$12,150 base,
-$166,650 wide costs, +$41,620 with VIX repricing**, on the same selections.
This has not passed economic robustness. The split is historical; designing
rules with knowledge of famous episodes means it is not untouched prospective
evidence. Drawdowns shown are closed-trade drawdowns, not intratrade risk.

After the intraday stress override, five-minute long-option band re-entry loses
$2,262.50 across 25 trades at base costs and $6,095 at wide costs. Do not force
a five-trades-per-day reversal strategy from these selected windows.

## Volume-adjusted moving average

SPX has no traded volume. Align SPY volume explicitly and calculate a proxy:

```text
CLV = (2*close - high - low) / (high - low), or 0 for a flat candle
flow20 = sum(CLV * volume, 20 bars) / sum(volume, 20 bars)
adjusted_EMA20 = EMA20 + k * ATR14 * flow20
k = +0.5 or -0.5, fixed before comparison
```

ATR supplies price units and a fixed coefficient controls the shift. This is
neither bid/ask order-flow imbalance nor dealer inventory. Daily long-option
holdout: plain EMA -$13,415, positive shift -$15,755, negative shift -$26,277.50.
For minute data, compare the same SPY-available windows: plain EMA -$1,067.50
versus shifted EMA -$1,547.50, each 95 base-cost trades. The volume adjustment
does not currently justify additional complexity. Do not compare the 95-trade
flow sample with the 200-trade full-coverage EMA as if coverage were equal.

Next: obtain historical executable option quotes for the range and SMA300
event studies, freeze a prospective date range, and test skew/IV shocks plus
spread sensitivity before changing exposure. A true flow extension should use
timestamped SPY or ES bid/ask events, with a separately validated mapping to SPX.
No additional package, paid data subscription or trading connection is enabled.

## References

- [Cboe: VIX and VIX1D horizons](https://www.cboe.com/insights/posts/what-the-vix-and-vix-1-d-indices-attempt-to-measure-and-how-they-differ): why VIX cannot be treated as an observed daily expected move.
- [San Francisco Fed: April 2025 tariff announcements](https://www.frbsf.org/research-and-insights/publications/economic-letter/2025/10/market-reactions-to-tariff-announcements/): event context, not a real-time news input.
- [Cont, Kukanov and Stoikov, The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402): order-flow imbalance uses book events; signed/close-location volume is a different measurement.
- [Dew-Becker and Giglio, Risk Preferences Implied by Synthetic Options](https://www.nber.org/papers/w31833): synthetic and traded option returns need not be interchangeable; an option-selling premium cannot be assumed constant.

## Reproduce and inspect

From the repository root, with the existing `.venv`:

```bash
.venv/bin/python -m qte.regime_report --captures build/robinhood-regimes-20261115 --output build/regime-study-repeat
.venv/bin/python -m qte gui --repo-root . --port 8769
```

The CLI requires a new/empty output directory. The GUI deliberately reads the
fixed `build/regime-study` export only, with bounded authenticated reads and no
arbitrary user paths. No downloads occur when viewing the report. Keep reports
and raw capture hashes together. The research module has no dependency on the
Robinhood connection once captures exist.
