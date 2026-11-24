# Calm-regime 0DTE SPX iron condor

**Research record. Pre-registered 2026-11-24, before any strategy results were
computed.** The idea comes from [regime lab](regime-lab.md): realized range is
predictable relative to VIX, but direction is not. In the calm regimes
R01–R03 the next session moves about 0.7σ, and closes beyond 2σ happen about 0.4% of the time.

## Data

- **Underlying:** SPY 5-minute regular-session bars (Alpaca), scaled to SPX by
  the prior SPX/SPY close ratio. Sessions 2016-01-04 to 2025-11-20.
  - Dev: through 2024-11-18.
  - Validation: 2024-11-19 to 2025-11-20.
  - Also reported: 2019 onward, which is outside the regime fit.
- **The holdout is not read.**
- **Options are modeled:** Black-76 per leg, rate 0, IV = prior VIX × m, flat
  across strikes, time = minutes to 16:00 / (390 × 252). There are no historical
  SPXW quotes.
- **Real-quote reference (Schwab chain, 2026-11-24, after hours):**
  - the expiry settling that day had ATM IV 1.37× VIX (calendar-time IV, including overnight risk; see the correction under Results),
  - the 24-day expiry had 0.81× VIX.

## Primary spec

| Element | Rule |
|---|---|
| Day | `vix_ts ≤ 0.9154` and `bb_pctb > 0.7202`. This is the R01–R03 parent node, using 2 indicators |
| Entry | 10:00 ET at the open of the 10:00 bar. Sell 1 iron condor on 0DTE SPXW |
| Strikes | Remaining expected move EMr = S × IV × √(minutes left / (390 × 252)). Short put = the 5-point strike at or below S − 1.0·EMr. Short call = the 5-point strike at or above S + 1.0·EMr. Wings are 25 points beyond each short strike |
| Exit | Hold to cash settlement at the 16:00 close. No stop |
| Costs | Each leg $0.05 worse than mid, rounded adversely to $0.05. $1.25 fee per leg at entry. No exit fees at settlement |
| Size | 1 contract (multiplier 100). Maximum loss is the wing width minus the credit |

## Robustness grid

The grid is reported in full and is not used to pick a winner:
- short distance k ∈ {0.75, 1.0, 1.25} × EMr
- wing width ∈ {10, 25, 50} points
- structure: iron condor, or put credit spread only
- stop: none, or close everything at the first 5-minute close beyond a short strike

That's 36 variants.

**Stress tests on the primary spec:** IV multiplier m ∈ {0.8, 1.2}, a $0.10
half-spread, and entry at 12:00 instead of 10:00.

**Controls:**
1. The primary spec traded every day, with no regime filter.
2. The primary spec traded only in the high-range regimes, R07–R12.

## Pass criteria (primary spec)

1. Net P&L is positive in **both** dev and validation, and also positive for
   2019 onward (outside the regime fit).
2. There are at least 150 dev trades, and the dev mean per trade has t ≥ 2.
3. At least 70% of calendar years are profitable.
4. Mean per trade beats the every-day control, and the high-range control's
   mean is lower than the primary's. This shows the regime filter matters.
5. It stays positive at **m = 0.8** (less credit) **and** at a $0.10 half-spread.
6. Tail risk: the worst day is ≤ 1 max loss (it must be, by construction), and
   max drawdown is ≤ 25% of total net profit over the sample.

## Results (computed 2026-11-24, after pre-registration)

`build/research/calm-condor-20261124/report.json`. Sessions 2016-01-04 to 2025-11-20.

**At the pre-registered model settings (IV = 1.0× VIX), the primary spec passes all six checks.**

| | Trades | Mean per trade | t | Win rate |
|---|---|---|---|---|
| All | 860 | +$302.73 | 34.8 | 97.7% |
| Dev / validation | 782 / 78 | +$283.21 / +$498.41 | | 97.4% / 100% |
| Since 2019 (outside the regime fit) | 559 | +$377.25 | | |

- **Every year 2016–2025 is profitable.**
- **20 losing days out of 860.** The worst was −$2,100 against a structural maximum of −$2,410.
- **Max drawdown is $2,494, on $260k net.** The mean credit was $3.37.
- **Controls** (mean per trade): every day +$274.75; high-range regimes R07–R12 +$256.68.
- **Stress** (net): IV ×0.8 +$191k; IV ×1.2 +$308k; $0.10 half-spread +$243k; 12:00 entry +$220k.
- **Grid:** all 36 of 36 variants are positive in dev, validation and since 2019.

**The result depends on one unmeasured input: the real 0DTE IV relative to VIX.**

| IV as a multiple of VIX | Calm regimes, mean per trade | Years profitable | Max drawdown | Every day, mean |
|---|---|---|---|---|
| 1.0 | +$303 | 100% | $2.5k | +$275 |
| 0.8 | +$222 | 100% | $3.0k | +$154 |
| 0.7 | +$169 | 100% | $3.3k | +$74 |
| **0.6** | **+$101** | **100%** | **$5.2k** | **−$24** |
| 0.5 | +$15 | 50% | $27k | −$144 |
| 0.4 | −$97 | 20% | $92k | −$300 |

