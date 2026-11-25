# Short-Term Vol-Reversal / Mean-Reversion / 0DTE Options Research Spec

**Audience:** an automated AI trading or research agent with market-data access.
**Companion files:** `strategy_schema.json` (JSON Schema for configs/signals/orders), `example_config.json`.
**Status:** these are research hypotheses. Every parameter below is a starting point that the agent must validate. None of them is a proven edge.

---

## 0. Read this first: the 400%/month target

The agent must treat **+400% in a month (5x) as a stretch outcome. It is not a planning assumption or an optimisation target.**

- 5x in about 20 trading days requires about **+8.4% compounded every trading day**. No published systematic options strategy sustains that. Funds that get 20–30% a **year** are considered excellent.
- Kelly math example: a 0DTE debit spread risks 1 to make 3 and wins 30% of the time. That is a real, positive edge (EV = +0.2R). Full Kelly size is about 6.7% of equity, and growth is only about 0.64% per trade. Reaching 5x takes about **250 independent trades** at full Kelly. Betting more than Kelly *lowers* long-run growth and moves the account toward ruin.
- The only ways to make 5x likely in one month are (a) a much larger edge than any documented one, or (b) oversizing. Oversizing makes a 5x month *possible* but makes a 100% loss *more* likely than a 5x gain.
- Retail 0DTE long-premium buyers as a group have lost money after spreads and fees (see CBOE and academic studies of 0DTE flows, 2023–2025). On average the edge goes to premium sellers and market makers, who carry the tail risk.

**Design consequence:** the system has two sleeves with hard-separated capital:

| Sleeve | Purpose | Capital | Sizing | Expectation |
|---|---|---|---|---|
| **CORE** | Positive-expectancy, validated edges | ≥ 85% of risk budget | ¼–½ Kelly, ≤ 2% equity risk per trade | Steady compounding; drawdowns survivable |
| **CONVEX** | Lottery-style 0DTE trades that *can* produce 5x+ | ≤ 10% of equity **per month** (hard cap; not refilled after it is spent) | Fixed small premium per trade | Most months lose most of this sleeve; rare months produce large multiples |

A 400% month, if it ever happens, will come from the CONVEX sleeve hitting while CORE is not blown up. The agent must **never** move CORE capital into CONVEX to "catch up".

---

## 1. Universe & Instruments

| Priority | Underlying | Why | Notes |
|---|---|---|---|
| 1 | **SPX (SPXW)** | Daily expirations, cash-settled, European, no early assignment, 60/40 tax (US) | Large notional; use spreads |
| 2 | **XSP** | Mini-SPX, same properties at 1/10 size | Wider relative spreads |
| 3 | SPY / QQQ | Daily expirations, tight markets | American-style, physically settled. **Pin/assignment risk at the close** |
| 4 | Single stocks (high-liquidity only) | Event vol crush | Only for Strategy S6 |

**Defined-risk structures only.** No naked short options in any sleeve. That rules out naked short puts/calls and short straddles. Iron condors, iron flies, verticals and butterflies are allowed.

---

## 2. Required Data Feeds

| Feed | Granularity | Used by |
|---|---|---|
| Underlying OHLCV + VWAP | 1-min (5-sec preferred for execution) | All |
| Option chain: bid/ask/last/IV/greeks/OI/volume, all strikes, 0–7 DTE | 1-min snapshots | All |
| VIX, VIX9D, VIX1D, VIX3M, VVIX | 1-min | S3, S4, regime |
| Economic calendar (FOMC, CPI, NFP, PCE, OPEX, quad-witching) | Daily | Regime/blackout |
| Earnings calendar + historical implied vs realized moves | Daily | S6 |
| Dealer gamma exposure (GEX) estimate | Daily + intraday if available | Regime |
| Historical intraday options data (≥ 2 years, incl. bid/ask) | 1-min | Backtesting |

