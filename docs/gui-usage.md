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
G5a adds an authenticated offline single-run job API. G5b.2 adds form metadata,
read-only previews, immutable inline submission and fixed synthetic lab workers.
G5b.3 forms and job views are available in the local test build. Start with the
[data-flow and strategy guide](test-build-guide.md). Extended browser/accessibility
acceptance remains separate from the passing automated checks.
G6a adds **Data → Validate local dataset** for CSV mappings and saved Alpaca
inputs. Preview, launch, then explicitly register the validation output. Its
detail offers **Use matching source in run**, preserving mapping and checksum;
changed/deleted original sources must be validated again. Data validation does
not establish authenticity, exchange-session coverage or trading merit.
G6b adds **Data → Prepare Alpaca download**: select symbols, timeframe, UTC
window, entitled feed and action-free declaration, then preview offline.
Downloads stay blocked with the normal launch command. A separately approved
online session may start with `--allow-alpaca-downloads` and server environment
variables `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`; never put credentials in
forms or configuration files. No online checks were performed for this build.
Explicitly registered download artifacts show cache lineage and checksums.
Copy their `bars.json` into an allowed repository data directory and validate it
before research; job-private paths are not general input paths.
G7a provides the offline [comparison command](gui-comparisons.md) for fixed SMA
comparisons and frozen candidate experiments. G7a.2a allows explicit registration
and verified file access in Research, with raw test/mixed files protected.
G7a.2b opens candidate/window views and explicit train/validation comparisons;
open a row for full details. Promotion eligibility and final-test facts remain
withheld. **Research → New comparison** now supports fixed SMA and frozen
candidate experiments using registered validation IDs, explicit evidence
declarations, preview then launch. Advanced parameters and separate cost profiles
are editable. Register successful output explicitly. The
[comparison contract](gui-comparisons.md) documents the guarded lifecycle and
same-request retry behavior. Both workflows passed local synthetic browser runs;
this is not evidence of historical performance.
G7b adds **Macro router** to **Research → New run**. Supply a bounded explicit
profile, preview then launch; the registered run's **Macro routing** table shows
transitions and complete as-of input records. See the [profile example and
rules](strategies/macro-router.md#gui-and-cli-profile). GUI integer fields inside
the profile are quoted decimal strings; CLI config uses native integers.
Missing/stale inputs block entries, dealer mode defaults to ignore, and proxies
never qualify as positioning. The comparison registry remains SMA/six families.
Consult the
[checkpoint](gui-task-log.md) for the current tested milestone. Downloads,
test-execution and broker requests never start merely by opening a page.

The interface uses neutral colors, square corners, local assets and native
keyboard controls. It starts in Library rather than a performance dashboard.
Documents are read-only; an architecture target or narrative test note is not a
machine-verified pass result.

**System → Connections** shows offline prerequisites for Schwab and Robinhood
Crypto. Environment-key presence is not credential validity or connectivity;
values are not inspected. Both remain blocked, with no account-read controls or
response storage. Details and the separate approval/setup boundary are in
[broker connections](broker-connections.md#local-gui-offline-prerequisite-inspection-g9a).

## Offline job API

The [job contract](gui-jobs.md) documents the fixed `research_run` API: explicit
configuration-path submission, durable retry identity, one active job, bounded
FIFO queue, status/log reads and cancellation. It uses the existing `.venv`,
copies inputs into a fresh job directory and invokes the ordinary v2 CLI. It
does not download data, contact brokers, run arbitrary commands or auto-register
results. Known protected inputs require prior explicit disclosure; derived
outputs remain protected. Restarted jobs are interrupted, not silently retried.

The current runner requires Linux process supervision. No job launches merely
by browsing a page. Research now offers **New run** and **New synthetic lab**.
Preview assumptions before launch; edits invalidate the preview. Recent jobs
retain failures and support pagination. Job detail offers bounded live updates,
cancellation and explicit registration after success. Unknown submission results
retain the same request/key for retry; check recent jobs before explicitly
starting another run. The terminal `qte run` workflow remains available.

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
neither bytes nor authorization. The job API accepts either the unchanged legacy
configuration-path request or the exact inline request with this fingerprint.
Inline submission reauthorizes and compares freshly captured inputs before full
validation; stale inputs return 409 and require another preview. Successful jobs
still require explicit artifact registration. Lab runs are synthetic mechanics
only; they do not select candidates or establish trading profitability.

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
observed dealer positioning. Job forms are available in the G5b.3 test build.

## Running and inspecting tests

Open **System** and choose **Run Python tests** or **Run C++ tests**. These fixed
commands use `.venv` and the existing `build/local-dev` binaries; they do not
install or compile anything. Build C++ first when needed. The GUI timeout is
300 seconds. Opening System does not run tests. Use Refresh for progress or
Cancel for a queued/running job; uncertain submissions keep the same retry key.

Select a job, then explicitly reveal its evidence to see counts and paged case
details. Reveal is durably recorded outside protocol because test output may
contain sensitive text. Raw logs/JUnit remain local and are not downloadable.
Failed/skipped cases stay visible; partial records are not promoted to passes.
**Observe current identity now** compares the explicit file scope at that moment,
not continuously. It never proves binaries came from current source. See the
[test-evidence contract](gui-test-evidence.md) for exact limits and API details.

## Measuring replay speed

Choose **System → Benchmarks** for an explicit synthetic no-op or SMA workload,
from 1 to 100,000 bars (default 10,000). Refresh for status; cancel a queued/running
job using the existing controls. Timing is one sample without warm-up, not
strategy performance. No-op means no trades, not buy-and-hold.
Verified load/replay/throughput figures appear with secondary metadata collapsed.
Raw logs remain private; no build, install or network request occurs. See the
[benchmark contract](gui-benchmarks.md) for workload and provenance limitations.

## Session and storage

This server binds only to IPv4 loopback. Local HTTP is an explicit deployment
assumption; do not expose it through a proxy, tunnel or LAN binding. The browser
gets an HttpOnly, SameSite=Strict session cookie and a separate in-memory request
token. Logout, restart or 30 minutes of inactivity invalidates the session.
After logout/expiry the new code is printed only in the terminal. Local GUI login
does not use account credentials, remote resources or telemetry. This cannot protect
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
