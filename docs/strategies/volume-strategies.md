# Common volume strategies

## November 19 profitability screen

**No strategy is cleared for live deployment.** The frozen study completed 264
daily/hourly QTE runs and 400 independent minute-session runs, including paired
price-only controls and two cost settings. No parameter optimization.

Recent daily window: January 2024–November 16, 2026; $100,000 starting cash,
10% allocation. These are constructed adjusted-price proxy instruments, not
executable ETF shares or audited total returns.

| Candidate passing the original screen | Stressed PnL | Paired price-only PnL | Trades |
|---|---:|---:|---:|
| SPY OBV confirmation | $2,203.55 | $1,106.34 | 44 |
| QQQ VWMA trend | $3,123.89 | $2,815.37 | 36 |
| QQQ OBV confirmation | $2,229.19 | $1,857.24 | 40 |

All three had positive earlier validation-window stressed PnL, but all trailed
the 10%-allocated buy/hold baseline in the recent window. Exploratory paired
20-day block-bootstrap 95% intervals for incremental daily PnL include zero for
all three. That does not establish an incremental volume edge. SPY/QQQ OBV recent
profits are concentrated in a few trades entered under the prior-day range label;
entry attribution is not an independently tested regime-switching policy.

All five minute variants lost at both cost settings over 20 selected SPY sessions
(October 19–November 13). Daily positive results must not be reinterpreted as
five-minute options profitability. QQQ hourly breakout was positive but had only
13 trades and lacks earlier validation data.

Full results, assumptions, fills, trades, equity and hashes:
`build/volume-profitability-results-20261119/report.md` and
`build/volume-intraday-results-20261119/summary.json`. Reproduction commands and
remaining data-validation work are in the November 19 task log. Raw captures,
original protocol and offline runners: `build/volume-profitability-20261117`.

## Implemented rules

Status: implemented research hypotheses, not validated profitable strategies.
Five `VolumeStrategy` variants run through the ordinary C++ backtest engine,
single-run CLI, Research form and declarative comparison workflow. The fixed
synthetic strategy lab and macro-router slate remain their original six families.

## Inputs and execution

Use the traded equity/ETF's own completed OHLCV bars, with consistent volume
units, adjustment policy and declared availability. SPX is not a traded equity:
do not fabricate its volume or silently substitute SPY/ES volume. These are
price/volume indicators, not observed buy/sell order flow or dealer positioning.

All periods count observed bars, not calendar days. No session reset is applied;
intraday history can span sessions/gaps. This is not time-of-day-normalized RVOL
or session VWAP. Compare consistent intervals; review seasonal intraday volume
and corporate actions before using real data. No future volume enters a signal.

Signals occur on the completed bar; orders fill at the next eligible observed
open under QTE's configured spread/slippage/commission and risk rules. No
same-close fills or intrabar stop fills are assumed. Long-only, one pending
order, no pyramiding; rejected/canceled orders release pending strategy state.
An outstanding last-bar order need not fill. Open positions remain marked and
reported, not force-closed at an invented price.

Default sizing: floor(10% of current equity / signal close) whole shares.
Independent engine risk checks can reject that order at submission/execution.
Common strategy exits: ordinary rule below, close <= actual entry fill price
minus 2.5 times entry-signal ATR(14), or 20 observed holding bars. ATR is a simple
mean of true ranges, not Wilder smoothing; a close-triggered exit submits a
market order for the next open. Stops do not guarantee a maximum loss.
Python callers can supply the existing availability-aware `regime_filter`; a
false result blocks entries and requests an exit. No new macro model is assumed.

## Frozen default specifications

Here `t` is the current completed bar, C/H/L/V are its close/high/low/volume.
All listed periods and thresholds are configurable via `VolumeConfig`.

| Registered type | Indicator and entry | Ordinary exit |
|---|---|---|
| `volume_breakout` | C[t] > maximum H of **previous** 20 bars AND V[t] / mean V of **previous** 20 bars >= 1.5 | C[t] < minimum L of previous 10 bars |
| `vwma_trend` | VWMA(10) > VWMA(50), each = sum(C*V)/sum(V), including t | Fast VWMA < slow VWMA; equality holds |
| `obv_confirmation` | C[t] > SMA(50) AND 20-bar OBV change > 0; change = sum(sign(C[i]-C[i-1])*V[i]) | C[t] < SMA(50) OR OBV change < 0; ties contribute zero volume |
| `cmf_trend` | CMF(20) > 0.10 AND C[t] > SMA(50); CMF = sum(((2C-H-L)/(H-L))*V)/sum(V) | CMF <= 0 OR C[t] < SMA(50) |
| `mfi_reversal` | Previous MFI(14) <=20 AND 20 < current MFI(14) <50 AND C[t] > C[t-1] | MFI(14) >=50 |

