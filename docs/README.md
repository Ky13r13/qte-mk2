# QTE documentation map and current capabilities

> One-page version: [START-HERE.md](../START-HERE.md). Pending cleanup: [archive proposal](archive-proposal.md).

**Last verified: 2026-11-24.** This page is the single source of truth for what
QTE can do today. Every change that adds, removes or changes a capability updates
this page in the same change (see `AGENTS.md`). `CONTINUITY.md` holds the
short session handoff, and the documents below hold the details.

## What QTE can do today

| Area | Capability | Entry point | Doc |
|---|---|---|---|
| C++ core | Deterministic event-driven backtest engine: orders, next-open execution, one portfolio ledger, risk, analytics | `qte_core`, `qte.BacktestEngine` | [architecture](architecture.md) |
| C++ futures | Margin schedule, variation settlement, fill admission, `FuturesOrderLifecycle`. **Not yet wired into the engine.** | `include/qte/risk/futures_*.hpp` | [ADR 0013](adr/0013-derivative-research-foundation.md) |
| C++ research sweep | Fast multithreaded single-contract futures screening (σ reversal, opening-range breakout), thousands of specs per second. The calm-condor study uses its own native option kernel | `qte._core.sweep_many`, `python -m qte.calm_condor` | [strategy search](strategy-search.md) |
| Truth database | Every Schwab/Alpaca capture and bar in `data/truth/`, with provenance, provisional and conflict handling, and rejected rows. Intervals: 1m, 5m, 10m, 15m, 30m, 1d, 1w, 1mo (native) plus 1h and 4h (derived) | `qte truth ingest\|derive\|coverage\|collect\|regimes` | [truth database](truth-database.md) |
| Historical data held | Daily: SPX 1970–, ES 1983–, VIX 1986–, SPY 1993–, VIX3M/VIX9D. 5-minute: SPY/QQQ 2016– (Alpaca), SPX/SPY/VIX Mar 2026– and ES May 2026– (Schwab). 1-minute: SPX/SPY/ES from Oct 2026 | `qte truth coverage` | [truth database](truth-database.md) |
| Regime study | Pre-open regime labels (`qte.regime_study`), scored against daily outcomes in expected-move units | `qte truth regimes` | [truth database](truth-database.md#regime-validation) |
| Data-driven regimes | 12 tree-based regimes with ≤ 4 indicators each (VIX term structure, Bollinger, VIX, rv/iv, SMA50 distance), fit 2008–2018 and validated 2019–2025 (R² 0.111 vs 0.045 for legacy labels) | `python -m qte.regime_lab` | [regime lab](strategies/regime-lab.md) |
| Options candidate | Calm-regime 0DTE SPX iron condor: passes its pre-registered checks under the model, break-even at a model multiple ≈ 0.48 (Schwab calendar IV / VIX × 0.432). `--iv-check` measures it; forward gate requires 20 calm opening observations with median ≥ 0.60 | `qte calm-condor observe\|gate-status\|settle` | [calm-regime condor](strategies/calm-regime-condor.md) |
| BTO options (user preference) | Opening-range breakout with 0DTE calls/puts. The original rule **failed** (+$8 per trade, t 0.21). The early-entry variant (by 11:30 ET, 60-minute stop: +$86, t 3.0) is in a **forward paper trial at real quotes** (`qte orb run`, decided after 30 trades) | `python -m qte.orb_options`, `qte orb run\|status` | [ORB BTO options](strategies/orb-bto-options.md) |
| Close measurement | The 15:50–16:00 ET move is 0.86× VIX-implied (1.7× midday; 0.91 since 2022). Direction is ~50/50, with a weak 30-minute momentum tilt. Whether real 15:50 option prices are cheap is not yet measured | descriptive | [last 10 minutes](strategies/close-10min-move.md) |
| Daily runner and report | `qte day run`: one interactive start per day runs the early-ORB trial, the 15:50 close capture and settlement (call, put, both, and the momentum side, at real asks), data collection, then a report: what the day was (trend, reversal, gap, range in σ, last 10 minutes), today's and tomorrow's pre-open regime, and both trials' running totals | `qte day run\|report` | [day runner](../python/qte/day_runner.py) |
| Strategy search | Locked dev/validation/holdout split, every trial logged, deflated-Sharpe leaderboard, holdout run by humans only | `qte search` | [strategy search](strategy-search.md) |
| Signal engine | Causal trend-day and σ-reversal signals on replay or a live recorder journal (Python reference implementation) | `qte signals replay\|follow` | [signal engine](signal-engine.md) |
| Daily brief | A Codex scheduled job (1:30 p.m. PT) captures the session, regime and news, and the GUI publishes it | `qte daily-brief` | [daily brief agent](daily-brief-agent.md) |
| Live market data | Schwab OAuth, read-only REST, SPX/SPXW streaming recorder, Live desk | `qte.brokers.schwab_auth`, `live_recorder` | [broker connections](broker-connections.md), [live desk](live-desk.md) |
| Session read | Schwab capture/replay, frozen regime, sigma, averages, dealer gamma proxy, IV gate and position scenarios | `qte session-read` | [session read](session-read.md) |
| Orders | Tickets are staged locally only. **No order can be transmitted.** | `qte.brokers.staging` | [order staging](broker-order-staging.md) |
| GUI | Local research library, jobs, Daily brief, Live desk, Gamma lab. **System → Schwab token and data** (`/system/schwab`): token status, a private refresh form (key and secret used once, never stored), and a weekly collect button | `qte gui` | [GUI usage](gui-usage.md) |

## Path to live trading (status)

1. Signal engine: **done** (Python reference). Port the live path to C++ when it's wired to execution.
2. Truth database and history: **done**. Needs a weekly `qte truth collect` (Schwab drops 1-minute bars after about 48 days).
3. Strategy search: **one options candidate** (calm-regime condor), with a forward observation and settlement workflow. The gate awaits 20 calm days with median IV ≥ 0.60× prior VIX. Earlier: Round 1 (Nov 24, 1,386 specs) found nothing ready for the holdout. See [search round 1](strategies/search-round1-20261124.md). The pre-registered low-vol 0DTE call scalp failed: [lowvol call scalp](strategies/lowvol-call-scalp.md). Calm-day realized volatility is about 0.41× VIX.
4. Shadow mode on live data: **pending**. Needs SPY/ES volume, a live straddle and VIX/VIX3M in the recorder.
5. Calm-condor shadow settlement through the forward paper ledger: **implemented**. Forward observations are pending. The broker paper coordinator remains separate.
6. Schwab order gateway with hard limits and a kill switch: **not started**. Requires steps 3–5 first.

## Document status

**Current reference.** Keep these accurate and update them with any code change:
[architecture](architecture.md) ·
[strategy search](strategy-search.md) ·
[truth database](truth-database.md) ·
[signal engine](signal-engine.md) ·
[adapters](adapters.md) ·
[research workflow](research-workflow.md) ·
[test build guide](test-build-guide.md) ·
[broker connections](broker-connections.md) ·
[order staging](broker-order-staging.md) ·
[live desk](live-desk.md) ·
[market sessions](market-sessions.md) ·
[daily brief agent](daily-brief-agent.md) ·
[GUI usage](gui-usage.md) ·
[GUI architecture](gui-architecture.md) ·
[Windows GUI](windows-gui.md) ·
[gamma lab](gamma-lab.md) ·
[roadmap](roadmap.md)

**Decisions (ADRs).** Each records a decision and its rationale. Change one only
with a new decision: [ADR index](architecture.md#decisions), 0001–0013.

**GUI feature guides.** These are current: the GUI Library lists them for features that still exist:
[result bindings](gui-result-bindings.md) · [lab views](gui-lab-views.md) ·
[research forms](gui-research-forms.md) · [jobs](gui-jobs.md) ·
[comparisons](gui-comparisons.md) · [test evidence](gui-test-evidence.md) ·
[benchmarks](gui-benchmarks.md) · [export v2](gui-export-v2.md)

**Research records.** These are dated findings and stay valid as records of what
was tested. New results go in new records rather than rewriting old ones:
`docs/strategies/*` ·
[five-agent research](five-agent-research.md) ·
[execution feasibility](execution-feasibility.md) ·
[Robinhood MCP capabilities](robinhood-mcp-capabilities.md) ·
[tick-data acquisition](tick-data-acquisition.md)

**Archive** ([`archive/`](../archive/README.md)). Superseded material, kept for history.
Don't treat it as current:
[development audit (Nov 21)](../archive/docs/development-audit.md) ·
[validation 2026-09-22](../archive/docs/validation-20260922.md) ·
[M13–M16 validation](../archive/docs/m13-m16-validation.md) ·
[GUI G0 preflight](../archive/docs/gui-g0-preflight.md) ·
[GUI restart handoff](../archive/docs/gui-restart-handoff.md) ·
[GUI dependency proposal](../archive/docs/gui-dependencies.md) ·
[early strategy spec](../archive/specs/claude-trading-research/STRATEGY_SPEC.md) ·
three unused example scripts in `archive/examples/`. The
[GUI task log](gui-task-log.md) stays in `docs/` because the daily agent still appends to it.

## Credentials and local state (never commit, never print)

- **Schwab token:** `.cache/schwab/` (ignored). Refresh with `python -m qte.brokers.schwab_auth refresh`. It prompts for the app key and secret, so only the user can run it.
- **Alpaca keys:** `~/.config/qte/alpaca.env`, outside the repo. Load the file only inside the command that needs it.
- **The truth database and all raw market-data captures** live in `data/truth/` (ignored by git). See the [layout](truth-database.md#folder-layout-datatruth-ignored-by-git-never-commit).
- **Research outputs** (`build/search/`, `build/research/`) and **old build or test artifacts** (`build/`) are ignored by git.
