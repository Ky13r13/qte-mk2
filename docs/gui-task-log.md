# Active GUI implementation checkpoint

Last checkpoint: 2026-10-13, user requested the next phase. G7a.2a comparison
artifact verification/classification is in progress; G7a.1 is accepted.
G7a GUI controls are not implemented. Network remains disabled by default.
**Read this file before resuming.**

## Authority and scope

User approved installation of the pinned GUI/test requirements in the existing
`.venv`, Sol and Terra build agents, and GPT-6 Astra review. Work through G1,
G2 and G3a/G3b/G3c in sequence. On 2026-09-24 the user extended authorization to
G4 and G5 after G1–G3 pass their gates. No broker requests, system package
installations, Git history changes or outside-repository
writes. Preserve all existing modified/untracked files and research artifacts.
User explicitly wants to keep pushing manually. Local inspection found `origin`
at `github.com:Ky13r13/qte-mk2.git`; push credentials/rights were not tested. Do not
commit, push, probe remote write access, or change remotes/history.
Read `AGENTS.md`, `docs/gui-architecture.md` and `docs/gui-dependencies.md`.
On October 8 the user explicitly authorized continuing all remaining G6–G9
milestones to support strategy development, while retaining the request for
token-efficient work and concise documentation. This authorizes implementation
and offline/mock checks, not credential acquisition, live downloads, broker
requests, new dependencies or Git operations. Each external capability retains
its explicit approval/prerequisite boundary.
On October 12 the user authorized task-scoped local commands to finish the GUI
and explicitly prohibited internet connections. Continue offline with the
existing tools; no provider/broker requests, remote Git operations or downloads.
The one-time resume heartbeat was updated for October 13 at 03:45 Los Angeles
time. It fired, resumed this work, and was deleted after use; it is not a
nightly schedule or usage-reset guarantee.

## Baseline / environment

### G7a.2a active scope

Sol `g7a2_sol_reader` owns the new pure comparison verifier and its unit tests.
Root owns stable-capture dispatch, catalog protection inheritance, CLI/catalog/
API regressions and docs. Astra `g7a2_astra_review` reviews independently. No
engine, form, route, job or browser code is added. Existing CLI exports remain
read-only, including the earlier advisory unsupported-reader marker.

Verify all role/file mappings before classifying bytes. Test inputs/scenarios
and mixed metadata require explicit disclosure; invalid/incomplete exports
remain entirely guarded. If a nominally safe input is already protected by the
disclosure journal, protect all derivatives too. Generic catalog status keeps
selection, holdout evaluation and scenario outcome unknown until role-filtered
views exist; no report metrics or failure reasons are public metadata.

### October 13 G6b acceptance

- Fixed `download_alpaca` form/preview/snapshot/worker; explicit startup and
  credential gates before submission/dispatch; no credentials in persisted
  artifacts/logs/argv. Pure GETs and preview never download.
- Bounded acquisition, exact request/cache/page/merged-bar verification,
  inherited disclosure protections and authenticated lineage view.
- Full command `.venv/bin/python -m pytest tests/python -q --maxfail=1
  --tb=short --basetemp=build/test-gui-g6b-confirm-20261013
  --junitxml=build/gui-g6b-confirm-20261013.xml`: **1009 passed, 68.29s**.
  Initial full run reached 761 passes then failed an outdated mocked launcher
  signature; updated it to assert both default-disabled and explicit-enabled
  forwarding. The artifact-kind test also caught a misplaced branch, now fixed.
- `PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev`: no work;
  `PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure`:
  **15/15 passed**. `git diff --check`: clean.
- `/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
  --test tests/javascript/research_ui.test.mjs`: **11 passed**.
- Astra reviewed acquisition bounds, strict JSON, FIFO/symlink safety,
  credential gates/env isolation and provenance. Seven adversarial safety
  regressions are included in the full suite. Raw and canonical snapshot hashes
  are both checked; a fixture was corrected without removing provenance checks.
- Actual local browser: authenticated, opened Alpaca form, entered SPY/1Hour/
  explicit iex/UTC window/action declaration, preview succeeded and download
  stayed disabled. No provider/broker requests, downloads, installs or Git writes.
  Isolated state: `build/gui-browser-g6b-20261013`, port 8770. Temporary tab
  closed and server stopped; no credentials/access codes recorded here.
- Astra final review closed with no blocking findings after confirming both
  provenance hashes. The full suite includes the seven safety regressions.
- Final closure command `.venv/bin/python -m pytest
  tests/python/test_download_capture_safety.py tests/python/test_gui_download_jobs.py
  tests/python/test_gui_download_api.py tests/python/test_gui_server.py -q
  --basetemp=build/test-gui-g6b-closure-20261013`: **30 passed, 1.04s**.
- Next: G7a declarative comparison bridge, then G7b/G8/G9. Real acquisition
  needs separate authorization, credentials and entitlement/action-free checks.

### October 13 G7a.1 acceptance

Root owns CLI integration, the unsupported comparison artifact guard,
documentation registry, examples, integration/export tests and docs. Sol
`g7a_sol_config` finished `comparison_config.py` and its tests; Terra
`g7a_terra_export` saved `comparison_export.py`/initial tests before reaching
usage limit. Root completed failure/provenance/export regressions. Astra
`g6b_astra_review_oct12` approved the independent review without blocking
findings and passed 37 targeted tests before the four final regressions. No GUI job,
account or engine change is included.

CLI evidence is caller-declared and tied to raw SHA-256, not authenticated
historical status. Both existing APIs are preserved: the exact four-scenario
SMA comparison and frozen-slate `run_experiment`. Synthetic examples cannot
promote candidates. Registry identities include family, symbol and parameters.
Outputs use indexed paths and explicit scenario mapping; failures are retained.
The catalog refuses to interpret this new format as a lab until holdout-aware
readers exist. Original user work remains untouched.

