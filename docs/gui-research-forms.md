# G5b research forms and fixed synthetic lab jobs

Status: G5b.1 shared validators, G5b.2a authorized previews and G5b.2b immutable
inline jobs/fixed lab workers are implemented, tested and independently reviewed.
G5b.3 forms/job views are implemented for the local test build; extended browser
acceptance remains pending as recorded in the task log. G5a passed its full
native/Python and Astra gates. This
contract adds no command, strategy factory, output path, environment, arbitrary
configuration, network, broker, or account-control surface.

G6a adds the fixed `dataset_validate` kind: the same `data` form, without strategy,
engine or analytics sections. Optional `data.expected_sha256` pins captured bytes
for validated-source reuse in research runs. It is part of the submitted config,
preview fingerprint and immutable snapshot; it never grants disclosure access.
Data → Validate local dataset uses the existing preview and job endpoints.

G6b adds `download_alpaca`: exact `symbols`, `timeframe`, UTC `start`/inclusive
`end`, explicit `feed` and `action_free: true`. No input path or credential
field is accepted. The pure preview works offline; the separate job needs an
opted-in server and environment credentials. Readiness is exposed as booleans
only at `GET /api/v1/data/alpaca/prerequisites`. The browser blocks launch when
either is absent, and the server independently enforces both gates. See the
[job contract](gui-jobs.md) for bounded acquisition and immutable cache checks.

## Scope and invariants

G5b adds an inline form for one existing single-run strategy and a separate form
for the existing fixed synthetic strategy lab. Both remain fresh-process CLI
jobs. The browser does not run an engine, calculate financial results, choose a
candidate slate, or create Python objects that cross HTTP.

- The CLI and GUI share configuration validation and construction. The worker
  remains the fixed economic execution boundary: `qte run ... --export-version
  2` or `qte lab ...`.
- Keep the existing G5a repository-path request, snapshot format, and research
  job fingerprint unchanged. A durable retry accepted before G5b must not turn
  into an idempotency conflict after G5b.
- Inline configuration has an explicit origin. It is never represented by a
  made-up source filename. Its data path is repository-relative, then rewritten
  only in the effective snapshot to the copied input.
- The server accepts fixed, nested, CLI-shaped sections and converts their web
  values through an allowlist. It does not accept a second sprawling flat form
  dialect or a raw pass-through CLI configuration.
- Preview is read-only and does not execute `BacktestEngine` or `run_experiment`.
  It cannot establish preregistration, prove that data is untouched, or reserve
  the previewed bytes. Preview first captures bounded configuration and data
  bytes, then authorizes both content identities before adapter or financial
  validation can disclose anything about them. Submission performs a new stable
  capture, rechecks holdout protection and disclosure, compiles identity and
  compares the preview fingerprint, then validates and snapshots those bytes without a
  path-based reread before invoking the normal job authority.
- Successful synthetic lab execution never promotes a candidate. Candidate
  failures remain rows in a complete lab; they do not by themselves turn a
  correctly completed lab job into a failed process job.

## HTTP contract

All routes require the existing session, request token, exact Origin for
mutations, no query parameters unless stated, duplicate-key rejection, bounded
bodies, and `Cache-Control: no-store`.

### Form metadata

`GET /api/v1/research/form-options` returns presentation metadata and effective
defaults. Strategy order is built as `("moving_average_regime", *FAMILIES)`;
the six-family list is never copied into a second registry. Candidate field
metadata must be keyed exactly by the existing ordered `FAMILIES`, with a test
that it neither omits nor invents a family. Lab timeframe and regime choices
come from the existing synthetic registries.

All response integers inside metadata/defaults use decimal strings through the
existing exact-value DTO conversion. Control-plane versions remain JSON integers:
the top-level `schema_version`, `config_origin.form_schema_version`, and a lab's
`effective_configuration.schema_version`. Metadata describing a version default
is presentation data, not a submitted configuration.

### Inline origin

Every preview and inline submission contains exactly:

