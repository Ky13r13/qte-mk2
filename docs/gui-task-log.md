# Active GUI implementation checkpoint

Last checkpoint: 2026-09-28, G1/G2/G3a reviewed; G3b implementation active.
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

## Baseline / environment

Repository `/home/glsu6/Documents/qte`; native compiler `/usr/bin/g++`, CMake
`build/local-dev`, Python only `.venv`. SSL and scoped RPM verification passed
after reboot; original failure retained in `docs/gui-g0-preflight.md`.
Baseline: 15/15 CTest executables, 419 Python tests. Approved hash-checked wheel
installation completed for all 12 packages in requirements-gui-test.lock using
the existing venv, no cache and repository-local TMPDIR. Sandbox index access
failed; scoped network approval allowed the install. No system package changed.

## File ownership during active build

- Sol task `gui_sol_g1` (`gpt-5.6-sol`), now assigned G3a: `run_views.py`,
  `charts.py` and run-view/chart tests. G2 readers/catalog/disclosure also owned
  for narrowly required fixes; coordinate changes with root.
- Terra task `gui_terra_g1` (`gpt-5.6-terra`), now G3a: static assets/tests,
  single-run chart/metrics/table/download views. Labs follow in G4.
- Root: `app.py`, request/DTO helpers, artifact API routes/tests, CLI/server,
  docs/checkpoints, integration and real-browser validation.
- Astra task `gui_astra_g1_review`: completed G1/G2 review. G3a review started,
  found the invalid/protected-run navigation regression described below, then
  hit usage limits. Sol/Terra follow-up tasks also errored at the same limit.
  Root saved local follow-up fixes/tests; none is independently re-reviewed yet.
  Do not assume agents survive a pause or that an errored review passed.

## Agreed G1 interface

`qte.gui.app.create_app(repository: Path, *, port=8765, code_sink=print,
clock=time.monotonic) -> Starlette`. Packaged fixed assets: `index.html`,
`app.css`, `app.js`. Header `X-QTE-Token`, HttpOnly cookie `qte_session`.
Auth routes `/api/v1/session` POST/GET/DELETE; library/capability DTOs in
`docs/gui-contracts/v1/`. Zero-radius neutral four-section UI; research/data/
system workflows unavailable until their milestone. No arbitrary static mounts.

## Next actions

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
5. G3a review completed after the usage pause; NOW G3b read-only owned result
   bindings, then G3c additive versioned exports.
   Never mutate legacy artifacts or change engine accounting/execution. Old
   missing fields remain explicitly not recorded. G3c/G4/G5 are not started.
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
No new dependency was required. Astra G3b review is pending. A concrete additive
G3c design is drafted in `docs/gui-export-v2.md`; no G3c runtime change yet.