Commands run from the repository root, using the existing `.venv`:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python/test_comparison_config.py tests/python/test_comparison_export.py tests/python/test_comparison_cli.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a-reviewed-20261013
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a-full-20261013 --junitxml=build/gui-g7a-full-20261013.xml
git diff --check
.venv/bin/python -m qte compare --config examples/comparisons/sma.json --output build/g7a-demo-sma-20261013
.venv/bin/python -m qte compare --config examples/comparisons/experiment.json --output build/g7a-demo-experiment-20261013
```

Results: native build no work; **15/15 CTest passed**; final comparison gate
**41 passed, 1.08s**; full Python **1050 passed, 68.322s**, zero failures/errors/
skips in the saved JUnit report. Full Python ran with scoped local execution
after earlier sandbox async-test stalls; no network/dependencies were used.
The focused CLI tests invoke both new example commands and verify recursive
checksums, retained failed trials, exact API parity, repeatability, source-change
failure and fail-closed catalog protection. No browser code changed in G7a.1;
the last browser gate remains G6b's 11 JS tests/local browser check.
Both explicit CLI demos succeeded in ignored `build/` directories: four SMA
scenarios and eight experiment train/validation scenarios. Synthetic evidence
selects nobody and leaves the final test unevaluated. `git diff --check` is clean.
Do not mark all of G7a complete: comparison GUI launching/views are still pending.

### Remaining G7a implementation seam

Sol inspected `experiments.py` (`Candidate`, `Evidence`, `ResearchWindow`,
`ExperimentConfig`, `run_experiment`) and `research.py`
(`compare_moving_average`). Reuse their chronological/universe/currency/interval
checks, frozen slate and validation-only selection; never import browser-named
factories. The shared preparation/CLI slice now exists with exact two-split SMA
parity; do not mislabel historical exports as synthetic labs. Next add a strict
role-aware export verifier/reader before multi-source snapshots and GUI jobs/views.
Sol's read-only next-gate recommendation: a dedicated `gui/comparison_artifacts.py`
verifier, dispatched from `artifacts.py`, with strict cross-file checks and
catalog disclosure regressions before adding routes/forms/JS. Verify every role
classification rather than trusting manifest labels; incomplete/invalid inputs
remain fully guarded. Never expose holdout outcomes/reasons in catalog metadata.
Current bounds: 1–32 allowlisted candidates, 3–24 ordered experiment windows,
one final test window, existing engine/cost/selection defaults.
Before implementation settle historical evidence declaration vs validation-ID
selection. Validation-ID-only GUI is the safer recommendation, but existing
G6a reuses original paths rather than immutable registry input bytes. Resolve
that real boundary instead of bypassing reserved paths. Holdout execution must
not implicitly disclose test values or require a reveal merely to compute;
do not reuse ordinary input authorization without a protocol-aware contract.

Repository `/home/glsu6/Documents/qte`; native compiler `/usr/bin/g++`, CMake
`build/local-dev`, Python only `.venv`. SSL and scoped RPM verification passed
after reboot; original failure retained in `docs/gui-g0-preflight.md`.
Baseline: 15/15 CTest executables, 419 Python tests. Approved hash-checked wheel
installation completed for all 12 packages in requirements-gui-test.lock using
the existing venv, no cache and repository-local TMPDIR. Sandbox index access
failed; scoped network approval allowed the install. No system package changed.

## File ownership during active build

- October 12 G6b: Sol `g6b_sol_plan_oct12` owns bounded download wrapper/verifier,
  adapter hardening and form compiler; Terra `g6a_terra_diagnose_oct12` owns
  capture/snapshot/runner gates; root owns catalog/API/browser/CLI integration.
  Astra `g6b_astra_review_oct12` reviews independently. No real network tests.
  G6a command `.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short
  --basetemp=build/test-gui-g6a-resume-confirm-20261012
  --junitxml=build/gui-g6a-resume-confirm-20261012.xml`: **979 passed in 70.24s**.
  Sandbox run stalled and was interrupted; scoped execution completed normally.
- October 9 G6a: Sol `g6_sol_validation` owns shared `data_validation.py`,
  `research_config.py`, CLI/forms and corresponding new unit tests; Terra
  `g6_terra_runner` finished capture/snapshot/manager/worker changes and tests.
  Root owns dataset catalog/routes/views/browser, API tests and documentation.
  Astra `g6_astra_review` reviews independently. Previous assignments are history.
- October 8 browser gate: Sol owns `static/research_forms.js` and its new static
  test; Terra owns `static/research_jobs.js`, its new static test and `app.css`.
  Root owns `app.js`/`app.py` integration, API tests, docs and real-control QA.
  Astra reviews independently. No validator, runner or financial changes.
- October 7 post-snapshot gate: Terra owns `jobs.py`, `job_worker.py` and
  manager/lifecycle tests. Sol owns new `job_outputs.py` and its strict fixed-lab
  verifier tests. Root owns `job_routes.py`/request shape parsing, new inline API
  tests and docs/full gates. Astra independently reviews. No browser/core changes.
- October 7 post-preview gate: Terra owns new `inline_inputs.py`, minimal
  `job_inputs.py` dispatch and `test_gui_inline_inputs.py`. Sol owns independent
  `test_gui_snapshot_compatibility.py` legacy golden tests. Root owns docs,
  snapshot-to-CLI integration tests and full gates. Astra reviews independently.
  No HTTP/manager/worker/core changes in this snapshot-only slice.
- October 7: G5b.2a source is saved. Root owns preview routes/app wiring,
  Catalog authorization, integration/protection tests and docs. Sol owns
  `research_forms.py`/unit tests; Terra owns `form_inputs.py`/unit tests (both
  source-quiet). Astra resumes independent review. Sol is doing read-only G5b.2b
  seam planning; no new launch/snapshot/worker changes before the preview gate.
  All older assignments below are historical.
- October 6: G5a is complete. Sol `g5b_sol_design_oct4` now owns
  `research_config.py`, `cli.py` and single-run preparation tests. Terra
  `g5a_terra_finish_oct4` owns CSV/Alpaca byte-loader seams, `strategy_lab.py`
  pure preparation and their new tests. Astra `g5a_astra_review_oct4` independently
  reviews G5b.1. Root owns docs/integration/full gates. No HTTP/runner/form changes
  are authorized in G5b.1; the older assignments below are historical.
- October 4 resume: earlier agents are no longer live. Terra task
  `g5a_terra_finish_oct4` owns the runner/input files and their three test files;
  Astra task `g5a_astra_review_oct4` is the independent read-only reviewer.
  Sol task `g5b_sol_design_oct4` is doing read-only G5b design, not implementation.
  The historical assignments below explain the saved work, not concurrent owners.
- Sol task `gui_sol_g1` (`gpt-5.6-sol`) saved the initial G5a runner; its later
  pending-initialization turn was interrupted. No current file ownership.
- Terra task `gui_terra_g1` (`gpt-5.6-terra`), now G5a:
  input helper/tests, `jobs.py`, `job_worker.py`, `test_gui_jobs.py` and
  `test_gui_job_worker.py`. Root explicitly transferred the remaining runner
  fixes after interrupting Sol; do not allow concurrent edits in these files.
- Root: `app.py`, request/DTO helpers, artifact API routes/tests, CLI/server,
  docs/checkpoints, integration and real-browser validation.
- Astra task `gui_astra_g1_review`: completed G1 through G4 implementation
  review and G5a design review. G5a implementation still requires a gate.
  Do not assume an errored/interrupted review passed.

## Agreed G1 interface

`qte.gui.app.create_app(repository: Path, *, port=8765, code_sink=print,
clock=time.monotonic) -> Starlette`. Packaged fixed assets: `index.html`,
`app.css`, `app.js`. Header `X-QTE-Token`, HttpOnly cookie `qte_session`.
Auth routes `/api/v1/session` POST/GET/DELETE; library/capability DTOs in
`docs/gui-contracts/v1/`. Zero-radius neutral four-section UI; research/data/
system workflows unavailable until their milestone. No arbitrary static mounts.

## Next actions

Current resume target (October 12): finish G6a final test gate, then G6b
downloads, G7a comparisons, G7b macro/router, G8a test evidence, G8b benchmarks
and G9 connection inspection in reviewable increments, OFFLINE. The approved
same-version CMake repair succeeded and CTest passed 15/15; no reinstall needed.
Full Python gate still needs final confirmation. See checkpoint below. G5b.3 is saved:
954 Python, 15 CTest and 6 executable JavaScript state tests pass; real browser
run/lab preview→launch→success→explicit registration is verified. Extended
accessibility/200% zoom and exhaustive browser lifecycle acceptance remain
pending. Use `docs/test-build-guide.md` for data flow and strategy development.
No external calls or dependency installs without the applicable approval. The numbered list below
is milestone history, not a request to restart completed work.

### G6a checkpoint — October 9

- Added data-only `qte validate-data` and GUI Data → Validate local dataset;
  CSV mappings/saved Alpaca use existing adapter preflight. Validation never
  constructs a strategy or runs the engine. Fixed job, snapshots, explicit
  registration and inherited disclosure protection reuse the current framework.
- Export `dataset_validation_v1`: unique wrapper, configuration, summary,
  captured input and completion hashes. Exact canonical replay, strict JSON,
  historical source identity, full input/config/source binding at worker exit.
- Dataset view reuses the matching original source with `expected_sha256` pinned
  in run preview/snapshot. No reserved-path bypass. Moved/deleted/changed sources
  must be validated again; immutable raw-byte replay by registry ID is deferred.
- New files: `python/qte/data_validation.py`, GUI `dataset_routes.py`,
  `dataset_views.py`, `static/datasets.js`, four `test_*dataset*.py` modules.
  Updated CLI/config, forms/capture/snapshots/worker/catalog and browser routing.
  Existing G5b.3 untracked files and user changes remain preserved.
- Full command `.venv/bin/python -m pytest tests/python -q
  --basetemp=build/test-gui-g6a-final-20261009`: **970 passed, 73.78s**.
  Earlier full run: 969 passed/1 static navigation assertion failed; preserved
  explicit research navigation and added dataset branch/assertion, then reran.
  Additional selection regression command `.venv/bin/python -m pytest
  tests/python/test_gui_datasets.py -q
  --basetemp=build/test-gui-g6a-selection-final-20261009`: **6 passed, 1.76s**.
- `/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
  --test tests/javascript/research_ui.test.mjs`: **9 passed**. Existing Node only;
  sandbox subprocess EPERM required scoped execution. No runtime installed.
- `PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev`: passed, no work.
  `PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure`: exit 135
  inside and outside sandbox; direct packaged `ctest --version` also exit 135.
  Ran each `build/local-dev/tests/cpp/qte_<name>_tests` directly for names
  bar, domain_types, price_instrument, order, fill, open_only_execution, position,
  portfolio, trade, risk, strategy, dataset, schedule, backtest, analytics:
  **all 15 passed**. `/usr/bin/g++ --version` reports GCC 16.2.1.
- Read-only CTest diagnosis: installed CMake 4.4.3 binary size 22614984 matches
  RECORD but reading fails EIO at byte 6946816. Expected SHA256
  `10e5d9bad3af7c3cfb59a4e906847a719e4d550299c4608e26e0d32e3cea915b`.
  Sibling cmake/cpack hashes match RECORD and run. Unreadable extent on Btrfs
  explains SIGBUS; underlying storage cause is not established. User subsequently
  approved same-version repair: downloaded and verified the exact CMake 4.4.3
  wheel (4167 RECORD entries, CTest hash above), then installed with
  `TMPDIR="$PWD/build" PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
  .venv/bin/python -m pip install --force-reinstall --no-deps --no-index
  --no-cache-dir build/cmake-repair-20261009/cmake-4.4.3-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`.
  Wheel SHA256 `bae3c4954623ec4d62e62c70443f0da7988b733111c2871fcc6a31ead5137e20`.
  CTest checksum/version and pip check passed; build/local-dev regenerated and
  built successfully; `ctest --preset dev --output-on-failure` passed **15/15**.
  Repair predates October 12's no-internet instruction. No system packages changed.
  Recommend user/admin filesystem health inspection if EIO recurs.
- A one-time task heartbeat `resume-qte-milestones` was scheduled and fired
  October 9 at 03:00 America/Los_Angeles (PDT, 10:00 UTC). It retained all approval
  boundaries. This is not an ongoing nightly schedule.
- Browser QA used isolated `build/gui-browser-g6a-20261008`, loopback port 8769.
  Validation preview→launch→success→registration→pinned-source SMA run→success→
  registration→result chart verified. Server session 26871 stopped cleanly and
  temporary tab closed. No real data, provider calls or persistent QA server.
- Astra final review passed after malformed/deep JSON and regular-file capture
  race fixes. Sol's final focused unit gate passed 14 tests; final JS gate 9 passed.
- The later Oct9 full run `--basetemp=build/test-gui-g6a-reviewed-20261009`
  showed one failure then stalled; this was NOT a passing run. A subsequent
  repair-era Python invocation was aborted. Reproduce rather than dismiss it.
  October 12 fresh command `.venv/bin/python -m pytest tests/python -q
  --maxfail=1 --tb=short --basetemp=build/test-gui-g6a-resume-20261012
  --junitxml=build/gui-g6a-resume-20261012.xml` is in progress.
  Extended zoom/narrow-screen acceptance remains separate.

1. DONE: approved locked packages installed; imports and `pip check` passed.
   Optional extras, CLI integration and loopback launcher are saved. No reinstall
   needed. All earlier agents stopped; new Sol/Terra G1 fix agents and independent
   Astra review started on resume. Their names have `_g1`/`_g1_review` suffixes.
2. G1 fixes complete: original 21-pass/1-fail unsafe-Markdown test now passes;
   FileResponse/ASGITransport hang resolved by fixed static-byte responses.
   Unicode credentials, unavailable document rows/DTO, body bounds, framing,
   anchor/skip links, file mutation/FIFO and logout feedback have regressions.
3. G1 independent Astra review has no remaining blockers. Full Python suite:
   `.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g1-gate-20260926`
   => 449 passed in 10.38s; scoped suite 30 passed. `git diff --check` passed.
   Browser sign-in, literal search, docs, metadata, skip link, restart invalidation
   and 390px layout checked using existing in-app browser. Explicit 200% browser
   zoom could not be established with the available shortcuts; remains manual QA.
4. DONE: G2 explicit SQLite artifact registry and strict
   legacy readers with durable holdout disclosure outside the rebuildable index.
   Code and UI are present; 60 GUI tests passed before the latest added hardening.
   Sol's latest core suite: 27 passed. Root API tests cover explicit registration,
   auth, decimal-string fields, downloads, invalid/disappeared roots and disclosure.
   Full Python command `.venv/bin/python -m pytest tests/python -q
   --basetemp=build/test-gui-g2-full-20260926` passed 490 tests in 10.22s with
   scoped execution approval. This predates the final three review fixes below.
   Real old/new labs and single export verify read-only (3093/3309/6 files); no
   actual research root has been registered or revealed.
   Final Astra findings fixed/re-reviewed: unsafe new entries no longer crash the
   catalog; partial disclosure has a separate remaining-disclosure flag;
   refreshed protected hashes inherit protection when copied/repacked.
   Protective classification can be persisted by verification; this never records
   a reveal, launches work, or modifies the research export.
   Final command `.venv/bin/python -m pytest tests/python -q
   --basetemp=build/test-gui-g2-final-20260927` => 494 passed in 10.72s (approved
   scoped execution); native build and 15/15 CTest still pass. Real browser tests
   verified typed disclosure and old-file available/new-file blocked after a
   later addition, using isolated synthetic fixtures only. Astra passes G2.
5. G1 through G4 review completed; NOW G5a bounded local research job runner.
   Never mutate legacy artifacts or change engine accounting/execution. Old
   missing fields remain explicitly not recorded. G5 implementation is next.
6. Update this log with exact commands/results, outstanding review findings,
   running process IDs/ports (no access codes), and next file/action at each gate
   and before stopping. Log checkpoints are not a scheduled automatic restart.
7. After G3, continue G4 lab comparison and G5a/G5b bounded local research jobs
   and forms, with the same tests/review gates. No network/account jobs.

Resume validation: native build succeeded (`PATH="$PWD/.venv/bin:$PATH" cmake
--build --preset dev`); `ctest --preset dev --output-on-failure` with that PATH
passed 15/15. `test_gui_server.py` passes 11 tests. Root extracted shared bounded
JSON/pagination and exact integer DTO helpers for G2, with additional regressions.

The isolated QA server used `build/gui-browser-20260926/repository` (not the
user's main catalog), on 127.0.0.1:8765 with explicit local-bind approval. Session
70852 was stopped; replacement session 17614 was stopped at this checkpoint.
No GUI or compute process is intentionally left running. Restart for further
browser QA; its fresh code must be read from the terminal, never this file.
User's saved lab/reference outputs remain unregistered and untouched.

Sandbox diagnostic: standalone asyncio thread wakeup hangs in the restricted
sandbox but returns normally with scoped escalation. G2 API/full Python suites
therefore run with user-approved scoped pytest execution, not changed dependencies
or weakened tests. Native build and 15/15 CTest tests passed again on G2 review.

## G3a resume details — 2026-09-27

Implemented files: `python/qte/gui/run_views.py`, `charts.py`, run routes in
`artifact_routes.py`, static `app.js`/`app.css`, and tests
`test_gui_run_views.py`, `test_gui_charts.py`, `test_gui_run_api.py`,
`test_gui_static.py`. Documentation changed in `gui-usage.md`,
`gui-architecture.md`, `roadmap.md` and this log. Existing dirty core/binding/
strategy files were not edited in this continuation. No packages installed,
Git history/remote operations performed or broker/provider calls made.

- API: `/api/v1/runs/{id}` returns `{schema_version:1,run:...}`; `/series` returns
  `{schema_version:1,series:...}`; `/tables/{name}` uses a flat envelope with
  `columns,rows,total,offset,limit,warnings`. Paging is numeric bounded metadata;
  artifact integers (ns/IDs/counts/ordinals/config values) are decimal strings.
- Series supports event/sampled, max 2000 points and inclusive signed-int64
  `start_ns`/`end_ns`; duplicate times/ordinals preserved, sequence null for v1.
  Fixed intervals alone determine gaps; no invented exchange sessions.
- Every read uses Catalog authorization. Final snapshot/generation consistency
  rejects replacement of valid files/marker between component reads. Reversed
  source timestamps (even outside the selected range) and malformed data/
  sampling metadata fail explicitly. No metric/accounting reconstruction.
- Astra's navigation finding fixed locally: only verified/unblocked single runs
  auto-open. Errors retain catalog/reveal access. Full record detail is reachable
  from the first table column. Recorded sampling now renders instead of being
  mislabeled missing. SVG coordinates use BigInt time differences and scaled
  finite equity values; inclusive chart-range controls never resample metrics.
- Browser verified: reference default chart, exact fill record, sampled run
  event 9 points versus sampled 8, annualization policy and undefined metrics,
  range narrowed to 2 points without changing headline metrics, corrupt catalog
  diagnostics, inherited protected-manifest catalog fallback, and 390px viewport
  without page overflow. Viewport reset. Download bytes/header/auth are HTTP
  tested; native save-dialog flow and explicit 200% zoom remain manual QA gaps.
- Isolated fixtures: `build/reference`, `build/corrupt`, `build/unknown`, and new
  `build/sampled-reference` under the QA repository. The sampled manifest was
  copied to unknown/copied-manifest.json to test inherited protection. It is now
  intentionally blocked in that QA catalog; do not clear its disclosure history.
  No new reveal was made during this continuation. QA tab closed after checks.

Commands/results (repository root):

```sh
.venv/bin/python -m pytest tests/python/test_gui_run_api.py tests/python/test_gui_run_views.py tests/python/test_gui_charts.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-g3a-integration-20260927
# 29 passed before the additional generation/ordering regressions
.venv/bin/python -m pytest tests/python/test_gui_run_views.py tests/python/test_gui_static.py tests/python/test_gui_charts.py -q --basetemp=build/test-gui-g3a-read-stability-20260927
# 26 passed
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g3a-full-20260927
# 517 passed in 11.57s; scoped async-test execution approval
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
# native build succeeded, no work required
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# 15/15 passed, build/local-dev only
git diff --check
# passed
.venv/bin/python -m qte run --config examples/research-run.json --output build/gui-browser-20260926/repository/build/sampled-reference
.venv/bin/python -u -m qte gui --repo-root build/gui-browser-20260926/repository --port 8765
# isolated QA only; foreground server subsequently stopped
```

Immediate next action: resume Astra G3a review, not a dependency install or a
fresh rebuild of the project. Check chart reduction/ordering, generation guards,
DTO precision, catalog fallback and protected-content routes. Do not count source
substring tests as behavioral browser coverage. Then run the gate again if fixes
are needed and proceed to G3b using Sol for bindings plus separate Terra work.

G3b inspection already established existing owned C++ fields: equity sequence,
fill sequence/reference open, full order request/snapshot, order events,
trade episode IDs/times/sequences/quantities/direction/PnL, position mark sequence.
PositionSnapshot does **not** own average-entry/PnL/final cash; do not invent them.
Expose only read-only owned copies, with exact int64/uint64 and retained-lifetime
tests. Rebuild editable QTE locally without dependencies (`--no-index
--no-build-isolation --no-deps -e .`, cache disabled, TMPDIR inside build), then
test before G3c. Proposed G3c default: preserve v1 CLI/export bytes and make the
tagged richer export opt-in; explain/confirm the concrete additive interface in
the next implementation step. G4 lab views must filter mixed-role reports without
exposing test values; do not bypass current Catalog.read_file protection broadly.

## G3a gate cleared / G3b active — 2026-09-27

Resumed Astra review found one additional malformed-schema defect: a metric
missing `value` was accepted as verified but crashed the view. `artifacts.py`
now requires that key (explicit null remains valid); actual-engine HTTP regression
in `test_gui_run_api.py` verifies invalid catalog status and sanitized 409.
Astra re-reviewed and reports no remaining G3a blockers.

`.venv/bin/python -m pytest tests/python -q
--basetemp=build/test-gui-g3a-review-final-20260927` => **518 passed in 12.44s**
with scoped async-test execution. Native build, **15/15 CTest**, and
`git diff --check` passed using the same commands as above. No server running.

Current assignments (resumed 2026-09-28): Sol owns `bindings/module.cpp` and new
`tests/python/test_gui_result_bindings.py`; Terra owns new
`docs/gui-result-bindings.md`; root owns public enum imports, integration/docs;
Astra reviews after implementation. Keep G3b minimal: existing result fields
only; no extra RunManifest/RiskLimits surface without a concrete consumer.
No core behavior or constructors added merely for tests. Existing dirty binding
work is preserved. Local editable rebuild allowed without dependency/network
installation; exact command/results will be logged at the next gate.

G3b source/docs now present, still testing: readonly fields/enums in
`bindings/module.cpp`; seven enums added to public `qte/__init__.py`;
`test_gui_result_bindings.py` is being strengthened for exact +1 ns, high
uint64 cancellation IDs and risk-rejected large quantities. Terra added
`docs/gui-result-bindings.md` with owned-list and sampling-sequence semantics.
The extension has now been rebuilt successfully using the existing native
`/usr/bin/g++` and venv:

```sh
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest tests/python/test_gui_result_bindings.py tests/python/test_bindings.py tests/python/test_lifecycle.py tests/python/test_research_baseline.py -q
# 20 focused tests passed
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g3b-full-20260928
# 523 passed in 11.04s; scoped async-test execution
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build succeeded and 15/15 CTest passed
git diff --check
# passed
```

Sol initially looked for system CTest and reported it absent; root verified the
existing `.venv/bin/ctest` and ran it successfully with the normal PATH above.
No new dependency was required. Astra G3b review subsequently passed, including
an independent 5-test binding run. The concrete additive G3c design in
`docs/gui-export-v2.md` also passed design review (not implementation review).

## G3c active — 2026-09-29

Usage interrupted Sol/Terra again after partial G3c work. On resuming, verified
`python/qte/research_export.py` and CLI opt-in changes exist; v2 frontend edits
are partial; exporter tests and GUI v2 reader/integration remain unfinished.
Sol owns exporter/CLI plus `test_research_export.py`; Terra owns static assets
and tests; root owns GUI validation/readers/API/tests and checkpoints. No source
or binary upgrade needed for G3c. Astra reviews implementation at its gate.

User requested a 3:24 AM Pacific continuation after the usage reset. No scheduled
automation was successfully created before that interruption. The user manually
resumed at 7:46 AM Pacific on September 29, so work continues now; no duplicate
future schedule was created. This log is a checkpoint, not an automatic timer.

V2 reader guardrails from Astra: wrapper presence cannot downgrade to v1 on
malformed/unknown schema; validate common projections/counts and lineage on one
stable generation without recomputing economics. Positions are signed int64
excluding INT64_MIN; fill/request quantities positive int64; episode cumulative
quantities uint64. Reversal fills can both close/open episodes; scale-in means
episode quantity need not equal its opening-fill quantity. Rejected invalid
requests must remain inspectable. Sequences repeat across collections and may
repeat within sampled carried values; no global uniqueness/contiguity claim.

### G3c integration and independent review — 2026-09-29

Exporter, strict v2 reader, owned-table API and frontend integration are saved.
Default v1 bytes remain unchanged; v2 is opt-in. Initial focused validation:

```sh
.venv/bin/python -m pytest tests/python/test_gui_export_v2.py tests/python/test_gui_run_views.py tests/python/test_research_export.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-g3c-views-20260929
# 51 passed
.venv/bin/python -m pytest tests/python/test_gui_run_api.py tests/python/test_gui_artifact_api.py -q --basetemp=build/test-gui-g3c-http-20260929
# 11 passed; scoped async thread-wakeup approval
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build succeeded (no work required); 15/15 CTest passed
.venv/bin/python -m pytest tests/python/test_gui_export_v2.py tests/python/test_research_export.py tests/python/test_gui_run_views.py -q --basetemp=build/test-gui-g3c-hardening-20260929
# 36 passed before additional review regressions landed
```

Astra found missing structural checks for lifecycle completeness, sampling grid
completeness/staleness, status/reason compatibility and position mark bounds.
Root implemented these, plus final-position/open-episode quantity consistency.
Terra is adding rehashed corruption regressions and a full real-engine reversal /
carry reader fixture. Sol added four real-engine v2 HTTP parity/protection tests.

Gate remains **pending**: Astra also found a duplicated-fact protection bypass
between v1/v2 projections. Sol owns central conservative protection propagation
and persistence for equity, sampled equity, fills and orders, with both-direction
raw-download / interpreted-view regressions. Do not clear G3c until reviewed.

Browser QA uses only the isolated repository from G3a. New `build/owned-reference`
was generated with `--export-version 2` and explicitly registered there. Confirmed
overview sequence labeling, positions and full mark sequence, all 16 closed-trade
fields, empty open-trade headers, and lifecycle table/full record navigation.
No user research root registered or holdout revealed. Temporary QA server/tab
will be stopped/closed at the end of verification. G4 preflight is read-only;
no G4/G5 implementation has started yet.

### G3c gate passed / G4 active — 2026-09-29

The G3c implementation and independent Astra review are complete. Astra's final
independent suites passed 26 reader tests and 21 HTTP tests. Sol fixed and tested
durable bidirectional protection for all four proven legacy/owned projections;
raw downloads, interpreted views, later copies and explicit disclosure share the
classification. Terra's final real-engine reversal/carry fixture passes the full
reader, and all structural findings have regressions.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g3c-full-20260929
# 582 passed in 15.12s; scoped async thread-wakeup approval
.venv/bin/python -m pytest tests/python/test_gui_documents.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-g3c-docs-20260929
# 22 passed after documentation/registry update
git diff --check
# passed
```