```json
"config_origin": {
  "kind": "inline_form",
  "path_base": "repository_root",
  "form_schema_version": 1
}
```

No other origin value is accepted by these routes. The existing G5a path form
remains its existing request, with `config_path`; it is not silently translated
to an inline form.

### Preview

`POST /api/v1/configs/validate` accepts exactly:

```json
{
  "schema_version": 1,
  "kind": "research_run",
  "config_origin": {
    "kind": "inline_form",
    "path_base": "repository_root",
    "form_schema_version": 1
  },
  "configuration": {}
}
```

`kind` is exactly `research_run` or `strategy_lab`; `configuration` has the
corresponding shape below. The body is bounded to 16 KiB. A valid response contains
`kind`, `config_origin`, `canonical_config_sha256`, `preview_fingerprint`, an
exact-value projection of the effective CLI configuration, and ordered
server-built assumptions. It also records `captured_config_sha256` for the
canonical submitted wire configuration, `data_sha256` (null for a lab), and
authorized `lineage`. The wire hash is the protection identity; the typed
configuration hash is part of the preview fingerprint. They are not interchangeable. An
invalid configuration returns a stable `invalid_configuration` error with
bounded field paths/messages and no traceback, source contents, or raw adapter
payload.

Preview order is normative:

1. Validate only the bounded HTTP envelope, allowlisted origin/path syntax, and
   the minimum `data.adapter`/`data.path` fields needed to locate the input.
2. Retain an immutable canonical serialization of the validated HTTP
   configuration, and capture repository data bytes once by stable descriptor,
   enforcing the existing size, regular-file, containment, hidden-path, suffix,
   and symlink rules. Compute both content hashes.
3. Ask the catalog protection authority about both hashes. Content protected
   without the required prior disclosure, corrupt disclosure history, or an
   unavailable/unresolved authorization decision fails closed before adapter
   parsing, symbol discovery, bar counts, date ranges, sampling checks, or
   financial validation results are returned. Properly disclosed content may
   proceed with its disclosed lineage recorded. A successful authorization with
   no known protected hash retains G5a's `input_disclosure: unknown`; that label
   means disclosure history is unknown, not that authorization failed. It is
   never proof of untouched data.
4. Only after authorization, parse and validate the exact captured bytes and
   build assumptions. No validator reopens the source path.

The preview fingerprint covers kind, canonical configuration, and captured-input
content identity for a single run; a lab has no data input. It is a stale-preview
check, not authentication or evidence that holdout data has remained untouched.
Even a successful preview makes no untouched-data claim.

### Inline job submission

The inline variant of `POST /api/v1/jobs` accepts exactly:

```json
{
  "schema_version": 1,
  "kind": "research_run",
  "config_origin": {
    "kind": "inline_form",
    "path_base": "repository_root",
    "form_schema_version": 1
  },
  "configuration": {},
  "preview_fingerprint": "64 lowercase hexadecimal characters",
  "idempotency_key": "16-128 ASCII letters, digits, hyphen, or underscore",
  "timeout_seconds": 1800
}
```

The same envelope with `kind: strategy_lab` uses the lab shape. Timeout remains
a control-plane JSON integer in `[1, 1800]`. Submission performs a fresh stable
capture, authorizes the captured configuration and data hashes before adapter or
financial validation, compiles the wire configuration to compare the fingerprint,
and only then performs full shared validation and snapshots
that exact captured object. It must not authorize one read and validate or save a
later path reread. Changed previewed bytes return a conflict; newly protected
inputs fail closed even when their bytes have not changed. G5a's existing launch-
time authorization and snapshot-hash checks remain required after acceptance.

Unknown fields and all request-selected command, executable, module, factory,
candidate list, window role, eligibility, output, environment, or CLI-option
fields are rejected.

## Wire numeric rules

`schema_version`, `form_schema_version`, and `timeout_seconds` are the only
integers in these mutation envelopes that are JSON numbers. Every integral
simulation/configuration value is a canonical decimal string:

