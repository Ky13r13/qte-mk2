# Fixed historical strategy comparison — November 20, 2026

Completed 162 QTE engine runs: four strategies × three ETF proxies × three
chronological windows × two cost assumptions × two regime policies, plus 18
buy-and-hold references. No parameter optimization. **No candidate passed the
complete screen; every candidate trailed its recent buy-and-hold reference.**

Open `build/book151-historical-20261120-final/report.html` for the selectable
candidate/buy-and-hold equity chart and all 24 recent stressed results. Full
fills, closed trades, event equity, coverage, failed gates, bootstrap diagnostics,
source/code/engine-binary hashes and the frozen protocol are alongside it.

## Selected findings

PnL below is dollars on a $100,000 account with 10% target allocation, after
stressed costs. Each window starts with fresh cash. These windows were previously
inspected in other research; validation is chronological, not an untouched
out-of-sample claim. Selected examples are descriptive, not promoted winners.

| Candidate | 2016–2020 | 2021–2023 | 2024–Nov 16, 2026 | Recent trades |
|---|---:|---:|---:|---:|
| IWM channel reversal, ungated | 1,836.92 | 1,274.15 | 2,462.08 | 15 |
| QQQ single MA, regime veto | 5,416.48 | 2,116.13 | 1,587.63 | 51 |
| SPY single MA, ungated | 6,181.43 | 1,575.03 | 1,352.90 | 50 |
| QQQ IBS reversal, regime veto | 5,517.82 | 3,428.12 | -2,426.29 | 65 |

Recent stressed 10% buy-and-hold PnL: SPY $6,557.76; QQQ $6,952.19; IWM
$4,347.07. Time invested and risk differ: dollar underperformance alone is not
proof of inferior risk-adjusted performance. The new study does not compute an
annualized risk-adjusted ranking or optimize allocations to equalize exposure.

The bearish-regime veto reduced recent stressed PnL in **10 of 12** paired
symbol/strategy comparisons. It improved QQQ single MA by $247.64 and reduced
SPY IBS losses by $245.10. It cut QQQ channel-reversal PnL by $2,364.78 and IWM
channel-reversal PnL by $1,603.16. A broad downtrend veto can exclude the very
rebounds that a reversal strategy seeks. This does not justify removing crash
controls from live trading: it rejects this particular universal research filter.

IWM channel reversal remained positive in all three windows, but its 15 recent
trades are too few for our 30-trade screen, and it underperformed the reference.
QQQ IBS is a useful counterexample: gains in both earlier windows did not persist.
Do not tune its thresholds on this recent window to manufacture a passing result.

## Exact protocol and limitations

- Fixed `BookConfig` defaults; all four families and both successful/unsuccessful
  variants retained. The strategy guide defines each signal and its adaptation
  from the source. No new option pricing or futures execution is inferred here.
- Regime policy uses existing SPX/VIX features built from strictly earlier
  sessions. Block entries **and request exits** for `stress_selloff`,
  `high_vol_down`, `trend_down`, missing/unclassified regimes. Exits execute at
  the next observed open. This is a combined entry/exit policy, not an entry-only
  ablation. The same SPX macro regime is used for all three proxies.
- Features are causal by market date but are reconstructed from later captures;
  point-in-time adjustment/revision history is unavailable. Coverage reports
  missing regime days (two in the recent window); veto variants fail closed.
- Provider regular-session, all-adjusted daily OHLCV is explicitly modeled as
  `_ADJ_PROXY` instruments. Not raw executable ETF shares, audited total returns,
  or point-in-time corporate-action data. Capture request dates without timezone
  offsets are interpreted as UTC; conflicting duplicates are rejected.
- 50 warmup observations; signals at daily close; subsequent open fills. Daily
  timestamps conservatively use 09:30–16:00 America/New_York even on early closes.
  No intrabar path or full exchange calendar. ATR stops are close-detected.
- Whole units; tick $0.01. Base commission/spread/slippage: 1/2/1 bps; stressed:
  2/6/4 bps, using QTE's existing spread semantics. Cash earns zero interest.
  No taxes. Penultimate close submits liquidation for the final observed open.
- Screen frozen before outcome computation: positive recent base/stressed PnL,
  positive stressed validation PnL, 30 recent closed trades, positive PnL excluding
  the best exit day, and recent stressed PnL above the reference.
- Paired recent daily-dollar excess versus buy-and-hold: 2,000 circular 20-session
  block resamples, seed 20261120; 95% intervals are exploratory and unadjusted for
  multiple comparisons. They are not a probability of future profitability.
- Entry-regime attribution assigns each entire closed trade's PnL to its fill-day
  regime. Trades may cross regime boundaries; this is not per-regime daily PnL.
- Every run reconciled independent fill-based cash, terminal equity and closed
  trade PnL, required flat terminal positions, and rejected unexpected rejections.

## Research decision

Retain IWM channel reversal as a sparse research candidate and the earlier SPY
OBV / QQQ VWMA / QQQ OBV candidates as provisional daily baselines. Nothing is
cleared for live trading. Preserve fixed parameters for newly collected sessions;
that supplies more useful evidence than another sweep over these inspected dates.

Next bounded experiment: compare the same candidates to exposure-matched
benchmarks, and separate a **stress-only** veto from a broad trend veto. Freeze
that protocol first and label these historical dates exploratory again. Avoid
choosing a different best strategy for each historical regime after seeing PnL.
For the original short-duration SPX options objective, contemporaneous bid/ask
surfaces and execution/IV sensitivity remain necessary; equity signals and
model gamma surfaces alone cannot validate option profitability.

## Reproduce

From the repository root, with existing local captures available:

```sh
.venv/bin/python examples/book151_historical.py --output build/book151-historical-new
```

Optional `--captures` selects the captured regular-session all-adjusted daily
JSON directory; `--regimes` selects the existing Robinhood regime capture bundle.
No downloads or trading calls occur. Output must be new. Initial failed attempt
and intermediate results were preserved, not overwritten. The first failure was
naive/aware capture timestamps, before any outcome run; subsequent loader
validation explicitly requires all-adjusted regular-session metadata.
