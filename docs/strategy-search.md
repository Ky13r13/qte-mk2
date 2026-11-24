# Strategy search

`qte search` lets people and agents run large parameter sweeps over truth-database
bars in a C++ core, with built-in protection against overfitting.

```sh
.venv/bin/qte search describe    --dataset SPY:5m:alpaca
.venv/bin/qte search sweep       --dataset SPY:5m:alpaca --costs spy_as_es --agent me \
    --spec '{"family": "sigma_reversal", "sides": "both"}' \
    --grid '{"bound_sigma": [0.75, 1, 1.5, 2], "hold_bars": [3, 6, 12]}'
.venv/bin/qte search leaderboard --dataset SPY:5m:alpaca --costs spy_as_es
.venv/bin/qte search holdout     --dataset SPY:5m:alpaca --trial 123 --confirm   # humans only, once per spec
```

## Protections

- **Locked split.** The first `describe` (or sweep) on a dataset writes
  `build/search/splits/<dataset>.json` once and never changes it:
  - **holdout:** the last 252 sessions,
  - **validation:** the 252 sessions before those,
  - **dev:** everything earlier.

  To re-split, you have to delete the file by hand.
- **Agents never see the holdout.** Its sessions aren't loaded for any sweep. The
  agent tool (`qte search agent-tool`) can't reach it at all.
- **Every trial is logged** in `build/search/trials.sqlite3`: spec, agent, dev and
  validation metrics, and daily validation P&L.
- **Rankings account for how much was tried.** The leaderboard ranks by
  *deflated Sharpe probability* (Bailey & López de Prado) computed over **dev plus
  validation daily P&L together**: the chance the true Sharpe beats the best you'd
  expect from that many unskilled tries. By default it lists only specs that are
  profitable on dev and validation separately; `--include-negative-dev` shows
  the rest. More searching raises the bar for everyone.
- **Sweep output order:** `--sort-by consistent` (default: the weaker of dev and
  validation Sharpe), `dev` or `validation`. Each result includes dev net P&L by
  year and by regime, and dev gross P&L before costs.
- **The holdout is spent once per spec.** `holdout` runs a recorded trial on the
  locked sessions, records the result permanently, and refuses to run the same
  spec again.

## Simulation (C++ core, `include/qte/research/sweep.hpp`)

The simulation trades one futures contract at a time and is used for screening
only. Real accounting stays with the portfolio ledger and `BacktestEngine`, and
candidates must be re-run there before anything is promoted.

- **Signal timing:** a signal uses a bar's completed values, so it's known at the
  bar's close. Entry is the next bar's open in the same session, plus adverse slippage.
- **Stops and targets** are checked against every held bar's high and low,
  starting with the entry bar:
  - if both are touched in the same bar, the stop is assumed to hit first,
  - if the open is already beyond the stop, the fill is at the open.
- **Session close:** positions are flattened at the close of the last allowed bar.
  Only regular-session bars are used. Early closes come from the XNYS calendar.
- **Session context uses prior-day data only.** The anchor is the prior close of
  the traded series. The expected move is anchor × prior VIX / 100 / √252. The
  regime is the pre-open label from `qte.regime_study`.

**Cost presets** (one contract, per side):

| Preset | Point value | Tick | Commission | Slippage |
|---|---|---|---|---|
| `es` | $50 | 0.25 | $2.25 | 1 tick |
| `mes` | $5 | 0.25 | $0.85 | 1 tick |
| `spy_as_es` | $500 per SPY $1 | 0.025 (= one ES tick) | $2.25 | 1 tick |

`spy_as_es_frictionless` is the same with zero costs. It's diagnostic only, and
it's ranked on its own leaderboard: use it to separate "no signal" from "costs eat
the signal". Agents may switch only to this preset.

`spy_as_es` treats SPY bars as ES: SPY $1 ≈ 10 SPX points. SPY has years of
history, and Schwab only has ES 5-minute bars from May 2026.

## Spec vocabulary

`family` is `sigma_reversal` or `opening_range_breakout`. `sides` is
`long_only`, `short_only` or `both`. These filters are optional:
- `regimes`: the pre-open regimes allowed for all entries.
- `long_regimes` and `short_regimes`: extra per-side restrictions.
- `min_prior_vix` and `max_prior_vix`: a gate on the prior-day VIX close.

**σ reversal.** It looks for a re-entry into the Bollinger band near
`prior close ± bound_sigma × expected move`. Parameters:
- `bound_sigma`, `touch_tolerance_sigma`, `band_window`, `band_width`, `touch_lookback`
- `stress_latch`: vetoes longs after a 2.5-move decline, or a 2-move decline with
  prior VIX ≥ 25.

With `hold_bars=6`, `max_trades_per_session=5` and 1-minute bars, the C++ core
reproduces the Python signal engine and `qte.reversal_tail` exactly. 40
randomized sessions in `tests/python/test_search.py` check this.

**Opening-range breakout (trend-day proxy).** Parameters:
- `range_bars`, `max_range_sigma`: the range must be narrower than this many
  expected moves. 0 disables the check.
- `volume_ratio`, `volume_lookback`: compares this slot's volume to the same slot
  in prior sessions.
- `last_entry_bar`, `stop_at_range_mid`.

**Exits (all families):** `hold_bars`, `stop_sigma`, `target_sigma`,
`max_trades_per_session`, `flatten_bars_before_close`. Zero disables each.

Grids are cartesian products, capped at 50,000 combinations.

## Agents

The glass agents integration (`.agentctl/`) was removed on 2026-11-24. The
`qte search agent-tool` JSON adapter still exists for any agent framework: it takes
the dataset and costs from a fixed `context` and gives no holdout access.

## Results

- Round 1 (2026-11-24): [search-round1-20261124](strategies/search-round1-20261124.md).
  Nothing is ready for the holdout. ORB (range 2, volume 2×, hold to close) is a real
  plateau but loses in 2016/2017/2019. σ reversal has no edge on 5-minute bars.


## Next steps

- Re-run shortlisted specs through `BacktestEngine` once futures wiring is
  complete, and on ES bars as they build up in the truth database.
- Use options and gamma data to validate (ADR 0013, gamma lab).
- Add families as reviewed C++ code, with a Python reference implementation and
  parity tests.
