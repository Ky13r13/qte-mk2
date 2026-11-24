# Daily brief agent

Open **Research → Daily brief** in the local QTE GUI. Select a session to read the news, the regime known at the open, its rationale, the observed outcome and market coverage. The range graphic uses the prior close and prior VIX to express SPX moves in daily expected-move units. Expand **Data and provenance** for the source summary, its checksum and its archive location.

The Codex heartbeat `qte-daily-regime-evidence` wakes at 1:30 p.m. Pacific daily. Codex provides scheduling, news research and the authenticated market-data tools. QTE owns session selection, evidence publication and the archive view. The GUI can be closed during collection. A future run depends on the local scheduler, workspace and data connections being available.

## Daily operating procedure

1. In the checkout, read AGENTS.md and the latest daily entry in docs/gui-task-log.md. Run `.venv/bin/qte daily-brief --due`. This checks completed XNYS sessions, including early closes, against the published archive. If a completed capture already exists on disk, publish it first and check again. Process missing dates oldest first. On a closed day with no backlog, finish quietly.
2. Use the authorized Robinhood market-data tools first. Capture daily SPX, VIX, SPY, QQQ and IWM, and the finest practical intraday SPX/SPY/QQQ series. Preserve request windows, retrieval timestamps, raw responses, adjustments, exclusions and hashes. Add futures and option data only with verified source coverage. Store each attempt under a fresh `build/daily-regimes/YYYY-MM-DD/<capture-id>/` directory. Use clock-sortable UTC capture IDs for revisions. Preserve earlier captures.
3. Use prior-session evidence and the existing `qte.regime_study.features` rules to record the pre-open label and reason. Compute returns, gap, ordered drawdown, realized volatility, prior-VIX expected-move excursions, moving-average distances and same-clock relative volume wherever supported. Describe the observed close separately. Identify provisional bars, incomplete intervals and provider discrepancies. Any suggested revision belongs in a separate field. No automatic refits, trade selection, orders or broker changes.
4. Research that day’s material macro, policy and company news. Prefer timestamped issuer, agency and central-bank releases. Record what was announced, when it became public, a primary source URL and why the timing might matter. Flag attribution as a hypothesis when the data establish only coincidence. If a search finds no verified material event, record that result. Keep date-only items out of intraday signal inputs.
5. Write a concise report and normalized `summary.json` next to the raw evidence. New summaries use the following fields. The publisher also reads the original November 20 capture format.

| Field | Expected contents |
|---|---|
| session | Eastern trading date matching the archive directory |
| prior_known_regime | Object with regime, reason and asof (an earlier session date). Use unclassified and null asof if history is unavailable. |
| retrospective_outcome | Short plain-language description of the completed session |
| events | Array of objects with event, time (ISO timestamp with offset) or date, source (HTTPS primary release), and optional attribution. An empty array records no verified events. |
| metrics | Object keyed by instrument. Include last, return_pct, gap_pct, observed, expected, interval_seconds when known. Use null for unavailable values. Percentages are percentage points. Preserve additional research metrics in the object. |
| sigma | SPX low, high and last in units of the prior expected daily move, when measured |
| daily_status, futures, options | Data publication status and instrument-specific coverage or blockers |
| provenance | Capture request and retrieval times, file hashes or reference to a local manifest, price basis, and excluded observations |

6. Run `.venv/bin/qte daily-brief` to publish. It preserves separate captures, checks the prior regime date and publishes the index after the individual records. Verify that the session appears in `build/daily-briefs/index.json` and is cleared from `--due`. Append the outcome and report/chart links to the existing task log. Keep raw captures and credentials out of chat. If the feed is inaccessible, record the failed attempt and blocker separately and leave the session due. If a day has some usable observations, publish the verified coverage and identify what is missing. Backfills of provisional evidence get a new capture and retain the original regime.

For manual publication or recovery, run both commands from the QTE checkout. They use local data and the repository venv. The archived November 23 report under `build/daily-regimes/2026-11-23/` is a worked example.