**GEX note:** a vendor feed is best. If there is none, the agent may estimate it from open interest × gamma × sign convention (assume dealers short puts and long calls). It must flag this as an approximation and must not use it as a sole trigger.

---

## 3. Regime Classifier (runs before any strategy)

Compute at 09:45 ET and refresh every 15 min:

```
regime.vol_level      = bucket(VIX): LOW <15, MID 15-22, HIGH 22-30, CRISIS >30
regime.term_structure = VIX / VIX3M  -> CONTANGO <0.95, FLAT 0.95-1.02, BACKWARDATION >1.02
regime.gamma          = sign(net_GEX) -> POSITIVE (mean-reverting tape) / NEGATIVE (trending tape)
regime.event_day      = true if FOMC/CPI/NFP today, or within 30 min of a scheduled release
regime.implied_move   = ATM 0DTE straddle mid / spot
regime.realized_rv    = annualised 5-min realised vol, trailing 30 min
```

Global blackout rules:
- No new CORE entries in the 10 min before or 15 min after scheduled macro releases.
- No new CORE entries if `vol_level == CRISIS`, except S3, which is designed for that regime.
- No new 0DTE entries after 15:30 ET, except closing trades.

---

## 4. Strategies

Each strategy defines: hypothesis → signal → structure → entry → exits → sizing → validation gate.

### S1 — Intraday VWAP Z-Score Reversion (CORE, 0DTE)

- **Hypothesis:** in positive-gamma regimes, dealer hedging dampens moves. Large intraday deviations from VWAP tend to revert.
- **Filters:** `regime.gamma == POSITIVE`, `vol_level in {LOW, MID}`, `!event_day`, time 10:00–14:30 ET.
- **Signal:** `z = (price - VWAP) / rolling_std(price - VWAP, 30 min)`. Enter when |z| ≥ 2.0 and the last 1-min bar closes back toward VWAP (confirmation; never catch the first bar).
- **Structure:** 0DTE debit vertical in the reversion direction. Long strike about 0.40Δ, short strike at or near VWAP. Target reward:risk ≥ 1.5.
- **Exits:** take profit at 70% of max spread value, or when price touches VWAP. Stop when |z| ≥ 3.0 or the debit loses 50%. Time stop: 45 min, or 15:45 ET.
- **Starting params to optimise:** z_entry ∈ [1.75, 2.5], lookback ∈ [20, 60] min, stop_z ∈ [2.75, 3.5].

### S2 — Opening Gap Fade (CORE, 0DTE)

- **Hypothesis:** moderate overnight gaps without a news catalyst partially fill in positive-gamma regimes.
- **Filters:** gap size between 0.3% and 1.0% of the prior close. No earnings or macro catalyst for the index's top 10 weights. `regime.gamma == POSITIVE`.
- **Signal:** at 09:45, if the first 15-min bar does NOT extend the gap (fails to make a new extreme), fade toward the prior close.
- **Structure:** 0DTE debit vertical. Short strike at 50% gap-fill level.
- **Exits:** target 50% gap fill. Stop on a new high/low of the day beyond the opening 15-min range. Time stop 12:00 ET.

### S3 — VIX Spike Reversal (CORE, 1–7 DTE; not 0DTE)

- **Hypothesis:** volatility spikes mean-revert. Once term-structure backwardation *starts to normalise*, selling the fear premium has positive expectancy.
- **Signal:** all three must hold:
  1. VIX rose ≥ 25% over the trailing 1–5 sessions.
  2. VIX/VIX3M was > 1.05 within the last 3 sessions and is now *falling* (today < yesterday).
  3. SPX closes above its prior session high (first sign of stabilisation).
- **Structure options (agent tests both):**
  - (a) SPX bull put spread, 5–7 DTE, short leg about 0.20Δ, width set so max loss ≤ sizing cap.
  - (b) SPX call debit spread, 3–7 DTE, long about 0.50Δ.