Native build and 15/15 CTest passed earlier in this checkpoint; no C++ source
changed. One Sol broader test invocation overlapped Terra's staleness-fixture
edit and reported a failure; the final fixture and fresh full suite pass without
weakening tests. A transient Astra capacity error was retried successfully; final
review was actually completed, not inferred from a failed agent turn.

Browser additionally verified empty open trades and sampled source-event labels.
New isolated v2 artifact ID: `f9d1403bc8404a6fba4d9ec3a247b0f8`. QA server session
93562 stopped with Ctrl-C (130), browser tab closed. No active server remains.
Explicit 200% zoom and native download-save flow remain the earlier manual gaps.

G4 decisions are in `docs/gui-lab-views.md`. Train/validation structured views
only, even after raw disclosure; fixed allowlisted server projections of mixed
files, no arbitrary read/protection override, whole-generation guard. Candidate
and comparison order remains slate order; one selected window required for
comparison/macro/decisions, no stitched returns. Macro reference and availability
must both join the same safe window; decisions use close-time `(start,end]`.
Mixed-summary outcome is now `unknown` in generic catalog metadata so it cannot
leak held-out failure results. Raw file inventory names/counts remain metadata.

Root has saved Catalog.experiment_view, authenticated experiment routes, static
asset allowlisting and `test_gui_lab_api.py`. Sol is building the projector and
reader tests; Terra saved the modular frontend and 20 passing static tests.
Next: finish and integrate projector, run actual-engine API/security regressions,
measure cold/warm paging on the large saved lab read-only, browser-test isolated
lab controls, full suites, then Astra G4 review. Only then begin G5a/G5b.

