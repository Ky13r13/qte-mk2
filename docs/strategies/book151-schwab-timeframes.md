# Schwab Book strategy explorer

The refreshed Schwab authorization returned daily OHLCV for 12 ETFs from 2015
through November 20, 2026. Raw capture hashes and receipt times are saved in
`build/schwab-daily-20261121/`. Five captures contained one or more inconsistent
OHLC rows. The separate daily engine screen excludes those symbols. IWM, XLB,
XLE, XLF, and XLI remain available for source audit.

QTE ran 1,920 native backtests for 20 fixed book adaptations across 1-, 5-, 10-,
15-, and 30-minute, daily, weekly and monthly candles. It used existing SPY/QQQ
one-minute captures for all intraday intervals and the new clean Schwab daily
captures for calendar intervals. Next-open executions are simulated. Five
candles of exposure translate to different durations at different intervals.
Archived option quotes are insufficient for a matching historical options test.

Nine of 160 pairs passed the retrospective research screen, all on daily or
weekly candles. None of the four existing daily Book151 implementations beat
the matched reference and met the other criteria in the separate seven-ETF,
210-run study. See `build/book151-schwab-timeframes-20261121/report.md` and
`build/book151-schwab-daily-20261121-final/report.md`.

Open **Research → Book strategies** in the local GUI. Select the strategy,
candle size, underlying, period, and cost assumptions. The pooled metrics
describe SPY and QQQ. The chart and trade ledger show the selected symbol.
Green up triangles mark long fills, pink down triangles mark short fills, and
gold diamonds mark exits. Click a ledger entry to jump to the fill. The colored
markers describe simulated fills and do not imply broker orders.

The exporter checks the archived study results against their manifest and both
symbols' candle captures against the hashes in the study protocol before
creating its output directory. A changed capture requires a matching study.
The local view reads an authenticated, bounded artifact set under
`build/book151-chart/`. To reproduce into a new directory, run:

```sh
.venv/bin/python examples/book151_timeframes.py \
  --daily build/schwab-daily-20261121 --minute build/schwab-pilot-20261121 \
  --output build/book151-schwab-timeframes-new
.venv/bin/python examples/book151_gui_export.py \
  --study build/book151-schwab-timeframes-new --daily build/schwab-daily-20261121 \
  --minute build/schwab-pilot-20261121 --output build/book151-chart-new
```

The view uses the fixed `build/book151-chart` location. Review the replacement
export before moving it into that location. Existing exports are preserved.
