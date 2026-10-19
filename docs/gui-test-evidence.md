# Test evidence — G8a

G8a.1 implements an internal parser, identity capture and record verifier.
G8a.2a adds trusted discovery, immutable requests and completion-last writing.
Both passed independent review and offline acceptance. G8a.2b adds supervised
test jobs. G8a.3 adds explicit System controls and disclosure-bound views;
generic catalog registration and raw test-output downloads remain disabled.

## Record contract

`test_evidence_v1` has exactly four files: `manifest.json`, `junit.xml`, `log.txt`,
and `complete.json`. The completion marker contains SHA-256 hashes of exactly
the other three. Verification re-derives results from JUnit, never a duplicated
summary or a console phrase. Complete evidence can describe failed tests.

The strict manifest records runner (`pytest` or `ctest`), job ID, original
repository/cwd and exact fixed argv, tool/platform versions, UTC-nanosecond
decimal timestamps, process exit/termination, log truncation, and before/after
source, test, binary and build/test-configuration hash maps. All four recorded
maps must be nonempty. `identity_scope=explicit_files_v1` deliberately does not
claim complete dependency coverage; `source_to_binary=unknown` is mandatory.
Checksums establish consistency, not execution authenticity or build provenance.

The command descriptor fixes existing `.venv` executables, the `dev` CTest preset
or `tests/python`, an absolute job-local JUnit destination, and pytest's unique
local basetemp/cache. Absolute JUnit paths avoid CTest's preset-relative output
resolution. No manifest command is executed by this reader. The worker rebuilds
its argv from its trusted job request, not imported evidence.

## Interpretation and limits

- Process/test outcome, during-run identity stability, current identity match,
  and source-to-binary correspondence are separate. Failed/skipped/interrupted
  outcomes remain visible even when identities change. `supports_passing_run`
  only qualifies reported passing cases against unchanged listed files; it is
  not a current-source, complete-coverage or compiled-from-source pass badge.
- Zero cases and all skipped/xfail cases are not passes. Collection/internal
  errors and contradictory success exits fail closed. A module-level pytest
  skip can produce a skipped JUnit row with exit 5; it is not a passing test.
- Preserve testcase order/ordinal and duplicate names. Pytest call failure plus
  teardown error can produce duplicate rows; multiple errors can share one row.
  Marker counts and dominant per-row outcome counts are recorded separately.
  CTest cases count executables, not individual C++ assertions.
- Explicit `pytest.xfail` markers remain skipped. Non-strict XPASS cannot be
  identified from ordinary JUnit. `xfail_strict=true` changes the default, but
  a test's explicit `strict=False` still overrides it; do not invent an XPASS
  classification or claim exhaustive expected-failure validation.
- Accept only the observed pytest/CTest XML profiles. Reject malformed XML,
  DTD/entities, namespaces, processing instructions, unsupported structures,
  inconsistent counts and invalid durations. XML is capped at 16 MiB, 100,000
  nodes, 10,000 case records and depth 16. Retained issue text is bounded plain
  text; untouched raw JUnit/log bytes remain untrusted inert evidence.
- Capture only explicit repository-relative paths through no-follow stable
  reads, checking the whole listed set again afterward. Duplicate paths,
  symlinks, special files, changes and byte/count limits fail closed. Empty
  generic comparison groups mean unknown, not same. No source discovery,
  compilation, installation or filesystem repair occurs.

## Trusted execution inputs — G8a.2a

`prepare_test_run` supports only `pytest` and `ctest`. It rediscovers fixed file
sets and captures hashes twice, detecting changed bytes and new/deleted files.
Source covers headers, C++ sources, bindings and Python/static assets. Test scope
is C++ tests, or Python tests/fixtures plus documentation, examples and scripts;
root/test conftests are included when present. Build scope includes project
configuration, requirement inputs/locks, venv configuration and, for CTest,
generated definitions/cache/Ninja/compiler configuration. Unsupported files or
config overrides fail closed rather than silently disappearing from coverage.

Python records the actual loaded, repository-contained extension and editable
hook, checking the source mapping and rejecting its automatic rebuild option.
The trusted `lib64` alias resolves to the canonical contained `lib` path before
no-follow capture. CTest records the wrapper, actual packaged runner/module and
every discovered executable, not a guessed binary directory. Fixed show-only
discovery checks ordered names/commands and the current working-directory-only
property profile. The current generated-CMake grammar is checked before
execution; hooks, includes, wrappers, extra arguments and redirected presets are
unsupported. Discovery is bounded and receives no inherited credentials.

This remains an explicit scope, **not all dependencies**. The system Python
executable/standard library, dynamic libraries and most installed packages are
not hashed; versions describe the active Python, installed pytest/CMake packages
and platform. Source-to-binary correspondence remains unknown. Discovery checks
trusted repository code/configuration, not an inert third-party import.