### G4 gate passed — 2026-09-30

The user resumed manually. Git was clean before the current test-portability
and Unicode hardening changes; the user owns that history, and no Git operation
other than status/diff inspection was performed. Sol added portable 14/15-slate
fixtures; saved ignored artifacts are optional supplemental checks, not required
for a fresh checkout. Their original assertions remain when the artifacts exist.

Astra found a lone-surrogate JSON encoding crash. The shared artifact JSON
boundary now rejects invalid Unicode scalars in keys and nested strings while
preserving valid surrogate pairs. HTTP regressions cover source identity, nested
config and candidate parameters, including JSON-within-JSON. Initial new tests
expected projection 422 but central catalog validation correctly rejects earlier
with the existing unavailable-view 409 contract; separate new assertions cover
both boundaries and verify raw bytes stay blocked. No previous test was weakened.
Astra independently re-reviewed and cleared G4 (18 artifact + 16 HTTP tests,
plus its earlier 19 reader tests).

```sh
.venv/bin/python -m pytest tests/python/test_gui_lab_views.py tests/python/test_gui_lab_api.py tests/python/test_gui_lab_privacy.py tests/python/test_gui_lab_static.py -q --basetemp=build/test-gui-g4-integration-20260929
# recovered previous result: 38 passed in 20.86s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g4-full-20260930
# before Unicode fix: 622 passed in 38.85s
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_lab_api.py -q --basetemp=build/test-gui-g4-unicode-20260930
# initial new-test contract mismatch: 28 passed, 3 failed (explained above)
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_lab_api.py -q --basetemp=build/test-gui-g4-unicode-final-20260930
# 34 passed in 9.87s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g4-gate-20260930
# 632 passed in 42.31s; scoped async thread-wakeup access
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build succeeded (no work), 15/15 CTest passed, build/local-dev only
git diff --check
# passed
```