- zero is `"0"`; nonzero values have no leading zero, leading plus, whitespace,
  exponent, fraction, or decimal point;
- signed values may have one leading minus; `"-0"` is rejected;
- booleans and JSON numbers are rejected where an integer string is required;
- UTC timestamps fit signed int64; intervals are positive signed int64;
  staleness is `[0, INT64_MAX]`;
- random seeds fit uint64; the lab seed remains `[0, 2^62 - 1]`;
- share quantities and enabled maximum order quantity are `[1, INT64_MAX]`;
- positive periods, capacities, holding bars, bars-per-window, and selection
  counts are parsed exactly and then checked by their existing narrower rules.

Optional `max_order_quantity` may be JSON null only where the existing CLI/core
uses null to disable it. Finite economic values such as cash, costs, allocation,
leverage, thresholds, tick size, and annualization values are JSON numbers;
nonfinite constants and overflow are rejected by the existing strict parser.
The browser retains the user's original field text until submission and must not
round a server preview back into a later simulation input.

## Fixed configuration shapes

### Single run

`configuration` has only the existing top-level CLI sections:

- `data` (required): `adapter`, `path`, `interval_ns`; optional `tick_size`,
  `currency`, `action_free`, and `schema`. `adapter` is `csv` or `alpaca`.
  `path` is a bounded repository-relative approved input path. CSV `schema` may
  contain only `symbol`, `timestamp`, `open`, `high`, `low`, `close`, `volume`.
- `strategy` (required): `type`, `symbol`, and the exact parameters allowed for
  that registered type. `moving_average_regime` requires `fast_period`,
  `slow_period`, and `quantity`. Candidate families accept only their exposed
  fields; omitted shared `CandidateConfig` fields receive the existing defaults.
- `engine` (required): `initial_cash`; optional `history_capacity`,
  `random_seed`.
- `execution` (optional): `commission_bps`, `spread_bps`, `slippage_bps`.
- `risk` (optional): `max_order_quantity`, `max_symbol_allocation`,
  `max_gross_leverage`, `allow_short`, `cash_floor`.
- `sampling` (optional): required `max_staleness_ns`, `periods_per_year`, exactly
  one of `interval_ns` or `timestamps_ns`; optional `annual_risk_free_rate`.

The exact wire types are:

| Path | Type |
|---|---|
| `data.adapter`, `data.path`, `data.currency` | JSON string |
| `data.interval_ns` | positive int64 decimal string |
| `data.tick_size` | finite JSON number |
| `data.action_free` | JSON boolean |
| each `data.schema` value | JSON string |
| `strategy.type`, `strategy.symbol` | JSON string |
| SMA `fast_period`, `slow_period`, `quantity` | positive decimal string; quantity fits int64 |
| candidate `fast_period`, `slow_period`, `breakout_period`, `exit_period`, `atr_period`, `contraction_window`, `contraction_memory`, `max_holding_bars` | positive decimal string |
| candidate `allocation_fraction`, `contraction_quantile`, `entry_z`, `min_atr_fraction`, `max_atr_fraction`, `shock_fraction`, `stop_atr_multiple` | finite JSON number |
| `engine.initial_cash` | finite JSON number |
| `engine.history_capacity` | positive decimal string |
| `engine.random_seed` | uint64 decimal string |
| every `execution` value | finite JSON number |
| `risk.max_order_quantity` | positive int64 decimal string or JSON null |
| `risk.max_symbol_allocation`, `risk.max_gross_leverage`, `risk.cash_floor` | finite JSON number or JSON null |
| `risk.allow_short` | JSON boolean |
| `sampling.interval_ns` | positive int64 decimal string |
| each `sampling.timestamps_ns` item | signed int64 decimal string |
| `sampling.max_staleness_ns` | nonnegative int64 decimal string |
| `sampling.periods_per_year`, `sampling.annual_risk_free_rate` | finite JSON number |

Required/optional status is exactly the section contract above and the existing
CLI rules; JSON null is not a substitute for an omitted field except for the
explicitly nullable single-run risk values.

