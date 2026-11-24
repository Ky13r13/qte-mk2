# Low-vol chop-up 0DTE SPX call scalp

**Research record. Pre-registered 2026-11-24, before any results were computed.**
The user's hypothesis: on slow, low-volatility up-chop days, buy the next OTM 0DTE
SPX call after a 30-minute pullback reverses, and take a small, quick gain.

## Data and isolation

- **Underlying:** SPY 5-minute regular-session bars (Alpaca, truth database),
  scaled to SPX by the prior-day SPX/SPY close ratio. Sessions from 2016-01-04 to
  **2025-11-20** only.
- **Holdout:** the locked `SPY:5m:alpaca` holdout (2025-11-21 onward), which
  includes the user's Dec 2025 examples, is **not read**.
- **Pre-open context:** prior SPX close and prior VIX close, giving an
  expected move EM = close × VIX / 100 / √252. The regime label comes from
  `qte.regime_study`.
- **Options are modeled, not quoted.** QTE has no historical SPXW quotes, and the
  Schwab API only gives live chain snapshots. Prices use Black-76 (rate 0
  intraday) with IV = prior VIX × m, frozen during each trade. Time to expiry
  is minutes to 16:00 ET ÷ (390 × 252).

## Primary spec (chosen before testing)

| Element | Rule |
|---|---|
| Day | Pre-open regime `low_vol_trend` (VIX < 15, prior close above the SMA50, SMA20 above SMA50) |
| Intraday calm | At the signal, the session's range so far is ≤ 0.6 EM |
| Window | Signal bar closes 12:00–15:00 ET (9:00–12:00 PT) |
| Signal R5 | The close before the signal bar is ≥ 0.10 EM below the close 30 minutes earlier, and the signal bar closes above the prior bar's high |
| Entry | Next 5-minute bar open. Buy 1 SPXW 0DTE call at the first 5-point strike strictly above spot |
| Exit | First 5-minute close where the modeled mid is ≥ +50% of the entry premium, or 30 minutes, or 15:55 ET. No stop |
| Costs | Buy at mid + $0.05, sell at mid − $0.05, rounded adversely to $0.05; $1.25 fees per contract per side |
| Limit | At most 2 non-overlapping trades per day |

Exits use 5-minute closes only, with no intrabar optimism.

## Robustness grid (reported in full; not used to pick a winner)

- **Signal:** R5, or R30. R30 fires when an aligned 30-minute bar closes red and
  the next aligned 30-minute bar closes above its high.
- **Intraday calm filter:** on or off.
- **Exits:** target +25/50/100%, hold 15/30/60 minutes, stop none or −50%.

That's 72 variants at m = 1.0 and $0.05 half-spread. The primary spec is then
re-priced at m ∈ {0.8, 1.2} and with a $0.10 half-spread.

**Controls:**
1. The same primary rule on all regimes.
2. Random-time call purchases in the same window on the same qualifying days,
   with the same exits (seeded). This tests whether the signal adds anything
   beyond "buy cheap calls on calm days".

## Pre-registered pass criteria (primary spec)

1. Net P&L is positive in **both** dev (≤ 2024-11-18) and validation (2024-11-19 to 2025-11-20).
2. There are at least 100 dev trades, and the dev mean net per trade has t ≥ 2.
3. At least 60% of calendar years are profitable.
4. It beats the random-entry control on mean net per trade.
5. It stays positive at $0.10 half-spread **or** m = 1.2. If it fails both, it's too cost- and model-fragile.

Daily SPXW expirations exist only since 2022 (Mon/Wed/Fri before that), so
results are also reported for 2022-05-11 onward.

## Results (computed 2026-11-24, after pre-registration)

`build/research/lowvol-call-scalp-20261124/report.json`. Sessions 2016-01-04 to
2025-11-20 (2,487), of which 783 were `low_vol_trend`. The holdout was not read.

**Verdict: FAIL. The primary spec fails all five pre-registered checks.**

| | Trades | Mean net per trade | t | Win rate |
|---|---|---|---|---|
| Primary, all | 264 | **−$88.90** | −9.2 | 21.6% |
| Dev / validation | 251 / 13 | −$85.89 / −$147.12 | | |
| Daily-expiry era (2022-05-11 onward) | 65 | −$144.96 | −4.3 | |
| Random-entry control (20 seeds) | | −$73.56 | | |
| Same rule, all regimes | | −$110.87 | | |

Other results:
- **Every year loses money.** The largest losing streak was 34 trades.
- **All 72 robustness variants have a negative mean.** The best is −$44 per trade.
- **Stress tests** (mean per trade): $0.10 half-spread −$98.88; IV × 1.2 −$106.17; IV × 0.8 −$70.08.

**Why it fails** (decomposed by the user's request for quantitative backing):

1. **No directional edge in the signal.** In the 30 minutes after entry, SPX moved
   −0.46 points relative to random entries on the same days (−0.017 EM). It rose
   51% of the time versus 52% for random entries.
2. **Theta dominates.** The next-OTM 0DTE call averaged $5.51, which is near the
   money, not cheap. At a flat price it loses about $70 per contract in 30 minutes.
   Spread, rounding and fees add about $18.
3. **The market doesn't move enough to pay for the volatility priced in (this
   doesn't depend on the option model).**
   - On `low_vol_trend` days, afternoon (12:00–16:00 ET) realized volatility from
     5-minute bars was a **median 0.41× the prior VIX**, and exceeded it on only
     **2%** of days (n = 773).
   - On other days it was 0.48×, exceeding VIX on 5%.

   A long gamma scalp only pays when realized volatility beats the option's implied
   volatility. So this needs real 0DTE afternoon IV below roughly 0.4–0.45× VIX.

**What would change the conclusion:** real quotes showing that afternoon 0DTE SPXW
IV on calm days is far below VIX. The Schwab `optionChain` snapshot includes
per-contract volatility, so the existing recorder can measure this going forward.
The user's December 2025 experience is in the locked holdout and wasn't examined.

**Observation, not a recommendation:** the same numbers favor *selling* premium
on calm afternoons, which carries exactly the news-gap tail risk the user flagged.
That would be a separate pre-registered study needing real quotes and tail analysis.