Browser QA: generated a two-clock, 15-entry isolated lab under
`build/gui-browser-20260930/repository`, deliberately injecting trend-candidate
failures through the QA-only helper while other candidates used the real engine.
Verified slate order, failure/no-trade/benchmark rows, full base/stress errors and
fields, actual nested cost context, explicit safe-window choice, macro paging and
source/vintage details, recorded regime decisions, daily-24h label and window
reset, catalog fallback and raw mixed files still unavailable. At 390px the page
width and scroll width both measured 375px; viewport override reset. No user
artifact was registered or revealed. QA server session 13560 stopped (130), tab
4 closed. Explicit 200% zoom and native save-file flow remain manual gaps.

Terra's read-only measurement of saved `build/strategy-lab-20260922`: 3,309 files,
uncached verifier 1.907s, warm detail 0.013s; first pages 0.013s candidates,
0.012s windows, 0.017s comparison, 0.032s macro, 0.062s decisions. All 6 macro
pages and 21 decision pages remained bounded; later-page totals 0.175s/1.185s.
Legacy 3,093-file lab verification: 1.821s. These are direct Python reader timings,
excluding HTTP/catalog journal overhead; OS disk-cache state is uncontrolled.

Next: write/review concrete G5a contract, then assign disjoint runner/input/API
work. No broker/provider jobs, no new dependencies, no automatic launches on read.

### G5a active — 2026-10-01

User resumed after usage interruption. Sol's first build turn errored before any
job implementation files existed; it was explicitly resumed, not counted as
completed. `docs/gui-jobs.md` records the concrete contract and Astra's design
clarifications: recheck protection at launch, inherit protected input lineage,
fresh CLI process, four source-identity checks, leave output nonexistent, retain
owner lock through supervisor cleanup, no inherited liveness write FD, no
pipe-backpressure deadlock, and no persisted-PID signaling on recovery.