Family-specific presentation uses the existing common entry/ATR-stop/holding
parameters plus: fast/slow for trend and pullback; breakout/exit for breakout;
breakout/exit and contraction fields for contraction; slow/entry-z/max-ATR for
mean reversion; fast/min-ATR/shock for rebound. Cross-field validation and unused
serialized defaults remain those of `CandidateConfig`; the GUI does not redefine
their economics.

The canonical configuration passed to the shared validator uses normal Python
integers and doubles. It preserves current CLI defaults, adapter behavior,
history requirements, sampling policy, manifest contents, and legacy SMA/v1-v2
export behavior. Only the snapshot data path changes to its copied basename.

### Fixed synthetic strategy lab

Lab `configuration` permits only:

- numeric `schema_version: 1`;
- `seed`, `bars_per_window`, and nonempty unique `timeframes` (required);
- optional unique `regimes` from the existing synthetic registry;
- optional `strategy_parameters`, containing only `CandidateConfig` fields
  other than `kind`;
- optional `engine`: `initial_cash`, `max_symbol_allocation`,
  `max_gross_leverage`, `max_order_quantity`;
- optional `execution` and `stress_execution`: `commission_bps`, `spread_bps`,
  `slippage_bps`;
- optional `selection`: `min_closed_trades`, `min_validation_windows`,
  `min_stressed_return`, `worst_stressed_return`, `max_drawdown`.

Its exact wire types are: lab `schema_version` is JSON integer `1`; `seed` is a
nonnegative 62-bit decimal string; `bars_per_window`, integer-valued strategy
parameters, `engine.max_order_quantity`, `selection.min_closed_trades`, and
`selection.min_validation_windows` are decimal strings; `timeframes` and
`regimes` are arrays of JSON strings; `engine.initial_cash`, allocation/leverage,
all cost fields, floating strategy parameters, and the three return/drawdown
selection thresholds are finite JSON numbers. Optional maximum order quantity
is omitted to use its existing default; when present it is a positive int64
decimal string. No lab configuration value accepts null.

The browser cannot supply candidates, benchmarks, factories, macro observations,
dealer observations, windows, roles, eligibility, or evidence labels. The fixed
handler constructs all six registered families in gated and ungated forms, the
macro router, cash, and buy-and-hold: the current 15-entry slate. It constructs
train, validation, and final-test windows, base/stress scenarios, scripted
synthetic macro context, and explicit absent dealer data through `run_lab`.

Lab snapshots are config-only. Their lineage records `synthetic_only`, no input
dataset identities, and ordinarily `input_disclosure: not_required`. The real
configuration hash is still authorized: a known protected configuration requires
prior explicit disclosure and retains `revealed_outside_protocol` lineage.
Success requires exit
zero, verified `strategy_lab_v1`, unchanged source/snapshot identity, every
timeframe report matching the accepted source identity, the exact fixed slate,
all expected non-holdout candidate/window/cost attempts, consistent retained
failure counts, no selected candidate, and `holdout_status:
not_run_no_selection`. This stricter check applies only to newly produced fixed
lab jobs; the catalog continues to read the supported legacy 14-entry lab.

Test-role and mixed-role generated files remain governed by the existing G4
projection/download disclosure rules. Worker verification may check them without
making their values available to the browser. Job metadata and logs expose no
scenario results.

## Shared validators and snapshots

Single-run parsing/preparation was extracted from `qte.cli` into `qte.research_config`.
It owns strict fields, adapter selection, strategy/config construction,
symbol/history checks, execution/risk objects, and sampling construction.
`run_research` and GUI preview/submission call it. Its input is an immutable
captured configuration plus exact data bytes, not a data path, and it does not
execute the engine.