MFI uses typical price TP=(H+L+C)/3 and raw money flow TP*V. Sum flows on rising
TP as P, falling TP as N over the last 14 transitions; MFI=100*P/(P+N).
Unchanged TP contributes neither side. With P+N=0, MFI is unavailable, not 50.
All-up/all-down windows give 100/0. The MFI entry cap avoids buying a recovery
that has already reached its exit threshold. Entry requires two defined MFI
values and therefore 16 bars (also enough for the default ATR).

CMF flat-range bars contribute zero signed flow but retain their volume in the
denominator. Zero-volume denominators make VWMA/CMF unavailable; no artificial
indicator value is inserted. A zero-volume current bar blocks every entry,
while applicable price exits, holding limits and stops still operate.
OBV uses a window change rather than an arbitrary cumulative starting level.

VWMA accumulation is centered on the current close so identical prices cannot
create a rounding-induced fast/slow crossover. All state is reset on engine
start; snapshots come from bounded completed engine history. The minimum engine
history capacity is `config.history_required` (CLI preflight checks it).

## Run now

From the repository root in the local venv:

```sh
.venv/bin/python -m qte run --config examples/volume-breakout-run.json --output build/my-volume-breakout --export-version 2
```

Other ready configurations: `examples/vwma-trend-run.json`,
`examples/obv-confirmation-run.json`, `examples/cmf-trend-run.json`, and
`examples/mfi-reversal-run.json`. Each uses **synthetic** `volume-bars.csv`: 180
continuous hourly bars labeled SPY, deliberately not an exchange calendar or
observed SPY history. They exercise mechanics only; their returns have no
financial evidentiary value. No annualization or optimization is requested.

The fixture uses C[i]=round(100+6*sin(i/12)+0.02*i,4), O[i]=C[i-1] (initial 100),
H/L enclosing O/C with 0.15 padding, and rounded
V[i]=1000+500*sin(i/7)+3000 when i mod 40 = 8 (otherwise no 3000 term).
Starts at 2024-01-02 00:00 UTC. This is an explicit synthetic volume pattern,
not calibrated market microstructure.

Restart the existing GUI process after updating code to refresh the registry.
Under Research → New research run, select one of the five volume names. For
the synthetic fixture use `examples/volume-bars.csv`, interval
`3600000000000` ns and its action-free declaration. The Library exposes this
document. Comparison forms support the same five names; comparisons retain
their existing chronological-window and evidence rules.

Python:

```python
from qte import BacktestEngine
from qte.strategies import VolumeConfig, VolumeStrategy

strategy = VolumeStrategy("SPY", VolumeConfig(kind="cmf_trend"))
results = BacktestEngine(engine_config).run(dataset, strategy)
```

The five default demos ran through the actual engine on November 17: fill counts
2/11/7/7/4 in the table order. These include open-position and unfilled-final-order
cases. Results are local under `build/volume-strategies-20261117/`, with ordinary
orders, fills, equity, metrics, provenance and version-2 chart artifacts.

## Research use and references

Entry/exit thresholds above are explicit QTE hypotheses; educational indicator
definitions do not establish their profitability. Keep all five candidates,
compare unweighted price baselines on identical windows, assess base/stressed
costs and regime attribution, and use new chronological evaluation data before
selection. Do not choose thresholds on the tiny reversal sample already studied.

- [Fidelity: OBV](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/obv) — signed cumulative volume convention.
- [Fidelity: MFI](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/MFI) — typical-price money-flow indicator.
- [Schwab: Chaikin Money Flow](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/C-D/ChaikinMoneyFlow) — volume-normalized accumulation/distribution.
- [Schwab: trading volume](https://www.schwab.com/learn/story/trading-volume-as-market-indicator) — above-average volume as breakout context.
- [Fidelity: volume-weighted moving average](https://www.fidelity.com/webcontent/ap010098-etf-content/16.06/help/research/learn_er_glossary_4.shtml) — bar-volume weighting.