Root added authenticated job routes, app lifespan hooks, catalog input/output
protection helpers and regression files. Sol owns durable scheduling/supervision;
Terra owns stable bounded input snapshots. **Integration is partial:** imports
may be temporarily unresolved until those assigned modules land. Do not report
G5a as usable or run G5b yet. The G4 gate above remains the last full green suite.

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_protection.py tests/python/test_gui_catalog.py tests/python/test_gui_documents.py -q --basetemp=build/test-gui-g5a-protection-20261001
# 33 passed in 0.67s before app job imports were connected
.venv/bin/python -m pytest tests/python/test_gui_requests.py -q --basetemp=build/test-gui-requests-20261001
# 7 passed in 0.15s before app job imports were connected
```

No GUI server/browser remains running. No dependencies, broker requests, Git
history/remote operations, or automatic artifact registrations were performed.

G5a integration update: all assigned modules now exist and imports resolve.
Real HTTP launch/dedup/invalid-config tests pass, with report/fill/equity/order
event/position byte parity against the normal v2 CLI. An initial API run had two
failures: job-first database initialization omitted the unrelated artifacts table,
so catalog input authorization could not query it. Catalog now treats a missing
artifact table as an empty lazy registry; the fresh run passes unchanged tests.

Astra's root-hook review found a publication race after CLI completion but before
derived-output protection, plus corrupt output-protection records. Catalog now
requires durable `succeeded` job state for all owned job artifact reads/registers;
the internal protection hook verifies records before and after writing. Missing,
failed, running or interrupted jobs stay blocked. Noncanonical repeated-separator
and dot-component paths are normalized before the guard and in saved registry
paths; old noncanonical rows are guarded too. Relevant corrupt/symlinked input
disclosure history fails closed. No implicit input or derived-output reveal.

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_api.py -q --basetemp=build/test-gui-g5a-api-first-20261001
# 2 passed / 2 failed, shared-table initialization defect, fixed above
.venv/bin/python -m pytest tests/python/test_gui_job_api.py -q --basetemp=build/test-gui-g5a-api-second-20261001
# 4 passed in 0.90s; scoped async thread-wakeup access
.venv/bin/python -m pytest tests/python/test_gui_job_protection.py tests/python/test_gui_catalog.py tests/python/test_gui_job_inputs.py -q --basetemp=build/test-gui-g5a-lineage-paths-20261001
# 53 passed
.venv/bin/python -m compileall -q python/qte/gui tests/python/test_gui_job_api.py tests/python/test_gui_job_protection.py
# passed at integration checkpoint; not a test-suite substitute
```

Next: Sol finishes scheduler/cancel/restart/storage/provenance regressions;
Terra additionally owns `test_gui_job_worker.py` for real-process watchdog,
timeout/cancellation/forced cleanup and environment/FD checks. Astra reviews
implementation independently. Root owns API/catalog integration and full gate.
Do not advance to forms or claim G5a complete on the four HTTP passes alone.

Further G5a checkpoint: root readers passed 46 tests and native build/15 CTest
passed again; no native source changed. Terra's real subprocess tests now assert
the required timeout state (`interrupted`), exposing the current worker's
`failed` result; this is an implementation fix pending Sol, not a test to weaken.
Owner EOF, cancellation, forced kill of a TERM-ignoring child and exception
cleanup are otherwise covered. Sol also owns Astra's pending fixes for per-call
SQLite/sidecar validation and lock release on initialization failure, plus
post-close submission rejection, signal cleanup and provenance reason accuracy.
No active QTE worker was found by exact executable/module process inspection.

Terra performed read-only G5b preflight (no implementation): keep config-path
jobs, add explicit inline-config origin, exact decimal-string web integers,
shared CLI validation, family-specific fields and a distinct fixed lab handler
with config-only snapshots. Root must settle/review the concrete G5b contract
only after G5a passes its implementation gate.

Coordination update: Sol stopped responding to follow-ups and had not saved the
review fixes. Interrupt returned `pending_init`, not an active editing turn.
Root reassigned the concrete remaining runner fixes/tests to approved Terra and
told Sol not to edit. Saved initial Sol work remains intact. No gate was inferred
from an uninitialized/errored turn.

### Resume — 2026-10-04

Recovered the October 1 files without Git changes. The saved close guards now
reject a closed manager before the opened shortcut and recheck inside submission
locking. All prior agent sessions are gone; fresh Terra/Astra tasks are verifying
the implementation, and Sol is doing read-only G5b preflight. G5a is not cleared.

The preceding turn also added an explicit 64-level JSON nesting limit at the
artifact boundary. A new test initially failed because Python 3.14 accepted the
deep input; the implementation now enforces the bound rather than relying on a
parser recursion exception. The corrected artifact/input run passed 32 tests:

```sh
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_job_inputs.py -q --basetemp=build/test-gui-g5a-json-boundaries-fixed-20261001
```

Current resume work: verify shutdown/protocol/provenance and process cleanup,
add authenticated end-to-end derived-protection coverage, then full Python/native
tests and independent Astra clearance. In particular, Astra reproduced a surviving
TERM-ignoring descendant after the CLI group leader exited; Terra owns that fix.
Do not treat direct-child cleanup alone as complete group cleanup. No G5b forms
are implemented yet, no new packages installed, no broker or remote Git calls.

October 4 verification so far (not the final gate):

```sh
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_job_protection.py tests/python/test_gui_requests.py -q --basetemp=build/test-gui-g5a-root-boundaries-20261004
# interrupted (130) at the known sandbox async wakeup stall; no pass claimed
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_job_protection.py tests/python/test_gui_requests.py -q --basetemp=build/test-gui-g5a-root-boundaries-scoped-20261004
# 49 passed in 0.61s, scoped async access
.venv/bin/python -m pytest tests/python/test_gui_job_api.py -q --basetemp=build/test-gui-g5a-api-20261004
# 5 passed in 1.36s, including actual protected-input/output lineage
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build: no work; 15/15 CTest, 0.08s, build/local-dev
git diff --check
# passed at this checkpoint
```

Astra additionally reproduced dual-pipe EOF being mislabeled as cancellation and
a second TERM interrupting cleanup. Terra is addressing both with process-level
regressions, owned descendant reaping and signal-safe cleanup. A signal test's
incorrect `None` exit-code expectation was tightened to the actual terminated
child code; no assertion was removed to hide the implementation failure. The
final source-stable runner gate and full-suite result are still pending.

### Resume — 2026-10-05

The user resumed after the usage interruption. All three October 4 agent turns
had errored; none was counted as a completed review. The same assigned tasks
were resumed. Saved worker code now records termination in a pending flag rather
than blocking signals around spawn (the latter was rejected because children
inherited blocked TERM/INT). Standalone supervisor tests are present, but need
fresh verification and review. The draft `docs/gui-research-forms.md` describes
G5b.1 shared validators, G5b.2 inline/fixed-lab backend, and G5b.3 forms. It is a
plan, not implemented functionality. Root also saved an additional actual-job
regression for harmless original-file edits after acceptance, not yet run.

October 5 integration checkpoint:

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_api.py tests/python/test_gui_documents.py -q --basetemp=build/test-gui-g5a-api-docs-20261005
# 13 passed in 1.91s, including the new copied-input regression
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5a-full-20261005
# 711 passed in 55.88s; baseline only, overlapped final runner fixes
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no work; 15/15 CTest in 0.05s
```

Final review additionally reproduced a stale-descriptor race: `RunningJob.wait`
closed control pipes while the manager still exposed the active job, allowing a
late cancel/close to act on a reused descriptor. Terra added per-job descriptor
locking, atomic invalidation and one-shot cancellation plus a regression. This
post-baseline fix requires the source-stable full rerun. Independent review also
checks actual standalone orphan adoption, inherited-lock exclusion, repeated
signals, and worker completion-versus-cancel, not only fake scheduler objects.
The G5b draft now states authorization-before-validation, exact captured bytes,
honest inline origin and the compatibility tradeoff of earlier sampling errors.

### G5a gate passed; G5b.1 active — 2026-10-06

Recovered the completed final suite after the user resumed. Astra explicitly
passed G5a implementation review: 72 focused tests passed in 13.24s, then four
final hardened regressions passed in 5.16s. Actual standalone-process tests
cover descendant adoption/reaping, repeated TERM, unblocked child TERM/INT and
inherited owner-lock retention; descriptor reuse is asserted, not assumed.
Terra's last final message errored on usage after its saved 49-pass checkpoint;
that error was not substituted for review. Astra's explicit PASS and root's
complete suite provide the gate evidence.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5a-gate-20261005
# 711 passed in 52.97s; source-stable final gate, recovered October 6
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# final native checkpoint already passed: no work, 15/15 CTest, 0.05s
git diff --check
# passed
```

G5a provides the fixed authenticated offline API, not launch forms. No GUI server
was started for this API-only milestone; HTTP integration exercised actual v2 CLI
jobs and byte parity, failure visibility, copied inputs and protected lineage.
No user research root was registered/revealed, no new dependency installed, no
Git history/remote operation or broker request performed.