**Break-even is about 0.48× VIX.** The regime filter matters most when IV is lower:
at 0.6× VIX the calm regimes make money every year while trading every day loses.
The model also uses flat IV, so it ignores skew. In the real market, OTM puts carry
higher IV (more credit) and OTM calls carry lower IV.

**First real-quote reference** (`qte.calm_condor --iv-check`, Schwab chain captured
after hours on 2026-11-24, VIX 26.6, not a calm-regime day):
- 0DTE ATM IV was 1.37× VIX,
- the short-put strike was 1.55× VIX,
- the short-call strike was 1.19× VIX.

These quotes include overnight risk, so they overstate intraday IV. They're still
far above the 0.48× break-even.

**Correction (2026-11-24, 12:30 ET).** The comparison above is wrong because the
units differ. Schwab quotes option IV on **calendar time** (minutes / 525,600), but
this study scales VIX on **trading time** (minutes / (390 × 252)). Verified on live
SPXW 0DTE quotes: calendar-time Black-76 matches the market mids, while trading time
is off by 3–7×. On the study's scale, the real multiple is calendar IV / VIX × 0.432.
- The after-hours figures (1.37×, 1.55×, 1.19×) translate to roughly 0.5–0.7×, and
  they include overnight risk, so they don't apply to an intraday entry.
- **Live 0DTE chain on 2026-11-24 at 12:20 ET** (an R12, high-volatility day; VIX
  27.07; 218 minutes left): ATM model multiple **0.53**, short put 0.57, short call
  0.54. That's above the 0.48 break-even and **below the 0.6 gate**.

`--iv-check` now reports `*_model_multiple` (regression test in `test_calm_condor.py`).
The gate still requires the median model multiple on calm-regime days to be ≥ 0.6.
On today's evidence that is **not yet established**.

**Status:** viable under the model and robust down to about 0.6× VIX. It isn't
cleared for live trading until the real 0DTE IV is measured at 10:00 ET on calm
days. Next:
1. Run `--iv-check` on the 10:00 ET chain for at least 20 calm-regime sessions.
2. Require the median short-strike IV to be at least 0.6× VIX.
3. Paper trade on live quotes.
4. One human holdout run.
5. Then a Schwab gateway with a hard per-trade loss cap equal to the wing width.

## Forward paper protocol · Pre-registered November 24, 2026

Use the [session read rules](../session-read.md) and the same primary strike selection as the research study. Accept one immutable observation per XNYS cash session, including explicit token-expiry and non-calm skips. The 09:55–10:05 Eastern window applies to the chain receipt time. A lower short-strike model multiple of at least 0.60 on the median of 20 or more calm observations passes the gate. Store a would-be one-lot trade when all four books are fresh and the net bid/ask credit lies strictly between zero and the wing width. Paper settlement compares the quoted credit with the modeled credit on those same strikes. Charge $1.25 per leg on entry and settle against the final complete cash-session SPX five-minute bar. A missing final bar leaves settlement pending.

For native parity, require the same trades and per-trade P&L within one cent on all pre-registered primary and robustness variants over the locked 2016–2025 dev/validation study. Settlement loss is capped by width minus credit plus entry fees. A simulated stop incurs an exit spread and exit fees, so the executable bound additionally allows the exit spread and adverse rounding on all legs.

### Run the forward trial

From the checkout, refresh the Schwab token interactively before 10:00 Eastern. Schedule observation on a host with timezone America/New_York. This cron example uses an Eastern-time crontab.

```cron
0 10 * * 1-5 cd /home/glsu6/Documents/qte && .venv/bin/qte calm-condor observe
```

`qte calm-condor observe` appends to `data/truth/forward/calm-condor.ndjson`. Each scheduled session is recorded once. `qte calm-condor gate-status` summarizes eligible records. Add `--replay DIR` to inspect an archival capture without adding it to the tally. After the truth database contains the final SPX five-minute bar, run `.venv/bin/qte calm-condor settle`. It appends the quote-based and model P&L to the study’s existing forward-paper ledger format at `data/truth/forward/calm-condor/paper-ledger.ndjson`. Both commands verify the hash chain before appending. The observer records expired-token skips without interaction.

The November 24 replay contributes zero calm observations. It was R12 and received at 12:20 Eastern. The forward gate awaits 20 qualified calm observations. The paper calculation is a snapshot-based comparison with no broker orders.

### Native simulator parity · November 24, 2026

The historical study now uses the C++ condor kernel through pybind11. The Python trade-day implementation remains the reference. Across 43 variants and controls through November 20, 2025, the implementations matched 38,839 trade comparisons with zero P&L difference. The primary reproduced 860 trades and $260,349.89 modeled net P&L. Settlement, stop execution and loss bounds have native unit coverage. The receipt is `build/condor-parity-20261124.json`.
