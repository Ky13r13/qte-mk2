# Strategy search round 1: σ reversal and opening-range breakout on SPY 5m

> **Status:** the raw agent run logs (`.agentctl/runs/...`) were deleted with `.agentctl/` on 2026-11-24. Every trial is still in `build/search/trials.sqlite3`.

**Research record, 2026-11-24.**
- **Dataset:** `SPY:5m:alpaca`. Dev runs 2016-01-04 to 2024-11-18 (2,235 sessions). Validation runs 2024-11-19 to 2025-11-20 (252 sessions). The holdout is **untouched**.
- **Costs:** `spy_as_es`, which is $29.50 per ES round trip (1 tick of slippage per side plus $2.25 commission per side).
- **Inputs:** a baseline sweep by Claude, then glass agents run `.agentctl/runs/20261124-054534-e57ba4e9` (research lead, two researchers, skeptic).
- **Scale:** 1,386 distinct specs tried.
- **Raw data:** every trial is in `build/search/trials.sqlite3`.

## Verdict

**Nothing is ready for the holdout or for live trading.**

| Family | Result |
|---|---|
| σ reversal, 5-minute bars | **No edge, even before costs.** At bound 1.0 it makes $0.6–15 per trade before costs, and at bound ≥1.5 it loses before costs. Re-parameterizing for 5-minute bars (band window 6–10, width 1.5, up to 3 trades per session; 162 points) raised the trade count, not the edge. The rule was built on 1–6 minute timing, and 5-minute bars blur it. |
| ORB, hold 12 bars | Positive before costs (dev Sharpe 0.35–0.69), negative after. **Costs take the whole edge.** |
| ORB, range 2 bars, volume ≥ 2× the prior-20-session slot average, range-midpoint stop, hold to close | A **real plateau**: all 18 neighbors are positive on both dev and validation. Trial 915/1643: dev 0.68–0.73 over about 1,345 trades, validation 1.19–1.27 over 136 trades, about $79 per trade before costs. Break-even is about 3 ticks of slippage per side. |

## Why the ORB plateau isn't enough

1. **It loses money in 2016, 2017 and 2019 at every neighbor.** 2022–2024 accounts for about 118% of net profit, and 2022 alone is 56%. The losses look structural, likely low volatility.
2. **The low_vol_trend filter (trial 1482/1642, dev 0.89) was chosen from dev data.** Validation has almost no low_vol_trend days, so it can't confirm the filter. All of the low_vol_trend loss comes from short trades.
3. **Validation is thin.** One year, 136 trades, so the Sharpe has a standard error of about 1.0. The same year has been reused for 1,386 specs.
4. **The deflated probability over dev and validation combined is about 0.001** given 1,386 tries. The deflation is conservative because many trials are near-duplicates, but the evidence still doesn't clear any reasonable bar.
5. **SPY stands in for ES.** The intraday ES history (Schwab, from May 2026) is too short to test on yet.

## Tooling defects found by the agents, and fixed

- **The leaderboard ranked by validation alone and promoted specs that lose money on dev.** It now scores dev plus validation daily P&L together and requires both to be positive. Legacy trials were re-scored.
- **Agents could only see the top 25 by validation.** `sort_by` (consistent, dev or validation) was added; the default is consistent.
- **The `qte_describe` schema rejected every call.** Fixed.
- **The spec vocabulary couldn't express the hypotheses the agents proposed.** Added `long_regimes`, `short_regimes`, `min_prior_vix` and `max_prior_vix` (C++ kernel, with tests).
- **Process:** editing the working tree during an agent run trips the glass agents scope guard, and the run ends as "failed". Don't edit the repo while agents run.

## Pre-registered next hypotheses (write the spec before testing, test once)

- **H1: volatility gate.** The 915 family with a `min_prior_vix` floor chosen *in advance* (for example 15), with no tuning. This tests whether 2016/2017/2019 lose because of low volatility.
- **H2: side-specific regimes.** Longs in every regime; shorts in every regime except low_vol_trend. This is the agents' "long always, short except LVT" idea, stated before it's tested.
- **Then:** re-run the survivors on real ES bars as Schwab history builds up, re-run them through `BacktestEngine` at 2-tick costs, and finally do one human holdout run on a single spec.
