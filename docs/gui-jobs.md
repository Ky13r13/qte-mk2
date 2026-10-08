# Bounded local research jobs (G5a)

Status: G5a implemented, full-suite tested and independently reviewed by Astra.
This extends the read-only library with explicit local launches only. G5b adds
forms and the fixed synthetic lab handler after the runner gate. No test,
download, account, arbitrary command or strategy-import handler is exposed.

## Request and ownership

`POST /api/v1/jobs` accepts exactly `schema_version: 1`, `kind: research_run`,
`config_path` (repository-relative JSON), `idempotency_key` (16–128 ASCII
letters/digits/hyphen/underscore), and `timeout_seconds` (integer 1–1800).
The default UI timeout will be 1800; the API requires it explicitly. The existing
session, exact Origin and request token are mandatory. Reads never launch work.
Unknown fields and query parameters fail closed.

The runner invokes the fixed equivalent of `.venv/bin/python -m qte run
--config <snapshot> --output <new artifacts directory> --export-version 2`.
The CLI remains the authority for strategy/data/config validation and economics.
G5a validates only the input envelope needed to copy local data; no second set of
financial validation rules. G5b may extract shared validation for form previews.

`GET /api/v1/jobs` is paged (25 default, 200 maximum); `GET /api/v1/jobs/{id}`
returns one record; `GET /api/v1/jobs/{id}/log?offset=0&limit=65536` returns bounded
sanitized text and a cursor. `POST /api/v1/jobs/{id}/cancel` accepts only schema
version. All are authenticated, no-store and reject ambiguous queries. DTOs
include opaque ID, kind, state/phase/reason, timestamps, timeout, source identity,
input hashes, request identity, exit status and repository-relative output path.
Exact nanosecond/PID/large-count values are decimal strings. No config bytes,
source data, credentials or raw traceback is returned by job metadata/log routes.

## Inputs and idempotency

Source config/data are bounded regular files under `examples/`, `configs/`,
`data/` or non-reserved `build/` paths. Hidden components, symlinks, secrets,
databases and GUI/private build state are forbidden. Config-relative `..` may be
normalized only while remaining inside approved repository paths. Stable
descriptor reads reject races/FIFOs. Config limit 1 MiB, data limit 16 MiB.

Record canonical config + input-content hashes, original relative paths and
source identity. Known protected inputs (including copied hashes) cannot be
executed before the existing explicit disclosure workflow permits them. Launch
does not imply an untouched holdout or authorize disclosure. Never auto-register
or reveal existing user research roots.
Recheck both hashes immediately before launch, including any registrations made
while queued. Outputs derived from protected/disclosed inputs inherit protected
content classification before they can be registered or exposed. Do not create
an implicit reveal of those outputs; metadata records disclosed-input lineage.

One idempotency key maps durably to one canonical request/input fingerprint.
An identical retry returns the same job, even if the source code has since
changed; changed config/data/kind/timeout returns 409. Persist before starting.
An edit to original files after acceptance does not change copied inputs.
Queued/running snapshot or code mutation fails provenance checks; it never
quietly produces a successful mixed-source run.

Each job owns a fresh `build/gui/jobs/<uuid>/inputs/` directory. Leave `artifacts/`
nonexistent for the CLI's exclusive creation.
Preserve original config bytes and an effective config with its data reference
rewritten to the copied input. Store hashes of every snapshot and create files
exclusively, read-only after writing. Outputs are never overwritten or erased.
This is application immutability, not protection from the workstation owner.

## Lifecycle and recovery

One active run and FIFO queue of at most 20 pending jobs. SQLite metadata shares
the existing UI database, not the financial journal. A repository-local advisory
owner lock prevents two GUI processes from scheduling concurrently. Startup
marks previous queued/running jobs interrupted; no automatic retries/PID attach.
Idle library use may leave the job subsystem uninitialized; first job operation
can start recovery, but may not launch recovered work.

States: queued -> running -> succeeded | failed | canceled | interrupted.
Record request ID, worker launch identity, executable/cwd, creation/start/end,
exit code and known phase. No invented progress percentage or pause/resume.

A supervisor separate from the engine process watches a parent-liveness pipe
(not a Python thread blocked behind C++'s GIL). On EOF, cancellation or timeout,
terminate only its owned CLI process group, then kill after bounded grace.
Normal server shutdown also stops children. Never signal a persisted PID after
restart. Preserve verified completion if it wins the cancellation race.
The liveness write end is close-on-exec and never inherited by either child.
The supervisor retains the owner lock until its CLI process group is reaped,
including owner death; restart fails closed while that cleanup is still active.

The current runner targets Linux (the existing Fedora environment). Its private
supervisor enables child-subreaper ownership so orphaned members of the CLI's
process group can be reaped locally, rather than relying on PID 1. If that
facility is unavailable, execution fails closed before starting the CLI. TERM
and INT are recorded without blocking those signals in the child; repeated
signals cannot interrupt group cleanup. A process stuck in uninterruptible
kernel I/O may keep the owner lock held even after KILL is requested: recovery
must not launch another owner while the prior group still exists.

Success requires exit zero, unchanged source/snapshot hashes and verified
`research_export_v2` completion. Otherwise keep partial artifacts and a sanitized
failure reason. Automatic registration is limited to a newly created successful
job output, if used; existing exports always require explicit registration.
Compare accepted, prelaunch, postrun and exported manifest source identities.
The fresh CLI process avoids interpreting already-imported server modules as
proof of unchanged code. Original-file edits after acceptance remain harmless;
the copied snapshots, not originals, are rechecked. Fingerprints exclude source
identity (immutable provenance, not a retry-conflict field).

Children inherit a minimal fixed environment (locale, repository-local temp/
cache settings and required venv execution settings only), never arbitrary parent
environment, Python injection settings or broker credentials. Use argv with
`shell=False`, no user-selected executable/module/options. Worker diagnostics are
bounded to 64 KiB and sanitized; logs expose known phases and generic validation/
execution errors, not arbitrary data-bearing CLI text. No dependency installs.
Drain/discard all subprocess output even after any capture limit, or redirect it
to the null device; do not let bounded capture deadlock the worker's output pipe.

## Gate

Deterministic tests: valid CLI/output parity, invalid config, no overwrite,
config-relative paths, exact integers, dedup/conflict, queue capacity/order,
credential stripping, filesystem races/symlinks, protected-input denial,
cancel-before-start/during-run/completion race, timeout, owner crash/restart,
source/input mutation, incomplete/rehashed-invalid output, log bounds and API
auth/query rejection. Full native/Python suites and Astra review before G5b.
