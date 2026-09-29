# QTE local research library

Status: architecture approved for incremental implementation. As of 2026-09-27,
G0 dependencies are installed, G1/G2/G3a are reviewed, and G3b is underway. See the
[active checkpoint](gui-task-log.md) for tested capability and remaining work;
later milestones below remain targets, not claims of shipped functionality.
The user requested a locally hosted, minimally colored, square-cornered interface
covering existing functionality, documentation and test artifacts. This is a
presentation/orchestration layer, not a new trading engine. Read with
[ADR 0010](adr/0010-local-research-ui.md), [core architecture](architecture.md)
and [development rules](../AGENTS.md).

The [2026-09-23 G0 preflight](gui-g0-preflight.md) records the first-screen contract
and an earlier SSL integrity failure. The [post-boot proposal](gui-dependencies.md)
records passing environment rechecks, resolved locks and contract examples. G0
approval was subsequently granted for the locked packages and GPT-5.6 Sol/Terra
builders under GPT-6 Astra review.

## 1. Product and visual contract

Working name: **QTE Library**. Open on documentation, not a dashboard. Four text
navigation items: **Library / Research / Data / System**. Each route has one
main view; opening an item replaces its list, with a breadcrumb back. No permanent
three-pane layout, KPI wall, news feed, ticker tape, decorative icons or animation.

- Neutral light theme initially: background `#FAFAF8`, text `#202020`, secondary
  text `#595959`, rules `#D6D6D2`. No semantic red/green PnL colors. Selected items
  use an underline or neutral inversion; status always has a word, not just color.
- All authored corners have radius **0**: fields, buttons, menus and panels.
  No cards, pills, gradients or shadows. Thin separators only where needed.
- System sans-serif, 14–16 px body, 12 px minimum secondary text, tabular numbers;
  readable line length about 76 characters. No remote fonts/assets.
- One primary action per view. At most four default table columns. Show 25 rows
  per page, with count and pagination; filtering never silently hides failures.
- Default run detail: title, one concise evidence/status line, equity chart,
  total return and maximum drawdown in one text line. Other metrics, orders,
  fills, provenance and assumptions are separate detail views, not stacked panels.
- Essential warnings remain visible: synthetic/unknown evidence, invalid export,
  unsupported execution, and uninspected holdout are not buried in disclosures.
  Secondary configuration fields and full provenance start collapsed.
- Keyboard-accessible native controls, visible focus, associated labels, skip
  link and semantic tables. Test 200% zoom and narrow screens; navigation wraps,
  only wide data tables may scroll horizontally. No hover-only actions.

Illustrative structure (not a running interface or a result screenshot):

```text
QTE       Library   Research   Data   System             Search
-------------------------------------------------------------
Research / strategy-lab-20260922 / hourly
Synthetic mechanics only · no selection · holdout not evaluated

Candidate                Base return   Stress return   Result
trend-ungated             [from report] [from report]   Excluded
...

25 of N rows                                      Previous Next
```

The comparison screen requires a selected window before showing per-window
returns. Its default overview shows candidate, eligibility, validation score and
reason; it never invents one portfolio return for independent windows.

## 2. Navigation and coverage

Documentation remains in repository Markdown, not a parallel CMS. Search covers
approved documents, strategy names, runs and tests, with type filters on demand.
Headings get stable prefixed anchors. Pages show their source path, content hash
and implementation status; an ADR describing a target is not proof it shipped.
Related links connect a strategy specification, its configuration, experiments,
test evidence and limitations. No inferred pass badge merely because a test file
exists. New built-ins add registry metadata, not strategy branches in C++.

