# QTE continuity

## Snapshot (2026-11-24)

- **Active goal (user: BTO calls/puts only, no spreads):** the early-ORB forward paper trial. Run `qte day run` each day by 9:40 ET (it also does the 15:50 close study and writes `data/truth/reports/daily/<date>.md`); ledgers are in `data/truth/forward/orb/`, decided after 30 trades. The condor is parked. See `docs/strategies/orb-bto-options.md`.

Capability map and path-to-live status: `docs/README.md`. Read it first.

- **Goal:** live ES/SPX trading. Gates: a search candidate, then the holdout (human-run), then engine futures wiring, then shadow mode, then paper, then the Schwab gateway.
- **Strategy search:** `qte search` runs on the C++ sweep kernel. The SPY:5m:alpaca split is locked: dev ≤ 2024-11-18, validation ≤ 2025-11-20, holdout from 2025-11-21 (humans only). Trials are in `build/search/trials.sqlite3`.
- **Round 1 done** (Claude baseline plus a glass agents run, 1,386 specs; the agent run logs were deleted with `.agentctl/` on 2026-11-24): nothing is ready for the holdout. See `docs/strategies/search-round1-20261124.md`.
  - Best family: ORB with range 2, volume 2×, hold to close (trials 915/1643/2459). It's a real plateau, but it loses in 2016/17/19, and its deflated p is about 0.001.
  - Next: pre-registered H1 (VIX floor) and H2 (shorts barred in low_vol_trend), using the new vocabulary.
- **Regimes:** `qte.regime_lab` builds 12 data-driven regimes (≤ 4 indicators each, fit 2008–2018, validation R² 0.111 vs 0.045 legacy). No indicator predicts direction. Volume indicators are verified correct but uninformative on daily SPX. See `docs/strategies/regime-lab.md`.
- **Options candidate: calm-regime 0DTE iron condor** (`qte.calm_condor`). It passes all pre-registered checks at IV = VIX (+$303 per trade, 860 trades, every year profitable). Break-even is real 0DTE IV ≈ 0.48× VIX; the gate is ≥ 0.6×. **Next: run `qte calm-condor observe` near 10:00 ET for 20 calm sessions.** Schwab IV is calendar time; compare using `*_model_multiple` (= calendar IV / VIX × 0.432). The live reading on 2026-11-24 (an R12 day) was 0.53–0.57: above break-even, below the gate.
- **Session read and forward condor:** Nov 24 replay R12 at 12:20 ET, fixed-strike 0.661× put / 0.600× call. Observer, gate-status and paper settlement are implemented. Zero qualifying sessions so far. See `docs/session-read.md`.
- **Fixes this session:** daily bars are keyed by New York trading date (phantom Sunday 2026-11-22 removed; the truth DB was rebuilt and the old copy kept); MFI can no longer exceed 100.
- **Low-vol 0DTE call scalp** (user hypothesis, pre-registered, `python -m qte.lowvol_call_scalp`): **FAIL**. −$89 per trade over 264 trades. The signal has no direction edge, theta is about $70 per 30 minutes, and afternoon realized volatility on low_vol_trend days is a median 0.41× VIX (above VIX on 2% of days). The open question is real 0DTE IV, which only live Schwab chain recording can answer. See `docs/strategies/lowvol-call-scalp.md`.
- **Truth database** (`data/truth/qte-truth.sqlite3`, captures in `data/truth/captures/`; 1h/4h derived by `qte truth derive`): SPX daily 1970–, ES 1983–, VIX 1986–. SPY/QQQ 5-minute 2016– (Alpaca, matching Schwab to 0.01%). Schwab intraday rolls off, so run `qte truth collect` weekly after the user's `schwab_auth refresh`.
- **Signal engine** (`qte signals`) is the Python reference. Live trend-day inputs (SPY/ES volume, straddle, VIX/VIX3M) aren't in the recorder yet.
- **Futures accounting** exists in C++ (margin, settlement, lifecycle), but `BacktestEngine` wiring is still open.
- **Orders:** staging only. No transmission.
- **Older systems are still valid:** Daily brief (Codex, 13:30 PT); S1/S2/S5/S7 studies; Live desk; forward protocol and paper ledger (built, not locked).
- Two old generated directories returned Linux I/O error 5. Preserve them and the raw captures.

## Done (recent)

- Integrated the four Live desk features and synthetic replay using venv-only optional dependencies.
- Added the locked forward protocol and hash-chained paper ledger. Explicit development/evaluation dates remain unlocked.
- Implemented futures fill admission and the in-process order lifecycle. Native acceptance covers the futures lifecycle.
- Verified 1,421 output checksums and rebuilt the 365-day panel and Book151 export.
- Connected session-bundle data to CLI/GUI and explicit-availability exports.
- Added Theta replay and Schwab partial-update normalization.
- Repaired catalog protection, library links and descriptor cleanup. See `archive/docs/development-audit.md`.

## Working set

- `include/qte/research/sweep.hpp`, `src/research/sweep.cpp`, `bindings/module.cpp` (sweep bindings)
- `python/qte/search.py`, `truth_db.py`, `signal_engine.py`
- `docs/README.md`, `docs/strategy-search.md`, `docs/truth-database.md`, `docs/signal-engine.md`
- `python/qte/forward_collector.py`, `forward_sessions.py`, `forward_protocol.py`, `forward_paper.py`
- `python/qte/session_read.py`, `session_options.py`, `adapters/schwab_session.py`
- `python/qte/regime_lab.py`, `calm_condor.py`
- `docs/session-read.md`, `docs/strategies/calm-regime-condor.md`

## Receipts

- Native condor parity: 38,839 trade comparisons, $0 difference, 860 primary trades. `build/condor-parity-20261124.json`. Full suites: 2,551 Python passed (8 skipped), C++ 22/22.

- S1/S2: `build/core-reversion-20261122/final/`. Two synthetic spread round trips. Six archived dates yielded 17 S1 price setups and zero S2 setups. Historical options P&L remains unknown. 53 focused Python and 20 native tests passed.
- S7: `build/s7-20261122/final/observed/report.md` and `final/demo/report.md` under the same run root. Six archived dates, 36 volume-confirmed closes on Nov 20, zero verified historical option trades.
- S5 on 30 SPY sessions: `build/s5-20261122/report.md`. Seven signals, six completed event exits; observed SPXW spread P&L is pending matched quotes.

- Live desk demo at `build/live-desk/demo/`: 2 paper fills / 4 synthetic probes, $730 displayed liquidation. 108 focused Python, 20 native and 65 JavaScript checks passed.

- Release checks: 2,275 Python passed, 8 Windows-only skipped; 65 JavaScript passed (Nov 21). Native: 20/20 on Nov 22.
- Audit evidence and build logs: `build/audit-tests-KEldvc/`.
- Rebuilt annual panel: `build/audit-tests-KEldvc/year-replay/`; preserved archive: `build/daily-regimes/year-20261120/`.
- Verified chart copy: `build/audit-entry-export-gIzjzO/chart/`.
- Quote replay: `build/quote-replay-audit-20261121/report.md`.
- Historical Book151 screens: `build/book151-schwab-timeframes-20261121/` and `build/book151-schwab-daily-20261121-final/`.
- Historical timing and risk: `build/spx-0dte-timing/final/` and `build/spx-0dte-timing/exploratory-attribution/`.
- Schwab raw REST captures: `build/schwab-pilot-20261121/`.
- Market coverage and synthetic agent results: `build/schwab-minute-refinement-20261121-final/`, `build/options-master-20261121-final/`.
- Git history and remote operations remain user-owned.