The minimal stable-byte seam is in the existing adapters: add internal byte-
loading helpers for canonical CSV and saved Alpaca fixtures, and make their
current path APIs read once and delegate to those helpers. The CLI captures its
config/data once and calls the same preparation function; GUI capture remains in
the bounded job-input boundary. Provenance and source hashes continue to derive
from the exact bytes. Do not add a virtual filesystem, generic stream interface,
temporary fake source filename, or second adapter implementation.

Before G5b.1, `run_research` constructed sampling after the engine had already run.
The implemented shared preparation boundary deliberately validates and constructs sampling
from the validated dataset's start/end before engine execution. This changes the
failure order for an invalid sampling configuration, but not any valid engine
run, metric, manifest, fill, or export: invalid sampling already produces no
completed output, and valid sampling is still applied only to the completed
owned result. Tests must pin valid output parity and the new early-failure rule.

This preflight covers configuration-only checks: at least two strictly increasing
sample timestamps retaining dataset start/end, nonnegative staleness, finite
positive annualization periods and finite risk-free rate greater than -1.
Actual equity-event ordering/values, realized staleness failures and undefined
return/annualized metrics remain checks on the real result in C++ analytics.
A valid preview is not a promise that a later run will succeed. Do not generate
fake equity or expose additional C++ internals merely to simulate these checks.

The validation/construction prefix of `run_lab` is now a pure shared
`prepare_lab` function used by both CLI and GUI. It constructs `CandidateConfig`, costs, and
`ExperimentConfig`, checks warmup capacity, and creates no output directory or
experiment.

Keep G5a path snapshots unchanged. New inline snapshots use a versioned manifest
and honest inventory:

- `submitted-form.json` containing the exact web form, including integer strings;
- `effective-config.json` containing the server-built CLI configuration;
- `input.csv` or `input.json` for a single run only;
- `input-manifest.json` containing hashes and the explicit origin.

There is no `original-config.json` or `original_config_path` for inline jobs.
Snapshot verification supports both the old G5a path format and the new inline
format. Research-run idempotency continues to use the existing kind, timeout,
canonical-config hash, and data hash formula. The inline origin and preview token
do not alter that formula. The lab uses a separate fixed kind/timeout/canonical-
config fingerprint with an explicit no-data marker.

The private worker specification adds only a server-written `kind`. Dispatch is
an internal fixed table:

- `research_run` -> `.venv/bin/python -m qte run --config <snapshot> --output
  <fresh artifacts> --export-version 2`;
- `strategy_lab` -> `.venv/bin/python -m qte lab --config <snapshot> --output
  <fresh artifacts>`.

No browser field is interpolated into argv.

## Pre-run assumptions

Preview returns ordered, server-generated assumptions and the UI keeps them
visible before launch. A single run states the source path/adapter and declared
provenance limits; action, interval, currency and adjustment assumptions;
strategy and effective defaults; next-open execution and costs; risk limits;
seed; sampling/annualization policy; and that it is one run with no optimization,
selection, or holdout claim.

A lab additionally states synthetic-only mechanics; the fixed complete slate;
base and stress costs; all attempts and failures retained; validation multiple-
testing risk; no synthetic promotion; scripted, generator-related macro inputs;
no dealer evidence; `daily_24h` is not an exchange session; and test-role values
remain withheld by the GUI disclosure protocol. The preview explicitly says it
cannot prove untouched data or prevent direct filesystem inspection.

## Incremental gates

### G5b.1 — shared validators

Extract the two pure preparation boundaries and add CLI parity tests. No HTTP,
job, snapshot, worker, or browser behavior changes. Gate on unchanged legacy SMA
and candidate exports, exact captured-byte adapter parity, the documented early
sampling failure, valid/invalid parity, full existing suites, and Astra review.

### G5b.2 — inline and fixed-job backend

Implement two subgates: **G5b.2a** metadata and authorized preview (no new launch
path), then **G5b.2b** inline snapshots/submission and the fixed lab worker.
G5b.2a captures the bounded HTTP configuration and allowed data bytes once,
checks protection before shared validation, and returns exact-value configuration
and explicit assumptions. Its preview fingerprint uses kind, canonical effective
configuration and input-data identity; it is not an execution authorization.
No files/snapshots or catalog registrations are created by a preview, except
existing protection history may be refreshed by the catalog authority.

