# QTE in one page

## The goal

Get **one** options strategy from "works in the model" to "trading with real money,
safely". The candidate is the **calm-regime 0DTE SPX iron condor**
([details](docs/strategies/calm-regime-condor.md)). Everything else waits.

## Where it stands

| Step | Status |
|---|---|
| 1. Backtest on 10 years of data | ✅ Passed (modeled option prices) |
| 2. **Real-quote check:** 20 calm days with the measured IV score ≥ 0.6 | ⏳ **Now.** Collect one reading per calm morning |
| 3. Paper trading on real quotes | ⏳ Runs alongside step 2 (`settle`) |
| 4. One holdout test (you run it) | After 2–3 pass |
| 5. Live orders through Schwab, with hard loss limits | Not built. Needs your explicit go-ahead |

## The five commands

Run everything from `~/Documents/qte`.

| When | Command | What it does |
|---|---|---|
| Weekly | GUI → System → **Schwab token and data**, or `.venv/bin/python -m qte.brokers.schwab_auth refresh` then `.venv/bin/qte truth collect` | Refreshes the token and downloads all market data into `data/truth/` |
| Each trading morning, 9:55–10:05 ET | `.venv/bin/qte calm-condor observe` | On a calm day, records the real IV reading and the condor QTE would have opened |
| After 16:00 ET | `.venv/bin/qte calm-condor settle` | Settles that paper condor at the close |
| Anytime | `.venv/bin/qte calm-condor gate-status` | Calm days observed, median score, pass/fail against 0.6 |
| Anytime, for your own trades | `.venv/bin/qte session-read --position '1x SPXW 7170P @9.45'` | Today's regime, gamma, levels, and your position's P&L and scenarios |

The GUI (`.venv/bin/python -m qte gui`) shows research, Daily brief and the Live desk.

## Where things live

| Path | Contents |
|---|---|
| `data/truth/` | All market data, the database and raw downloads. Never committed |
| `build/` | Generated outputs and old test runs. Never committed, never deleted without asking |
| `docs/README.md` | Full capability map, for detail |
| `CONTINUITY.md` | Latest handoff between AI sessions |
| `AGENTS.md` | Rules every AI session follows |

## Rules that keep it simple

- **One goal at a time.** A new strategy idea goes through `qte search` or a pre-registered
  study, and only after the current goal is done.
- **One AI per task.** Claude or Codex, not both on the same files.
- **Commit after every green step,** so there's always a clean point to return to.
- **Nothing is deleted without asking,** and old material goes to `archive/`.
- **No live orders** until you approve the gateway.
