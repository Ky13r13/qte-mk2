# Schwab five-minute strategy refinement

QTE screened 58 configurations in development and ran the three frozen
finalists plus their original rules on later sessions. The study ran 384 native
ETF replays across symbols and cost cases. No configuration passed the
stressed development checks. The operational policy is cash.

The Schwab SPY and QQQ captures cover the same 30 regular sessions. October
12–16 warmed the prior-session features. October 19–November 6 supplied 15
search dates. November 9–20 supplied 10 chronological evaluation dates, withheld
from revision. Earlier QTE research inspected overlapping dates. This is
retrospective evidence. SPY and QQQ share calendar exposure and are not counted
as independent days.

| Later-date rule | Fixed P&L | Refined P&L | Refined trades | Refined gross before costs |
|---|---:|---:|---:|---:|
| Bollinger reentry | −$2,332.15 | −$207.12 | 8 | −$67.20 |
| Volume breakout | −$1,537.23 | −$491.20 | 36 | +$138.50 |
| Prior-range reentry | −$319.04 | −$377.99 | 13 | −$148.35 |

Figures combine separate $10,000 target sleeves under stressed costs and a
one-minute entry delay. Refined breakout had 15 trades that were profitable
at unadjusted opens but lost after execution costs. Tightening the Bollinger
rule cut its trade count from 100 to 8, which accounts for much of the smaller
aggregate loss. The prior-range revision did worse than its fixed control.

The American-style option valuation used hypothetical seven-day contracts,
previous-session realized volatility as an IV proxy, and the delayed entry and
exit prices from the ETF replay. The refined breakout made $89.40 at base
modeled costs and lost $379.20 with wider spreads, across 22 modeled trades.
A two-volatility-point drop made the wider-spread loss $1,982.20. Fourteen of
36 opportunities exceeded the $650 modeled debit-plus-fee cap. Schwab option
quotes available for this study were a single end-of-session snapshot, so these
results measure sensitivity to assumptions. They provide no observed option
fills or historical option profitability evidence.

## Rules and search

- Bollinger reentry uses the current and previous completed BB20 close z-scores
  and requires a candle in the reentry direction. Search range 1.25–3.0.
- Volume breakout crosses the high or low of the preceding 20 completed bars
  and requires enough current volume relative to their mean. Search range
  1.0–2.5. The filtered version requires agreement with the session open and
  cumulative close-times-volume average and conditions extreme moves on the
  direction of the observed shock.
- Prior-range reentry uses the preceding close as anchor and the median dollar
  high-low range of five prior sessions as scale. The reentry must stay on the
  same side of the anchor and improve its Bollinger reading. Search range
  0.75–2.25 times the prior range. The range scale has no implied volatility
  interpretation. Filtered reversals veto fades against a latched move of 1.5
  prior ranges, and require direction agreement in an expanded session range.
- The two deterministic interval probes compare both halves. Three rounds
  halve the bracket and keep all trial ledgers. Rankings favor gate passers,
  then sample coverage, then the worst result among base, stress and delayed
  stress. Cash stays selected if nothing passes. No assumption of monotone P&L.
  Independent research agents reviewed early failures and audited the search.
  [Bailey et al. on backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- Signals use completed minutes. Base and stress submit at that bar's end for
  the next observed open. Delayed stress waits one additional minute to submit.
  The exit is five minutes after entry. One position at a time and at most five
  entries per symbol and day. All positions flatten intraday. Base costs are
  1/2/1 bps commission/spread/slippage per configured QTE semantics. Stress
  uses 2/6/4 and $0.01 ticks. The short-side benchmark does not model borrow or
  locate failures. Each native run reconciles fills, trades and terminal equity.
- Development and evaluation gates require positive P&L under each cost case,
  positive P&L after excluding the best date, at least 30 trades and eight active
  dates per case, and positive delayed stress P&L on each symbol. The current
  sample is too short for a reliable regime robustness or deployment claim.
- Options model pricing uses the existing 64-step American lattice, seven
  calendar days at entry, $1-grid strikes at least 0.5% OTM, 4% rate, 1%
  continuous yield and one contract. The annualized prior five open-to-close
  RMS proxy is bounded to 10–80%. Model half-spreads are $0.05 and $0.15 per
  share with fees of $0.65 and $1.30 per side. Dealer flow and historical news
  and volatility surfaces were unavailable in this minute dataset.

One development counterexample was the October 22 10:39 ET SPY prior-range
reentry. It moved from 762.68 to 762.86 and earned $2.34 before costs, but lost
$15.67 under stress. For new sessions, preregister one comparison of the fixed
reentry against a rule that abstains when the remaining distance to the prior
close cannot cover estimated round-trip costs. Reject it if fewer trades reduce
aggregate losses while delayed P&L per trade remains negative. Preserve these
sessions as retrospective research and use the Schwab Streamer to collect
fresh option bid and ask updates for an executable-price test.

```sh
.venv/bin/python -m qte.minute_refinement \
  --captures build/schwab-pilot-20261121 \
  --output build/schwab-minute-refinement-new
```

The completed audit, frozen policy, all attempts, evaluation ledger and
interactive candle report are under `build/schwab-minute-refinement-20261121-final/`.