Build G5b.2b incrementally. Its first slice adds only inline preparation and
versioned immutable snapshots with tests; no new HTTP launch or worker kind.
Legacy `PreparedInputs`, v1 snapshot inventory/manifest and path-job fingerprint
remain unchanged. New inline preparation retains canonical wire configuration,
the submitted preview envelope, typed configuration hash, captured data identity
(null for a lab), and preview fingerprint. Snapshot verification must recompile
the saved form and recompute the effective configuration/fingerprint, not merely
trust hashes stated in its manifest. Existing persisted expected hashes remain
the outside authority. Research alone rewrites its copied data pathname.
This integrity check recompiles the strict wire shape; it does not rerun an
adapter or prove financial validity. Its caller must first authorize the captured
inputs and obtain the actual shared `prepare_form` result for those same bytes.
Write and verify enforce matching configuration/data size bounds. Partial writes
remain incomplete and non-reusable; there is no automatic overwrite or deletion.

After that small gate, add manager/worker dispatch and the fixed-lab completion
verifier together. The stricter current-slate/complete-attempts check applies to
new lab jobs only, never to legacy catalog readers. It verifies failures remain
rows and no synthetic selection or test evaluation was introduced. Submission
then connects the already-reviewed capture/authorization/preview path to these
boundaries. Browser controls remain G5b.3, after the complete backend gate.

Add exact-value parsers, metadata/preview endpoints, inline snapshots, the
`strategy_lab` job kind, fixed worker dispatch, and fixed-lab completion checks.
Keep the G5a path request and fingerprint byte-for-byte compatible. Gate on
backend/API/real-process tests, full suites, and Astra review before adding forms.

### G5b.3 — forms and job browser

Add `/research/new/run`, `/research/new/lab`, and `/research/jobs/{id}`. The
Research page links to both forms and shows all recent jobs, including failures.
The forms preview assumptions before enabling launch. Job detail supports bounded
polling, sanitized log, valid cancellation, and an explicit registration action
after success; it never auto-registers. Gate on real-control keyboard, narrow
screen, 200% zoom, validation-error, double-submit, cancellation, restart, failed
candidate, and protected-holdout checks. Browser automation is claimed only if
its tooling is separately approved and actually run.

## Tests, ownership, and edit seams

G5b.1 owns only shared configuration modules, the minimal adapter byte-loading
seam, `cli.py`, `strategy_lab.py`, and their direct tests. Test every registered
strategy; path-versus-byte adapter parity; one-capture behavior; invalid fields
and cross-field constraints; adapter/symbol/history/sampling parity; early
sampling rejection without engine execution; legacy SMA; effective defaults;
and output metrics/fills/orders/equity/export parity.

G5b.2 owns GUI request/config handlers, job inputs, manager, worker, API wiring,
and backend tests. Cover int64/uint64/62-bit boundaries, JSON-number rejection,
origin honesty, authorization before all content-derived validation, exact-byte
validation/snapshot identity, no-data lab snapshots, tampering, stale preview,
newly protected input, old path retry compatibility, auth/origin/unknown fields, queue/cancel/
timeout/owner loss, credential stripping, fixed argv, exact 15-entry slate,
retained injected failures, fake selection, missing candidate, mismatched source,
and existing G4 mixed-role privacy after explicit registration.

G5b.3 owns a separate static form module, the minimal `app.js` routing hooks,
CSS, static tests, and browser/manual evidence. It does not edit validators,
runner internals, or financial projection code.

Root owns the small shared route/application integration points and resolves any
overlap before merging work. Astra independently reviews financial/causal parity,
exact-integer transport, fixed-command safety, idempotency compatibility, failure
retention, and holdout non-disclosure at each gate. After each substep, run the
targeted tests, full Python suite, native build and CTest, and report any exact
blocker; no test is weakened to advance the milestone.