| Existing capability / source | GUI destination and operation | Coverage limit / increment |
|---|---|---|
| README, AGENTS, architecture, ADRs, roadmap, strategy specifications, validation notes | Library: browse/search/deep-link original documents and related tests | G1; documentation is read-only |
| `qte run`, SMA and six candidate families | Research: configure one run, review assumptions, launch, inspect/export | G3/G5; current command has no macro-router type |
| `qte lab`, gated/ungated candidates, router, cash/hold benchmarks | Research: complete slate, scenarios, base/stress, exclusions, selection and holdout | G4/G5; synthetic mechanics, not validated alpha |
| `compare_moving_average`, `run_experiment` Python APIs | Research: registered split/comparison workflows | G7; declarative bridge needed, not arbitrary Python factories |
| `MacroRegimeFilter`, router and dealer observation types | Library: exact rules; Research: as-of input/decision and route inspection | G4/G7; no observed dealer data or macro acquisition exists |
| CSV and saved Alpaca adapters, dataset preflight | Data: register local inputs, inspect mapping/assumptions, validate and choose dataset | G6a; no provider fields in canonical Bar |
| Alpaca downloader/cache | Data: explicit feed/date/symbol request and immutable cache inspection | G6b; approved credentials/entitlements and action-free declaration required |
| Owned engine results: metrics, fills, orders, equity | Research: focused chart/table/download views | G3; no browser accounting or metric reimplementation |
| Positions, closed/open trades, order-event history, exact event sequence | Research: result detail views | G3b/G3c; C++ owns them, some Python bindings and current CSV exports omit fields |
| Public C++/Python low-level contracts, order/execution/accounting/risk tests | Library: API/contract pages and linked deterministic test evidence | G1/G8; not raw mutable objects exposed over HTTP |
| CTest/Python tests and narrative validation records | System: previous evidence, explicit test job, failure details | G8; narrative notes remain distinct from machine test records |
| `scripts/benchmark_replay.py` | System: fixed hold/SMA workload, recorded timing and environment | G8; its `hold` option is a no-op workload, not buy-and-hold |
| Schwab GET and Robinhood crypto GET foundations | System: capability/prerequisite status, later explicit read | G9; not connected, no Robinhood equities or order submission |
| Offline `PaperCoordinator` | Library/System: design, offline tests and readiness checklist | G1/G8; no start-trading, halt-clear or lease-takeover controls |

All public capabilities get an explanation or usable workflow; not every library
constructor needs a form. Missing UI bridges above are planned work, not existing
features. Code-defined custom strategies remain normal trusted repository work,
reviewed and tested before entering the registry; no browser code editor, upload
of executable strategies, dynamic import path, pickle or `eval` endpoint.

## 3. Architecture and technology decision

```text
Browser: local HTML / CSS / JavaScript modules
                 | same-origin, versioned JSON
Python web service (.venv; loopback only)
  documentation reader | evidence catalog | bounded job coordinator
                 | immutable requests / owned result exports
Fresh .venv workers -> existing Python workflows -> pybind11 -> C++20 engine
                 |
Immutable artifacts + manifests     Local SQLite UI registry
```

Recommend **Starlette + base Uvicorn + markdown-it-py**, with plain browser ES
modules and local SVG charts. No Node, npm, React, Electron, Docker, CDN or cloud
service is required. SQLite, subprocess orchestration and file hashing use the
Python standard library. The optional stack is now installed in the repository
venv using the approved hash-checked locks.

Tradeoff: three small direct Python dependencies and explicit request schemas
give routing/server/Markdown behavior without introducing a separate frontend
toolchain. This costs more hand-written form/view code than a dashboard framework,
but permits the requested sparse appearance and precise evidence handling. Avoid
Streamlit-style rerun/state coupling and a handwritten HTTP server. Revisit a
frontend framework only after implemented complexity justifies it.

Use an optional `gui` dependency extra and separate locked GUI requirements, so
headless QTE and C++-only builds stay unchanged. Resolve candidate versions using
Python 3.14 compatibility metadata; request approval for the exact lock and its
installation, then verify it with tests. No guessed pins or unapproved test install.
HTTP test tooling and browser automation are separately declared development
dependencies, not silently added. No runtime package download.

Proposed code placement, created only as milestones need it:

- `python/qte/gui/`: app/router, security, documentation, catalog, format readers,
  jobs, request DTOs and packaged static assets; small files by responsibility.
- `python/qte/` shared research/config/export helpers extracted from `cli.py`
  where a second caller actually needs them. CLI and GUI use the same validation.
- `tests/python/test_gui_*.py` plus browser tests when their tooling is approved.
  Real engine parity fixtures remain in existing suites.

Available launch (later workflows remain gated by the milestones below):