- **Exits:** take profit at 50% of max profit (credit) or 100% gain (debit). Stop if VIX makes a new spike high or the spread hits 2× credit.
- **Warning:** spikes can keep going (2008, Mar 2020). Entering on spike *reversal* rather than spike *level* is mandatory.

### S4 — 0DTE Variance Risk Premium Harvest (CORE, 0DTE, short premium, defined risk)

- **Hypothesis:** 0DTE implied moves often exceed realised moves on quiet, non-event days.
- **Filters:** `!event_day`, `vol_level in {LOW, MID}`, `regime.gamma == POSITIVE`, VIX1D / VIX ≤ 1.0.
- **Signal:** at 10:00–11:00 ET, `implied_move_remaining > 1.2 × forecast_realized_move_remaining`. Forecast with a HAR-RV model or a trailing intraday RV scaled by √time.
- **Structure:** iron condor with short strikes at about 0.10–0.15Δ and wings 10–25 SPX points wide. Credit ≥ 20% of width.
- **Exits:** take profit at 50% of credit. Stop when a short strike's delta exceeds 0.30, or the loss reaches 1.5× credit. Close everything by 15:50 ET. No holding to settlement.
- **Note:** high win rate with fat left tails. A strategy with 90% wins can still have negative EV. Judge it by expectancy and tail loss, not by win rate.

### S5 — Short-Horizon Oversold Bounce (CORE, 1–5 DTE)

- **Hypothesis:** Connors-style short-term mean reversion in broad indices (RSI(2), IBS). It is well documented, but the edge has decayed since publication, so it must be re-validated.
- **Signal:** SPY above its 200-day SMA AND (RSI(2) < 10 OR IBS < 0.15) at 15:45 ET.
- **Structure:** SPX/XSP call debit spread, 2–5 DTE, long about 0.50Δ.
- **Exits:** close when RSI(2) > 65, or the close is above the prior day's high, or after 5 sessions. Stop at 50% of debit.

### S6 — Earnings IV Crush (CORE, event-driven)

- **Hypothesis:** for some stocks, implied earnings moves historically overstate realised moves.
- **Filter:** only names where implied move > realised move in ≥ 70% of the last 12 reports, AND the median ratio of realised to implied ≤ 0.8. Options must be liquid (bid/ask ≤ 5% of mid at ATM).
- **Structure:** iron fly or iron condor centred ATM, wings at about 1.5× implied move. Open at T-15 min before the close pre-report. Close at the next session open + 15 min.
- **Sizing:** ≤ 1% equity max loss per name, and ≤ 4 names per day.

### S7 — 0DTE Trend-Day Convexity (CONVEX sleeve)

- **Hypothesis:** in negative-gamma regimes, dealer hedging amplifies moves. A breakout from a tight opening range can run several implied-move multiples. Cheap OTM 0DTE options then pay 10–50x.
- **Filters:** `regime.gamma == NEGATIVE` OR `term_structure == BACKWARDATION`. The opening-range (09:30–10:00) width must be < 0.6 × implied move.
- **Signal:** a 5-min close beyond the opening range, with volume > 1.5× the 20-day average for that time of day.
- **Structure:** long 0DTE OTM option at about 0.10–0.15Δ, or a long 0DTE debit spread 1 to 2 implied-move-σ away, in the breakout direction.
- **Sizing:** fixed premium = `min(1% equity, remaining_convex_budget / remaining_trading_days_in_month × 2)`.
- **Exits:** sell ⅓ at 3x, ⅓ at 6x, and trail the remainder with a stop at the opening-range midpoint. Unfilled premium expires. Assume a total loss per trade is the *normal* outcome.
- **This is the only sleeve where 5x+ outcomes are realistic, and it is also where losing money is the most likely outcome.**

---

## 5. Risk Management (hard limits; the agent cannot override these)

