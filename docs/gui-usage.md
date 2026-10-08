# Local QTE library

From the repository root, using the existing virtual environment:

```sh
.venv/bin/python -m qte gui
```

Open `http://127.0.0.1:8765/library` and enter the one-time code printed in that
terminal. Leave the terminal running; Ctrl-C stops the server. An occupied port
is an error, not an automatic fallback; select `--port 8766` explicitly if needed.
The pinned optional GUI dependencies have already been installed in this checkout.
The command never installs dependencies itself.

G1 provides searchable original documentation with source hashes and status.
G2 provides explicit artifact registration, verification and guarded downloads;
G3a single-run charts/tables and G3b owned-result bindings are reviewed.
G3c richer exports/views are implemented, regression-tested and reviewed.
G4 adds role-filtered candidate, window, comparison, macro and regime views.
G5a adds an authenticated offline single-run job API. G5b.2a adds form metadata
and a read-only validation preview API; browser forms remain planned work.
Consult the
[checkpoint](gui-task-log.md) for the current tested milestone. Later job, data,
test-execution and broker controls are not enabled merely by opening a page.

The interface uses neutral colors, square corners, local assets and native
keyboard controls. It starts in Library rather than a performance dashboard.
Documents are read-only; an architecture target or narrative test note is not a
machine-verified pass result.

## Offline job API

The [job contract](gui-jobs.md) documents the fixed `research_run` API: explicit
configuration-path submission, durable retry identity, one active job, bounded
FIFO queue, status/log reads and cancellation. It uses the existing `.venv`,
copies inputs into a fresh job directory and invokes the ordinary v2 CLI. It
does not download data, contact brokers, run arbitrary commands or auto-register
results. Known protected inputs require prior explicit disclosure; derived
outputs remain protected. Restarted jobs are interrupted, not silently retried.

The current runner requires Linux process supervision. No job launches merely
by browsing a page. There are no launch buttons yet; the terminal `qte run`
workflow remains available while [G5b forms](gui-research-forms.md) are built.

## Research preview API

Authenticated clients can read `GET /api/v1/research/form-options` and submit
`POST /api/v1/configs/validate` using the fixed [form contract](gui-research-forms.md).
The preview accepts one registered strategy on a local CSV/saved Alpaca input,
or a fixed synthetic lab configuration. It shows validated configuration and
execution/risk/research assumptions without running a backtest or creating a job.
It checks known input protection before returning validation details, never
records a disclosure, and makes no claim that unknown data is untouched.

The request is bounded to 16 KiB; captured research data is bounded to 16 MiB.
Simulation integers are exact decimal strings, not browser-rounded numbers.
The preview fingerprint records captured content/configuration, but reserves
neither bytes nor authorization. Inline submission and lab workers are a later
backend step; the existing job API still accepts only a configuration path.

## Inspecting a recorded run

In Research, explicitly register a repository-relative export directory such as
`build/trend-reference-20260921`. A verified, unblocked single-run export opens
one equity chart and the recorded return/drawdown. Invalid or protected exports
retain their catalog diagnostics and disclosure controls instead.

Metrics, orders, fills, equity records, provenance and files have separate views.
Tables show at most four default columns; open a record to see every source
field. Timestamps and identifiers retain their exact decimal text. Event and
sampled equity are distinct; unavailable sampled data is labeled not recorded.
Expand the chart range to enter inclusive UTC-nanosecond bounds. This changes
the display only, never the full-run metrics or recorded sampling convention.
Charts retain duplicate-time rows and declared gaps, with bounded display-only
reduction; exact rows and authorized file downloads remain available.

Legacy v1 files do not contain event sequence, detailed trade episodes, position
snapshots or order-event history. Those fields stay explicitly not recorded;
the opt-in v2 format exports existing owned results without rebuilding accounting
from CSVs:

```sh
.venv/bin/python -m qte run --config examples/research-run.json --output build/my-owned-run --export-version 2
```

Use a new output directory; existing exports are never overwritten. Default
exports remain v1. V2 adds focused positions, closed/open trades and order-event
views, plus exact event sequences. Positions contain inventory and valuation
marks, not position cost basis or PnL. Carried sampled values retain their source
event sequence. See the [format contract](gui-export-v2.md).

## Inspecting a recorded strategy lab

Explicitly register its export directory in Research. A verified lab opens the
recorded candidate slate, including exclusions and benchmarks, without ranking
by return. Choose a timeframe, then a train/validation window for Comparison,
Macro inputs or Regime decisions. Full records retain failures and missing facts;
independent windows are never stitched into a portfolio curve. `daily_24h` is a
synthetic clock, not a US exchange session.

Test-role structured views remain withheld even after a raw-file disclosure.
Mixed raw files retain their separate confirmation requirement. Synthetic prices
and scripted macro inputs are mechanics evidence, not validated trading alpha or
observed dealer positioning. Job forms are G5 work, not yet available.

## Session and storage

This server binds only to IPv4 loopback. Local HTTP is an explicit deployment
assumption; do not expose it through a proxy, tunnel or LAN binding. The browser
gets an HttpOnly, SameSite=Strict session cookie and a separate in-memory request
token. Logout, restart or 30 minutes of inactivity invalidates the session.
After logout/expiry the new code is printed only in the terminal. No account
credentials, remote resources or telemetry are involved. This cannot protect
against a compromised process running as the same local user.

Artifact registration is explicit and repository-relative. It does not scan
`build/` or modify a legacy export. Catalog metadata lives in
`.cache/qte-gui/catalog.sqlite3`; append-only disclosure records live separately
under `build/gui/disclosures/`. Do not delete disclosure records to try to reset a
holdout. Missing history is unknown, never proof that data was untouched.

Checksum verification establishes file consistency, not profitability, provider
authenticity or preregistration. Synthetic results remain software/mechanics
evidence. Raw held-out, mixed-role and unclassified contents require a separate
explicit disclosure confirmation. Direct filesystem inspection is outside the
GUI's control. Verification can append protective-content classifications so
renamed/copied bytes inherit protection. This internal safety bookkeeping is
not a disclosure event and does not change the source export.

## Validation notes

Use `.venv/bin/python -m pytest tests/python` and the normal `build/local-dev`
CMake/CTest presets. In the Codex sandbox, Python's thread-to-event-loop socket
wakeup is restricted: async catalog tests require scoped execution approval.
A trivial `asyncio.to_thread` diagnostic fails inside that sandbox and succeeds
outside it; this is not a reason to remove the tests or install new dependencies.

G1 browser checks cover sign-in, literal search, documentation, metadata, skip
navigation, restart invalidation and a 390px layout. Explicit 200% browser zoom
remains a manual check; source-level frontend checks do not replace browser QA.
G3a browser checks cover real exported run selection, event/sampled switching,
metrics with undefined reasons and sampling policy, complete fill records,
exact chart ranges, corrupt/protected catalog fallback and narrow chart layout.
Authenticated download bytes are tested through HTTP; the native browser
save-file flow has not been exercised. See the checkpoint for commands/results
and subsequent milestone status.

G3c browser checks additionally cover v2 overview/sequence labels, positions with
mark sequence, complete closed-trade details, empty open-trade tables, lifecycle
history, and sampled source-event labeling. All browser checks use a separate
QA catalog, not user research artifacts.