```sh
cd /home/glsu6/Documents/qte
.venv/bin/python -m qte gui
# http://127.0.0.1:8765 ; foreground, Ctrl-C stops it
```

One server process, no automatic reload, proxy headers disabled, no LAN binding.
Port collision gives an actionable error; an explicit `--port` may change it.
No background daemon, startup installation, telemetry, account access or browser
opening unless requested. `qte gui` must check optional dependencies and report
missing ones without installing anything.

Official references checked for the proposal: [Starlette](https://starlette.dev/),
[Uvicorn settings](https://www.uvicorn.org/settings/), and
[Markdown rendering security](https://markdown-it-py.readthedocs.io/en/latest/security.html).
These are framework capabilities, not a substitute for QTE security tests.

## 4. Catalog, artifact formats and evidence

Use explicit registered roots, **never a recursive scan of all `build/`**. Build
contains passing/failing test fixtures that must not become research evidence.
Initial suggested imports are `build/strategy-lab-20260922`, the older
`build/strategy-lab-20260920`, and `build/trend-reference-20260921`; a user confirms
registration. Missing paths simply show unavailable. Existing artifacts are
read-only and retain their original bytes, identifiers and source identity.

Store only UI catalog/job metadata in `.cache/qte-gui/catalog.sqlite3`, never the
paper coordinator database. Config/input snapshots and new job outputs live in
`build/gui/jobs/<uuid>/`; workers write into a new `artifacts/` child. Imported
datasets are copied to immutable `build/gui/datasets/<uuid>/`. Append-only
holdout-disclosure records live in `build/gui/disclosures/`, outside the
rebuildable index, keyed by protocol/artifact and dataset/window identities.
Re-registration, path renames and restarts must retain those restrictions;
missing history means unknown disclosure status, never an untouched claim.
All these roots are already ignored. No deletion UI in the first release, and
no outside-repo filesystem browsing. Only the catalog is rebuildable; outputs
and disclosure records remain evidence.

Implement explicit readers, not one guessed JSON shape:

1. `single_run_v1`: root `manifest.json`, `report.json`, CSVs and `complete.json`;
   IDs in CSV use `fill_id` and `order_id`.
2. `strategy_lab_v1`: root configuration/summary/completion, per-clock
   report/slate/generator/macro/comparison, nested scenario CSVs using `id`.
   Clock report source identity applies to child runs; no fabricated child
   manifest. Old 14-entry and current 15-entry slates both work.
3. `test_evidence_v1` and `benchmark_evidence_v1`: new wrappers in G8, not implied
   by old console output. CTest `LastTest.log` is mutable scratch, not history.
4. A tagged `research_export_v2` wrapper in G3c identifies `artifact_kind`, schema
   version, capabilities and source lineage while preserving legacy readers.
   It serializes owned `BacktestResults` values: positions, closed/open episodes,
   full order snapshots/events, and equity/fill sequences. New router exports
   may include existing `route_events`; old artifacts show “not recorded.”

The C++ results already own these fields, but current Python bindings omit
`EquityPoint.sequence`, `BacktestResults.order_events`, fill sequences and many
order/trade fields. G3b first adds only the read-only owned-value bindings needed
for those views, with lifetime and exact-integer tests. G3c exports them. Neither
changes engine/accounting behavior or reconstructs missing facts from CSVs.

Verification checks schema, mandatory files, checksum map, exact inventory (except
the completion marker), bounded file sizes and safe paths. Reject absolute,
parent-traversal and symlink entries, duplicate JSON keys, nonfinite numbers and
unrecognized versions. Unknown formats remain downloadable as approved inert
attachments, not interpreted results. A completion marker is not sufficient
without verification. Cache verification by fingerprint, invalidate on file
change, and recheck the specific bytes served; stable file-handle reads must
detect mutation during verification. Do not silently repair old exports.

Keep these dimensions separate in the catalog and available in details:

- **Export:** incomplete / complete / unsupported format.
- **Integrity:** unchecked / verifying / verified / invalid.
- **Evidence:** synthetic / historical-declared / unknown; checksum verification
  is not authentication of a provider, preregistration or data quality.
- **Scenario outcome:** succeeded / failed / canceled / interrupted.
- **Selection:** excluded / selected / no selection; benchmarks are not candidates.
- **Holdout evaluation:** not evaluated / evaluated; separately, **disclosure:**
  no recorded GUI reveal / revealed outside protocol / unknown. Even the first
  state cannot prove absence of direct filesystem inspection.
- **Code match:** same fingerprint / different / unknown. Old valid results remain
  valid records when code changes. Current `source_identity()` hashes Python
  package files and the loaded binary, not docs or static assets. Record a
  separate GUI asset/version identity rather than changing old hash semantics.

Default list uses only name, kind, date and combined plain-language status.
The detail view exposes dimensions without collapsing them to one green badge.

## 5. Research honesty, precision and charts

The browser never rebuilds PnL, trade episodes, risk decisions or annualization.
It consumes existing reports and owned-result exports. Missing fields remain
missing; zero trades or undefined Sharpe is not an error to hide. Metric DTOs
include `value`, `undefined_reason`, `unit` and their recorded sampling policy.

Every int64/uint64 field (UTC ns, sequence, order/fill IDs, share quantities,
seeds) crosses the web API as a **decimal string**. JSON numbers for these are
rejected on input, converted exactly in Python, and only range-checked display
coordinates may become JavaScript numbers. Currency-valued doubles retain their
serialized precision; rounded display text never becomes simulation input.
Raw source JSON is parsed server-side to avoid JavaScript precision loss.

Raw event equity and sampled equity are different views. Retain duplicate-time
points and source row ordinal; v1 does not persist their event sequence, so show
“sequence not recorded,” never invent one. The chart may connect same-time points
vertically. Annualized metrics always use the recorded sampling convention, not
screen resolution or chart points. Gaps and stale/carry policies are visible;
`daily_24h` stays labeled a synthetic continuous clock, never “US daily session.”

One SVG chart initially, neutral solid equity line; drawdown is a separate chosen
view, comparison uses labeled solid/dashed strokes. Server-side deterministic
first/min/max/last bucket reduction is display-only, bounded to 2,000 points,
preserves chronological order, endpoints, extrema and gap boundaries. If those
cannot fit, request a narrower range. State raw/display counts, permit exact-row
inspection and download. Do not interpolate across declared gaps or stitch
independent experiment windows into a fictitious equity curve. Server-derived
drawdown presentation must use the canonical definition and parity fixtures;
headline metrics continue to come from QTE analytics, not reduced chart data.

Comparisons require compatible currency, interval, window and cost context, or
explicitly label differences and refuse aggregate ranking. Default order is the
recorded slate, not best return. Include failures, no-trade trials, cash and hold
benchmarks. Filtering shows excluded-row counts; it never changes selection.
Synthetic evidence cannot promote a candidate. Scripted macro inputs and absent
dealer inputs stay explicit; an allowed-family filter is not an actual router
trade history. Historical data remains caller-declared until provenance checks
support stronger language.

Untouched holdout data must not leak through search, charts, regime tables,
downloads or generic file routes. Catalog verification may hash bytes without
exposing values. Reveal requires an explicit warning/confirmation and an immutable
local disclosure event; subsequently show “revealed outside protocol.” Filter
mixed-role macro/regime exports too. This policy overrides all preview/download
routes, including unknown attachments, exact exports, archives and separately
registered copies. Protected or mixed-role files require disclosure before raw
download; known dataset/window identities inherit the same restrictions. New
unclassified inputs never get an untouched label. This cannot police direct
filesystem access and never claims proof of untouched holdout or preregistration.

## 6. Web API and local safety

Use `/api/v1`, stable opaque catalog IDs and bounded JSON DTOs. Core pybind objects
never cross HTTP or survive callbacks as borrowed references. UI read requests
cannot start simulations, downloads, tests or broker requests.

| Route family | Responsibility |
|---|---|
| `GET /capabilities`, `/library`, `/documents/{id}` | Readiness, search and safe Markdown view |
| `GET /artifacts`, `/runs/{id}`, `/experiments/{id}` | Registered metadata, paged details, metrics and bounded chart series |
| `GET /datasets`, `/datasets/{id}` | Registered provenance/preflight facts, authorized previews |
| `POST /catalog/register`, `/configs`, `/configs/validate` | Explicit repo-contained registration and immutable config snapshots; pure validation |
| `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/log`, `POST /jobs/{id}/cancel` | Typed, idempotent local jobs and bounded log cursor |
| `POST /artifacts/{id}/reveal-holdout` | Confirm and journal disclosure before serving protected contents |
| `GET /connections`, `POST /connections/{id}/read` | Local prerequisite status; deliberate approved GET-only broker action in G9 |

Nested endpoints for series/tables/downloads use catalog item IDs, not arbitrary
paths. Default page 25, max 200; chart max 2,000 points; metadata bodies max 1 MiB;
log pages max 64 KiB. Large datasets use explicit local registration/copy, not
browser uploads. Parse large CSVs incrementally; oversized or unsupported inputs
fail clearly. No shell, SQL, URL proxy, file editor or generic import endpoint.
Errors use stable code, sanitized message and request ID, not stack traces.

Threat boundary: one user on one workstation, not hostile local administrator
isolation. Still protect against malicious web pages, documents and manifests:

- Bind only `127.0.0.1`; exact Host+port and Origin checks, no wildcard CORS,
  no forwarded-header trust and no state-changing GET. Reject cross-site/null
  origins and malformed host headers. Protect all JSON reads as well as writes.
  Normal same-origin GET may omit Origin: require a valid session and same-origin
  fetch metadata or the session-bound request token, not absent Origin alone.
  JSON fetches always send that token; top-level authenticated document navigation
  is handled separately. Mutations require exact Origin plus CSRF token.
- Print a one-time local access code on startup, entered into the initial login
  form. No URL/query token or logged credentials. Exchange it for a random
  HttpOnly, SameSite=Strict session cookie, rotated on restart; HTTP loopback is
  the explicit reason Secure cannot be assumed. Require a per-session CSRF token
  on mutations, bounded login attempts and idle expiry. After logout/expiry a
  fresh code is issued only to the local terminal, not through an unauthenticated
  web endpoint. Do not mistake CSRF or
  loopback binding for protection against a compromised local process.
- CSP: local scripts/styles only, no inline scripts/eval, no frames/object embeds,
  no external image/font/network loads. Disable raw HTML in Markdown explicitly,
  allow only safe link schemes, prefix generated IDs, escape code/log/table text.
  External links require user navigation and use `noreferrer noopener`.
- Resolve and enforce repo/registered-root containment and reject symlinks on
  every file operation, including source data paths inside configs. Never serve
  `.git`, `.venv`, secrets, paper DBs, or arbitrary `.cache` files. Use explicit
  MIME types, `nosniff`, no HTML/SVG artifact execution, attachment-only exports.
- Server-only credentials supplied by the existing local environment/approved
  signer. Never browser storage, configuration artifacts, process arguments,
  stdout/stderr or public report files. Fresh research/test children get a
  minimal allowlisted environment without broker credentials. Account responses
  are private, no-store, redacted where practical and not research artifacts.
- No automatic provider calls, OAuth implementation, secret vault, reconnect,
  order placement or paper-control mutation as part of this GUI plan.

## 7. Job contract and recovery

Initial kinds: `research_run`, `strategy_lab`; later `dataset_validate`,
`download_alpaca`, `cpp_tests`, `python_tests`, `benchmark`, `comparison` and
explicit broker read. Only fixed handlers and allowlisted arguments. Use
`subprocess` argv with `shell=False`, `.venv/bin/python`, repo cwd, and unique
outputs. Never forward a user-supplied command, executable, environment or pytest
argument. Do not share engine/strategy instances across jobs or HTTP threads.

One active compute job; FIFO pending queue capped at 20. Research/test/benchmark
jobs have a default 30-minute configurable bounded timeout; a timeout means
interrupted/incomplete, not successful. Broker transport keeps its existing
20-second bounded timeout. Progress shows known phases and elapsed time, not a
fabricated percentage. Poll active job status every second, back off to five
seconds when hidden; no WebSocket/SSE complexity initially.

Persist request ID, idempotency key, canonical config+input hashes, declared
assumptions, executable/working directory, source identity, timestamps, worker
identity, state and exit code before launch. Reusing a key with different input
returns a conflict; refresh/double click cannot create a second run. Snapshot
effective config and inputs; a preset edit never mutates a running job. For copied
configs, rewrite data references to snapshots explicitly instead of breaking the
CLI's config-relative-path rule. Hash again at completion; source/input changes
produce a visible provenance failure, never a quietly successful mixed-code run.

States: queued -> running -> succeeded | failed | canceled | interrupted.
Cancellation requests terminate only the owned subprocess group, wait a bounded
grace period then force termination if needed; retain partial files. The engine
has no cooperative pause/resume, so do not invent that feature. If completion wins
the cancellation race, preserve verified completion rather than relabeling it.
Success requires expected artifacts, valid completion marker and exit status;
lab completion with failed scenarios displays those failures separately.

On service shutdown stop owned active workers. On crash/restart mark prior active
jobs interrupted, do not auto-retry downloads/account reads or attach to a PID
that may have been reused. A worker watches a parent-liveness pipe and terminates
if its owner dies; record launch identity. Verify surviving artifacts explicitly
before offering a new run. Never erase partial outputs. SQLite UI state is not a
financial journal or authority for account/position reconciliation.

## 8. Incremental implementation plan

Each row is a separate review/test gate, not permission to implement all rows.
The first usable release is **G1a + G1b + G2 + G3a + G4**: read-only library and
existing results. Research launching follows only after this evidence layer is
trustworthy.

| Milestone | Small deliverable / dependency | Required acceptance |
|---|---|---|
| G0 — dependency/contract gate | Approve stack, resolve exact GUI/test dependency locks, versioned DTO fixtures | `.venv` only; no silent install; native C++ and headless Python remain independent |
| G1a — local shell | Loopback service, four routes, zero-radius visual tokens, session/origin boundary; G0 | Wrong host/origin/session rejected; no network egress; responsive keyboard/zoom check; no work starts on navigation |
| G1b — documentation library | Original Markdown, search, ADR/status/API/strategy links; G1a | HTML/script/path injection fixtures; valid internal anchors; missing pages explicit; no duplicated editable docs |
| G2 — evidence catalog | Explicit root registration; two legacy readers and integrity states; G1a | Both slate versions; failed/incomplete/tampered/unknown schemas; traversal, symlink and mutation races; no test-fixture auto-import; holdout protection |
| G3a — existing run views | Metrics, raw/sampled equity, orders/fills, exact downloads; G2 | Match actual exports; undefined reasons; 64-bit round trips; duplicate-time/gap charts; no browser financial calculation |
| G3b — result binding coverage | Minimal read-only owned-value bindings for existing C++ result fields; G3a | Exact ns/ID/sequence round trips; retained results survive parent deletion; no new mutation or accounting logic |
| G3c — export completeness | Tagged additive exporter for owned positions/trades/order events/sequences; G3b | Hand-calculated fixture parity; legacy artifacts unchanged; missing old fields labeled; CLI and GUI output parity |
| G4 — lab comparison | Slate/window/cost context, reasons, benchmarks, macro inputs and roles; G3a | All 15 current entries and 14 legacy entries; no selection on synthetic; mixed-role holdout leak tests; no stitched-window returns |
| G5a — local job runner | Single-run CLI handler, FIFO, identity, cancel/restart and immutable outputs; G2 | Double click/retry dedup; config-relative paths preserved; crash/cancel/source-change tests; no credential inheritance |
| G5b — research forms | Family-specific fields, advanced costs/risk/sampling, lab handler; G5a | Same valid/invalid config results as CLI; equivalent metrics/fills; explicit pre-run assumptions; legacy SMA works |
| G6a — local dataset workflows | CSV mapping, saved Alpaca imports, bounded validation jobs; G5a | Existing interval/availability/action preflight unchanged; unsupported session semantics explicit, no exchange-calendar validation claim; no repair/bar filling |
| G6b — Alpaca download UI | Explicit feed/date/symbol/action declaration, cache lineage; G6a | Offline pagination/corruption/retry parity; no request on page load; real check requires separate user-authorized credentials |
| G7a — declarative comparison bridge | Config adapters for existing SMA and registered `run_experiment` workflows; G5b/G6a | Chronology/universe/currency checks preserved; frozen slate before holdout; no arbitrary factory imports; all trials retained |
| G7b — macro/router bridge | Explicit local observations, as-of inspection, router params/route-event export; G7a/G3c | Availability/vintage/staleness/dealer provenance and proxy rejection; exit-before-switch parity; no false dealer inference |
| G8a — test evidence | Fixed CTest and pytest jobs plus machine records; G5a | JUnit/log/command/test-source+binary hashes, exit status and complete marker; failed jobs remain visible; skip != pass; stale records labeled |
| G8b — benchmark evidence | Bounded existing benchmark job and machine/build/workload record; G8a | Timing not research performance; no-op `hold` correctly labeled; no automatic million-bar runs or unrelated build/install commands |
| G9 — connection inspection | Readiness then separately approved explicit broker GET; G1a | Mock auth/redaction/operation validation; no trade endpoints; Schwab contract/signer prerequisites fail closed; private response retention off |

G8 uses `.venv/bin/ctest --preset dev --output-on-failure --output-junit <job path>`
and `.venv/bin/python -m pytest tests/python --junitxml=<job path>` with repo-local
unique basetemp. CTest runs **existing compiled tests**, not a silent build. Record
binary hashes and source/build freshness separately; an old passing binary is not
evidence that current edited C++ passes. Build actions require a later explicitly
scoped handler. No arbitrary test code supplied by the browser.

Every milestone needs targeted Python/API tests, full existing C++/Python
regressions and an Astra review before advancing. Browser checks must exercise
real controls, not just screenshots: document search, run selection, corrupt
artifact warning, configuration error, job cancellation and restart. Add browser
automation only with approved tools; record manual checks where automation cannot
run. Native engine tests alone do not validate a GUI. Measure catalog paging and
bounded chart responses against the existing 1,080-scenario lab before optimizing.

## 9. Model assignments, approvals and non-goals

The requested lead/reviewer is **GPT-6 Astra**, used explicitly for the independent
capability audit and final architecture review. Planned build split: **GPT-6 Sol**
for backend/catalog/jobs/integration; **GPT-6 Luna** for bounded static styling,
documentation views and fixture work after Astra fixes contracts. Sol/Luna work
in non-overlapping files; Astra checks interfaces, security, causal/financial
invariants, test strength and integration, not merely their completion summaries.

The [official model catalog](https://developers.openai.com/api/docs/models) lists
those GPT-6 models, but this session's callable agent tool currently offers
`gpt-6-astra`, `gpt-5.6-sol` and `gpt-5.6-luna`. No Sol/Luna build agent was started
and no version silently substituted. Use GPT-5.6 build variants only if the user
approves; otherwise wait for the requested versions. These are development roles,
not an OpenAI API dependency, subscription or in-product autonomous trading agent.

Recommended defaults above settle visual style, navigation, stack, paths,
ownership, job concurrency and rollout. Remaining approvals/choices are explicit:

1. Approve G0 dependency additions/locks before installation and G1 implementation.
2. Confirm available build-model substitutions or retain the requested versions.
3. Confirm evidence roots at first import; other repo files stay inaccessible.
4. Supply approved provider credentials/contracts/signing setup only when opting
   into network/account reads. No credential entry is needed for the library.
5. Browser automation may require an additional package/browser installation;
   approve separately or use documented manual checks without claiming automation.

No engine behavior change, new strategy economics, exchange calendar, corporate
action support, optimization/walk-forward/Monte Carlo implementation, cloud/LAN
hosting, paper-account control or live trading is authorized by this plan. M17–M20
and R2–R4 remain prerequisites for their respective operational/research claims.

## 10. Planning validation

Inspected source and both actual artifact layouts; preserved all existing dirty
work. No runtime code or dependencies changed. Existing regression commands:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-plan-20260922
git diff --check
```

Native build succeeded (no recompilation required); **15/15 CTest executables**
and **419 Python tests** passed (9.14 seconds). These verify the existing baseline,
not an unimplemented GUI. Independent GPT-6 Astra review identified and prompted
corrections for missing Python result bindings, durable disclosure history,
cross-route holdout protection, unsupported session-validation claims and
same-origin GET handling. Astra reviewed the corrected plan and reported **no
remaining blocking design findings**. That is design approval only; implementation,
security behavior and dependency compatibility still require their acceptance
tests.