| Rule | Value |
|---|---|
| Max equity at risk per CORE trade | 2% (default ½ Kelly from validated stats; whichever is lower) |
| Max concurrent CORE risk | 8% of equity |
| Max CONVEX sleeve spend per calendar month | 10% of starting-month equity |
| Daily loss limit (CORE + CONVEX) | 5% of start-of-day equity → flatten all and halt for the day |
| Weekly loss limit | 10% → halt; reduce sizes 50% for next week |
| Monthly drawdown kill switch | 20% from monthly high → halt all live trading; require human review |
| Max position correlation | No more than 2 same-direction SPX 0DTE positions at once |
| Order type | Limit orders only, starting at mid, walking toward the natural price in ≤ 3 steps. Never market orders on options |
| Spread filter | Skip if leg bid/ask > 10% of mid (0DTE) or > 5% (multi-day) |
| Settlement | Close all SPY/QQQ 0DTE positions by 15:50 ET (assignment/pin risk) |
| Leverage | No margin used to increase position counts beyond the above limits |

---

## 6. Validation Gates (strategy must pass ALL before going live)

1. **Backtest** on ≥ 2 years of intraday options data with **real bid/ask fills**. Fills are at the natural price, or at mid ± 25% of the spread, whichever is worse. Fees are included.
2. **Walk-forward:** optimise on a rolling 6-month window, then test on the next 1 month. Report only out-of-sample results.
3. **Minimum sample:** ≥ 100 OOS trades for CORE and ≥ 50 for CONVEX.
4. **Metrics thresholds (OOS):** expectancy > 0 after costs. Profit factor ≥ 1.3. Deflated Sharpe > 0, accounting for the number of parameter combos tried. Max drawdown ≤ 25%.
5. **Stress tests:** replay Aug 5 2024 (VIX spike), Apr 2025 tariff shock, Mar 2020, Feb 2018 (Volmageddon; use SPX EOD data if no intraday). The strategy must not breach the monthly kill switch in any of these.
6. **Paper trading:** ≥ 20 sessions live-paper. Live-paper expectancy must be ≥ 50% of backtest expectancy, or the strategy goes back to research.
7. **Live ramp:** first month at 25% size, second at 50%, then full size, *only if* live stats stay within 1σ of paper.
8. **Decay monitor:** if rolling 50-trade expectancy falls below 0, disable the strategy automatically.

---

## 7. Agent Execution Loop

```
PRE-MARKET (08:30 ET)
  load calendar → set event_day flags/blackouts
  load overnight data → compute gaps, VIX term structure, GEX
  check kill-switch state, sleeve budgets, open positions
  
INTRADAY (every 1 min, 09:30-16:00 ET)
  update regime (every 15 min)
  for strategy in enabled_strategies:
      if regime_filters_pass and not blackout:
          sig = strategy.evaluate(data)
          if sig and risk_engine.approve(sig):
              order = build_order(sig)  # limit, defined-risk
              if config.require_human_approval: queue_for_approval(order)
              else: submit(order)
  manage open positions: profit targets, stops, time stops
  enforce daily loss limit

CLOSE (15:45-16:00 ET)
  flatten all 0DTE SPY/QQQ; flatten SPX 0DTE unless config allows settlement
  
POST-MARKET
  log all trades (schema: TradeRecord) → update rolling stats, Kelly estimates, decay monitor
  produce daily report
```

**Recommended:** set `require_human_approval: true` for at least the first live month. An LLM agent may misread data, and that risk is real in its own right.

---

## 8. Reporting the Agent Must Produce

Daily: P&L by sleeve and strategy, trades taken and skipped (with reason), regime snapshot, and limit usage.
Weekly: rolling expectancy, win rate, avg win/loss, profit factor, and Kelly estimate per strategy. Also slippage vs model, and a decay flag.
Monthly: total return, max DD, and CONVEX sleeve spend vs payoff. Include an honest comparison to the 400% target, stating the distance and *not* proposing to increase risk to close the gap.

---

*This document is research material, not financial advice. Options, and 0DTE options especially, can lose 100% of premium within minutes. Only trade capital you can afford to lose entirely.*