G5b.1 now starts under the reviewed `gui-research-forms.md` contract. Sol owns
single-run preparation and CLI extraction; Terra owns internal byte-loading
adapter seams and pure lab preparation. The shared preparation does not execute
an engine. Moving invalid sampling checks earlier is intentional; valid economics
and exports must retain parity. Result-dependent C++ checks remain authoritative.
Do not add inline endpoints, lab runner changes or forms before this small gate.

G5b.1 implementation checkpoint: the adapter byte seams and deep-copied pure
lab preparation are saved. Review preserved CSV invalid-option validation before
source-file opening, including through the CLI capture path. Single-run
preparation owns copied configuration, canonical data, a fresh strategy/config,
engine config and optional sampling settings; it does not execute a backtest.
Shared configuration checks do not pretend to validate future equity-event
staleness or undefined metrics; those remain in real-result C++ analytics.

Terra reported 36 focused passes before the concurrent CLI extraction. A later
35-pass/1-failure run hit the existing source-mutation guard during concurrent
Sol edits; this is not waived or weakened. Rerun after all source files settle.
Full G5b.1 tests and Astra review are pending. G5a's 711-pass gate above remains
the last completed full gate, not a claim that this extraction is already done.

After the next usage interruption the same agents resumed. Terra's source-stable
rerun cleared the earlier concurrent-edit failure:

```sh
.venv/bin/python -m pytest tests/python/test_research_adapters.py tests/python/test_lab_config.py tests/python/test_adapters.py tests/python/test_strategy_lab.py -q --basetemp=build/terra-g5b-sourcequiet-20261006
# 36 passed in 6.56s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build no work; 15/15 CTest in 0.08s
git diff --check
# passed at checkpoint
```

G5b.1 independent pre-gate evidence: Astra compared the pre-extraction HEAD CLI
with the refactor in isolated namespaces. All seven strategy types traded in
v1/v2 (14 pairs), saved Alpaca SMA ran in both versions (2 pairs), and a small
fixed lab ran (1 pair). All 373 exported file pairs were byte-identical, including
metadata/completion, without normalization. Evidence stays ignored under
`build/astra-g5b1-parity-8h8ZrP/`. Fifty focused tests passed independently.

The broader regression exposed an existing reversal-test patch target at the old
CLI import location. An initial private-constructor injection compatibility fix
was rejected as unnecessary production surface. The approved correction moves
only that test's patch target to `qte.research_config`; its real reversal and
accounting assertions remain unchanged.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5b1-gate-20261006
# 754 passed, 1 failed in 56.15s; NOT a passing G5b.1 gate
```

Failure: `test_standalone_subreaper_adopts_descendant_before_releasing_lock`
did not see its fixture's final readiness file. The child waits for the
descendant JSON path to exist, then immediately parses it; existence alone does
not guarantee the writer has finished. Terra owns diagnosis and an atomic
readiness/failure-cleanup fix in `test_gui_job_worker.py`, with Astra review.
Do not conflate this with a passing production cleanup test. Full stable rerun
and final review are required before starting G5b.2.

### G5b.1 gate passed — 2026-10-06

The user resumed and root recovered the final full-suite result. Atomic readiness
publication fixed the fixture race without extending its 3-second readiness
deadline or weakening any process-group/lock assertion. Setup-failure cleanup
allows 8 seconds (beyond the worker's 2-second TERM grace) and closes owned lock
FDs. No production supervisor change was needed. The reversal test now patches
the extracted module only; no constructor-injection parameters remain.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5b1-final-20261006
# 755 passed in 53.32s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# October 6 native checkpoint: no work, 15/15 in 0.08s
git diff --check
# passed
```

Astra explicitly reviewed the latest code and fixture fixes: 76 focused
preparation/export tests and 18 worker lifecycle tests passed. It repeated the
independent HEAD comparison on final source: 17 paired runs / 373 byte-identical
files, no normalization, stable identity. Final evidence directory:
`build/astra-g5b1-final-parity-QNQuYN`. G5b.1 is complete, not the whole G5b.

Next: G5b.2a preview boundary (no new launches). Sol owns form conversion,
metadata/assumptions and unit tests; Terra owns bounded inline capture and tests;
root owns HTTP wiring, authorization and integration. Astra reviews independently.

### G5b.2a resume checkpoint — 2026-10-07

Saved strict form conversion/metadata/assumptions, bounded wire/data capture,
explicit nullable lab identity validation, and authenticated metadata/preview
routes. Preview orders capture → protection authorization → shared preparation;
it does not execute research or create snapshots/jobs. Exact captured wire config
hash and typed config fingerprint remain distinct. Invalid protected data does
not disclose financial/adapter diagnostics before an explicit existing reveal.

Before the interruption Terra reported 35 focused capture/legacy-input passes;
Sol reported 83 shared preparation/form passes. Astra reviewed capture (35
passes) and found CSV field-limit exception sanitization, which Sol fixed.
Agents then hit usage limits before the final independent preview decision.
Root's integration tool cell was lost across the interruption: its result is
unknown, not a pass. Rerun under a new unique build test directory. Root also
fixed the lab effective-configuration schema version DTO exception on resume.
Full stable gate and final Astra review are still required. No new launches yet.

October 7 root integration first returned 103 passed / 2 failed in 1.21s.
Both were newly written lab-success fixtures using 96 bars, below the existing
128-bar minimum. Corrected fixture to 128; validation rules and all assertions
remain intact. Added exact ordered registry-key and CandidateConfig field-inventory
drift assertions. Final source-stable root checks:

```sh
.venv/bin/python -m pytest tests/python/test_gui_form_api.py tests/python/test_gui_form_protection.py tests/python/test_gui_form_inputs.py tests/python/test_gui_research_forms.py tests/python/test_gui_job_protection.py -q --basetemp=build/test-gui-g5b2a-focused-final-20261007
# 105 passed in 1.13s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5b2a-full-20261007
# 837 passed in 53.16s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no work; 15/15 passed in 0.07s
git diff --check
# passed
```

API/full pytest used scoped execution approval for the documented sandbox async
wakeup failure. No packages, broker calls, user exports or Git history changed.
Final independent Astra review is pending at this checkpoint; this is not yet
permission to add a launch path. The next backend slice should first add immutable
inline snapshots and compatibility tests without exposing a new launch route.

### G5b.2a independent review passed — 2026-10-07

Astra explicitly passed current code review with no blockers, verified the final
DTO/fixture/drift corrections, and retained its independent 122-unit pass.
Its additional API rerun waited on a separate `timeout`-wrapper approval and was
aborted without a result; it is not counted. Root's 105 focused / 837 full and
15 native results above are the completed integration gate. Reviewer stopped
its owned stalled session (exit 130); bounded diagnostic ended at timeout (124).

Now G5b.2b snapshot-only slice under the documented small gate: new explicit
inline origin/versioned immutable inventory, existing legacy snapshot bytes and
fingerprints unchanged, no new job submission or worker surface. Gate before
manager/worker/lab verification integration. No additional dependency approval
or installation is needed.

Snapshot-slice interruption checkpoint: Sol saved the pre-change legacy-byte,
manifest/inventory and `_fingerprint` golden test (1 pass before interruption).
Root saved two real CLI parity tests in `test_gui_inline_snapshot_cli.py`; they
await the new snapshot module and have not run yet. At the next user resume,
`inline_inputs.py` had not been saved, so the current working test tree was not
collectable as a full suite. Terra/Sol/Astra resumed their same owned slice.
837 Python / 15 native remains the last completed full gate, not a claim about
the unfinished snapshot implementation. Finish the module before a new full run.

Snapshot source was subsequently saved and stabilized. `inline_inputs.py` adds
the explicit v2 envelope/manifest, one canonical wire identity, recompiled typed
identity, copied-data path only for research, honest null lab data fields, and
pre-write/verify bounds. Only late dispatch was added to legacy `job_inputs.py`;
no launch route, manager or worker change. Reviewer/root findings on path
association, malformed types, fake source filename and symmetric size limits
were fixed, with regressions. Root's separate invalid suite now has 42 cases for
forged prepared values and rehashed semantic corruption. Partial snapshots fail
verification and cannot be overwritten/reused.

