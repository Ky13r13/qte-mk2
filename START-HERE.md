# QTE in one page

## The goal

Get **one** buy-to-open SPXW strategy (calls/puts only, no spreads) from "works in the model"
to "trading with real money, safely". The current candidate is the **early opening-range
breakout** ([details](docs/strategies/orb-bto-options.md)). The calm-regime condor
([details](docs/strategies/calm-regime-condor.md)) is parked because it uses spreads.

## Where it stands

| Step | Status |
|---|---|
| 1. Backtest on 10 years (modeled prices) | ⚠️ The original rule failed; the early-entry variant is a lead (+$86 per trade, t 3.0) |
| 2. **Forward paper trial at real quotes, 30 trades** | ⏳ **Now.** Run `qte day run` each trading morning |
| 3. One holdout test (you run it) | After step 2 passes |
| 4. Live orders through Schwab, with hard loss limits | Not built. Needs your explicit go-ahead |

## The commands

Run everything from `~/Documents/qte`.

| When | Command | What it does |
|---|---|---|
| Weekly | GUI → System → **Schwab token and data**, or `.venv/bin/python -m qte.brokers.schwab_auth refresh` then `.venv/bin/qte truth collect` | Refreshes the token and downloads all market data into `data/truth/` |
| **Each trading day, start by 9:40 ET** | `.venv/bin/qte day run` | Asks once for the app key and secret (memory only) and runs the whole day: the early-breakout paper trial, the 15:50 close capture and settlement, the data download, then **the daily report** in `data/truth/reports/daily/<date>.md` |
| Anytime | `.venv/bin/qte orb status`, `.venv/bin/qte day report --date YYYY-MM-DD` | Paper-trial totals and decision rule; rebuild any day's report |
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