Schema-4 snapshots contain only `test-request.json` and `input-manifest.json`
under a fresh job's private `inputs/`. They pin captured request bytes, never
supplied shell commands. Writes recheck preflight before creating the directory;
loads are inert, exact-inventory/hash checked and owned. Dispatch rechecks fresh
identities and then the pinned snapshot. Partial directories are retained and
never reused. The job manager uses these helpers for private `test_run` requests.

The runner creates `artifacts/` and produces `junit.xml` there. The writer
requires exactly that initial file, binds paths to frozen manifest bytes, then
exclusively writes private log/manifest files and the completion marker last.
Whole-file state and canonical-directory checks surround completion. Failed or
skipped tests can have complete evidence. Late mutations or I/O failures retain
partial output; marker presence alone never proves validity. Readers must verify
the whole record again. The writer proves consistency, not execution authenticity.

## Private supervisor — G8a.2b

Typed requests share the existing FIFO queue, single-running-job lock,
idempotency, cancellation and restart-interruption policy. Only installed `.venv`
pytest/CTest commands run, with no build, install or arbitrary arguments. The
worker disables pytest plugin autoload and removes inherited Python/pytest and
provider settings. Temporary/cache paths are job-local.

Combined stdout/stderr is continuously drained; only its first 16 MiB is retained
in private `runner.log` and, when finalization succeeds, `artifacts/log.txt`.
Overflow is labeled, not a deadlock or silent full-log claim. Cancellation,
timeout, owner loss and normal leader exit clean up process groups and adopted
nested-session descendants before returning the scheduler lock. This is a Linux
trusted-test runner, not a sandbox for arbitrary test code.

Dispatch and worker launch recheck the pinned request. Completion captures a
fresh identity, never substituting the starting identity when discovery fails.
Valid failures/skips can retain complete evidence; invalid/missing JUnit or
failed discovery retain only partial output. The parent accepts success only
after independently verifying evidence against the request and current scope.
Job status, test outcome and provenance remain separate.

Generic job DTOs/logs expose only sanitized lifecycle status, not raw test output
or identities. The catalog refuses test-job roots/subpaths, even after success.
Authenticated generic status/cancel operations continue to work for these jobs.
Raw records remain repository-local, outside generic download access.

## System controls and disclosure — G8a.3

System lists only accepted `test_run` database rows, with bounded pagination.
It never scans old `build/` outputs or treats narrative logs as machine evidence.
Opening the page performs no test launch or preflight/discovery. Explicit launch
accepts only runner, bounded timeout and idempotency key; the server prepares the
trusted request. The browser uses a 300-second timeout and retains the identical
request/key after uncertain submission. Status refresh is manual; cancellation
uses the existing supervisor. CTest runs existing binaries without compiling.

Evidence content—including counts, names, issue text, versions and identity
comparisons—is protected by default. Explicit reveal warns that trusted test
output may contain sensitive or held-out text, and appends an outside-protocol
disclosure tied to the accepted request and hashes of all four evidence files.
Every read verifies the binding again. Replaced valid bytes require a new reveal;
partial/corrupt output remains unavailable. This is local disclosure tracking,
not automatic secret redaction or proof of untouched holdout. Raw logs/JUnit are
not served through the GUI even after reveal.

The revealed view separates lifecycle state, test outcome, during-run identity
stability and current-match observation. Complete failed/skipped/interrupted
records remain viewable. Current match starts unknown; an explicit POST captures
current preflight identities and tool-version match with an observation timestamp.
It is not a continuously fresh badge, and failures remain unknown. Source-to-binary
correspondence remains unknown. Case detail pages preserve case order/duplicates,
limit total text to 65,536 characters and events to 200, and visibly label
truncation. An oversized numeric duration is omitted, never numerically truncated.

All routes require the session request token; POST also requires the exact Origin:

- `GET /api/v1/system/tests`: filtered list; `offset`/`limit` (maximum 200).
- `POST /api/v1/system/tests`: `{schema_version:1, runner, timeout_seconds, idempotency_key}`.
- `GET /api/v1/system/tests/{job_id}`: lifecycle and guarded, paged evidence.
- `POST .../{job_id}/reveal`: `{schema_version:1, confirmation:"reveal outside protocol"}`.
- `POST .../{job_id}/check-current`: `{schema_version:1}`; disclosed evidence only.

No route accepts executable paths, shell commands, pytest arguments, environment
overrides or arbitrary artifact paths. Runner regression tests never recursively
launch the entire Python suite from inside itself.

## Remaining increments

G8b follows with the bounded existing benchmark: timing is not strategy merit,
and its `hold` workload is a no-op, not buy-and-hold.