Terra reported 92 combined focused passes before the latest usage interruption.
The root session result was unavailable after resume, so it was rerun:

```sh
.venv/bin/python -m pytest tests/python/test_gui_inline_inputs.py tests/python/test_gui_inline_snapshot_invalid.py tests/python/test_gui_inline_snapshot_cli.py tests/python/test_gui_snapshot_compatibility.py tests/python/test_gui_job_inputs.py -q --basetemp=build/test-gui-inline-snapshot-resume-20261007
# 74 passed in 3.06s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no work; 15/15 passed in 0.04s
git diff --check
# passed
```

Astra independently passed the latest snapshot-only code review: 74 focused
tests passed in 3.03s under `build/test-g5b2b-astra-snapshots-reviewed-20261007`,
with no remaining blockers. Approval covers snapshots only, not future launch
or worker integration. Full Python gate has been launched under
`build/test-gui-inline-snapshot-full-20261007`; record its result before moving
to the next backend slice.

### Inline snapshot gate passed — 2026-10-07

Full result recovered before further edits:

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-inline-snapshot-full-20261007
# 894 passed in 55.70s
```

Together with 74 focused, 15 native and Astra's explicit latest-source PASS,
this completes the snapshot-only slice. Existing CSV/Alpaca v1 inventory and
research retry hashes remain frozen by golden tests. Research economic artifacts
match direct CLI output after original data changes; every small synthetic-lab
artifact matches direct CLI bytes after original config changes.

Next active backend work: exact alternative inline request shape, fresh
capture→authorize→shared preview→fingerprint comparison→snapshot submission;
only fixed research/lab worker dispatch; new-lab-only completeness/provenance
verification. No browser controls, external provider calls or broker actions.

### Backend recovery — 2026-10-08

The preceding resume was blocked by an unresponsive command runner, including
read-only `pwd`. Its attempted checkpoint write and pending test result could
not be confirmed and were not counted. The next user continuation restored
terminal access. Read-only inspection found a clean checkout at `abc7586 oct7`
with the saved backend implementation; Codex performed no Git history or remote
operation. Last completed full gate remains the snapshot-only 894 Python / 15
native above, not a pass for the newer launch changes.

Root's saved work includes exact path/inline envelopes and individual size
limits, compile-only fingerprint comparison after protection authorization but
before financial/adapter diagnostics, and same-captured-bytes submission. Added
retry regressions now require stale409 after source edits and same-key conflict
after obtaining a fresh changed-input preview; no second job may be created.

Sol's saved strict fixed-lab verifier matches accepted configuration/current
fixed factories, exact candidate parameters/history, actual macro identities,
stored generator/data/window declarations, integer summary counts, comparison
columns and full attempt/failure/log inventories. Its recovered focused run
passed 14 tests, including hourly+daily_24h gap-shock/multiple-regime coverage.
Terra/Astra identified one remaining worker issue: generic snapshot attribute
fallback could accept False as legacy research. Terra is replacing it with real
v1/v2 snapshot type checks and adapting fixtures without weakening assertions.
Wait for source quiet, then focused API/lifecycle, full Python/native, and final
independent review. No G5b.3 browser work before the backend gate passes.

### Backend integration gate in progress — 2026-10-08

The lost API session could not be recovered, so it was rerun rather than counted.
The real-snapshot worker guard is saved and independently Astra-reviewed PASS.
Review also passed strict fixed-lab verification and stale/rekeyed API retries;
approved process cleanup/signal/descriptor ownership remains unchanged.

```sh
.venv/bin/python -m pytest tests/python/test_gui_inline_job_api.py tests/python/test_gui_job_api.py tests/python/test_gui_form_api.py tests/python/test_gui_requests.py -q --basetemp=build/test-gui-inline-api-recovered-20261008
# 41 passed in 6.63s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no build work; 15/15 passed in 0.08s
git diff --check
# passed
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-inline-backend-full-20261008
# 934 passed, 1 failed in 67.94s
```

The failure is `test_owner_eof_terminates_owned_child_and_uses_fixed_environment`:
its helper waits only for marker existence, then signals owner loss while the
child can still be between opening and writing the readiness file. The FD-probe
assertion sees empty text rather than `closed`. Terra is fixing the readiness
handshake without weakening FD/environment/cleanup assertions; Astra reviews.
No browser edits yet. Sol's bounded forms/jobs ownership plan is prepared;
implementation must wait for this gate. Tests use scoped local execution for
the previously documented async wakeup issue, not Full Access or installation.

### Backend gate passed; browser implementation started — 2026-10-08

Terra atomically publishes both parent FD-probe readiness and descendant PID
markers after complete writes; no assertions, deadlines or production behavior
were weakened. Astra explicitly passed the final fixture review before its turn
hit usage limits. That review message and the full result were recovered on the
next user continuation, rather than assuming the errored final message passed.

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_worker.py -q --basetemp=build/terra-worker-markers-atomic-20261008
# Terra: 22 passed in 11.99s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-inline-backend-final-20261008
# root: 935 passed in 67.55s
```

Together with the 41 API / 15 CTest results and independent backend reviews,
G5b.2b is complete. Browser changes now begin under the ownership above. Planned
routes are `/research/new/run`, `/research/new/lab`, `/research/jobs/{id}`.
No new dependencies, external data calls, broker actions or Git operations.

### G5b.3 local test build — 2026-10-08

User narrowed this turn to finishing the existing test build efficiently and
requested a full data-flow/strategy guide. Sol built the metadata-driven forms;
Terra built paged job views and bounded polling. Root integrated routes/assets,
added module state-machine tests, completed sparse form styles, and kept optional
CSV mapping collapsed. Astra reviewed fixes for exact wire values, stale preview,
uncertain retry identity, cancel DTOs, view disposal, polling/focus and error
recovery; latest module review has no code-level blockers.

```sh
.venv/bin/python -m pytest tests/python/test_gui_research_browser_routes.py tests/python/test_gui_research_forms_static.py tests/python/test_gui_research_jobs_static.py tests/python/test_gui_static.py tests/python/test_gui_app.py -q --basetemp=build/test-gui-browser-focused-recovered-20261008
# 40 passed in 0.42s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-test-build-final-20261008
# 954 passed in 71.13s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no build work; 15/15 passed in 0.05s
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/research_ui.test.mjs
# 6 passed; no npm packages or runtime installed
sed -n '/^```python$/,/^```$/p' docs/test-build-guide.md | sed '1d;$d' | .venv/bin/python -
# runnable guide example: 2 fills, total return 8.991000000002636e-05
git diff --check
# passed
```

The new route test initially assumed an incorrect `default-src 'none'` policy;
it was corrected to assert the existing reviewed same-origin CSP rather than
changing production security. A minimal DOM test-double needed action dataset
support; six executable module tests now pass. They are not layout/browser tests.
Scoped test execution addresses sandbox thread/spawn restrictions. Existing
bundled Node was discovered read-only; Python remained in `.venv` throughout.

Browser smoke used only `build/gui-browser-20261008/repository` and the local
loopback server. Actual controls validated the example SMA run, enabled launch
only after assumptions, observed success, explicitly registered it and rendered
event equity. A synthetic hourly lab with 128 bars/window and exact maximum
62-bit seed also succeeded and registered; its view retained all 15 candidates,
synthetic exclusions and withheld test-role values. No user catalog/export was
registered or revealed. This does not claim exhaustive browser cancellation,
restart, narrow-screen or 200% zoom acceptance; those remain manual follow-up.
Automated API/process tests still cover these backend safety boundaries.

Astra's final bounded review of root navigation, collapsed CSV mapping, form
styles and the guide is clear for the stated test-build scope, without claiming
the outstanding extended browser acceptance is complete.
Final style/static check: 31 passed in 0.06s using
`.venv/bin/python -m pytest tests/python/test_gui_research_forms_static.py tests/python/test_gui_research_jobs_static.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-test-build-layout-final-20261008`.
`git diff --check` passed. Temporary browser tab closed and owned QA server
stopped with Ctrl-C (exit 130). No background GUI server remains from this task.
