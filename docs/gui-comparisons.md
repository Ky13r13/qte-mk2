# Declarative comparisons — G7a

G7a.1 adds a shared configuration/preparation layer and the offline `qte compare`
command. G7a.2a adds strict artifact verification and guarded registration in the
existing GUI catalog. G7a.2b adds read-only candidate, window and comparison
views. G7a.3a adds private validation-ID capture and immutable multi-source
snapshots. G7a.3b adds authenticated preview and fixed comparison job execution.
G7a.3c adds reviewed browser forms and local synthetic end-to-end acceptance.
None changes engine behavior.

## Launch from the GUI

Choose **Research → New comparison**, then SMA train/test or Frozen candidate
experiment. Add chronological windows using registered dataset-validation IDs;
**Choose validated dataset** lists only names/IDs, never input contents. Declare
each window's evidence kind, source and corporate-action policy explicitly.
Validation and checksums do not authenticate those declarations.

Configure the frozen candidate slate from registered strategies. Advanced
parameters remain editable; base and stressed costs are separate. Exact integer
fields, including quantity and random seed, travel as decimal strings. Experiment
windows/candidates are capped at 24/32; SMA requires train/test and one candidate.

**Preview assumptions** validates without running or reserving data. Any edit
invalidates the preview. **Launch fixed comparison** creates one bounded job;
after success, **Register output** explicitly opens its safe views. An uncertain
submission freezes the draft: **Retry same request** preserves its body and
idempotency key. Review recent jobs before deliberately starting a new request.
Final-test identities are protected at acceptance; no form action reveals them.

Metadata comes from authenticated `GET /api/v1/comparisons/form-options`, derived
from the existing registry/configuration defaults. The browser does not load
arbitrary strategy modules, calculate financial metrics or inspect raw datasets.

## Try the mechanics fixtures

From the repository root, choose fresh output directories:

```sh
.venv/bin/python -m qte compare --config examples/comparisons/sma.json --output build/comparison-sma
.venv/bin/python -m qte compare --config examples/comparisons/experiment.json --output build/comparison-experiment
```

Both examples use fabricated prices, explicitly labeled synthetic. The
experiment must select nobody and must not run its final test window. These
fixtures demonstrate the workflow, not a trading edge or historical research.

## Two existing workflows, two distinct interpretations

| Workflow | Existing authority | Interpretation |
|---|---|---|
| `moving_average_comparison` | `research.compare_moving_average` | Exactly four scenarios: train/test × zero-cost/stressed; one fixed SMA, no optimization or candidate selection |
| `historical_experiment` | `experiments.run_experiment` | Frozen candidate slate; all train/validation attempts retained; validation-only ranking; only the selected candidate sees the final test; no runner-up after failure |

Each window starts from cash with fresh indicator warmup. Open positions remain
marked at the end, not forcibly liquidated. Neither workflow stitches returns
across windows, annualizes them, performs rolling walk-forward tests or Monte
Carlo analysis, or establishes statistical significance.

## Configuration contract

Top-level fields are exactly `schema_version: 1`, `workflow`, `windows`,
`candidates` and `experiment`. Unknown fields, nonfinite numbers, ambiguous
types and arbitrary factory/import names are rejected. CLI integers are JSON
integers, not the decimal-string wire format used by existing GUI forms.

Each window declares `name`, `role`, `regime`, `data` and `evidence`:

- `data` reuses the canonical CSV/saved-Alpaca adapter options. Paths are relative
  to the configuration file; fixed UTC intervals and availability at `bar_end_ns`
  are unchanged. No exchange-calendar validation or gap filling is implied.
- `evidence` explicitly gives `kind` (`historical` or `synthetic`), `source`,
  `source_sha256`, `availability_policy: "bar_end_ns"` and
  `corporate_action_policy`. The hash must match captured bytes. Evidence kind,
  source authenticity and corporate-action claims are caller declarations,
  **not independently verified by checksums or adapter validation**.
- Historical experiments accept 3–24 ordered windows with train/validation and
  exactly one final test. SMA comparisons accept exactly train then test.
  Chronology, interval, currency and strategy-symbol checks precede execution;
  experiments additionally require an identical universe across windows.

Candidates contain `name`, `type`, `symbol`, `parameters` and optional `eligible`.
Supported types are `moving_average_regime`, `trend`, `breakout`, `contraction`,
`pullback`, `mean_reversion` and `rebound`. The registry creates fresh instances;
no browser-selected Python code is imported. Experiment slates contain 1–32
candidates. SMA comparisons require one eligible SMA candidate. History is
derived from validated lookbacks, with a 100,000-bar bound.

`experiment` preserves existing API defaults. Historical experiments use the
`ExperimentConfig` fields, including base/stress costs and screening gates;
`{}` does not relax those gates to fit a tiny fixture. SMA accepts only
`initial_cash` and `stressed_costs`; its zero-cost baseline is explicit.

## Private GUI input foundation — G7a.3a

The internal form replaces each window's `data` with a verified
`dataset_validation_v1` catalog ID. Candidate/experiment integers use canonical
decimal strings. Evidence kind, source and corporate-action policy remain caller
declarations; hashes and availability policy are injected from validated input.
CLI validation copies and successful v2 GUI validation jobs are supported. The
latter must still match their recorded snapshot, options and allowed original
source; reserved job paths are not accepted as arbitrary data sources.

Captured bytes, source order and parameters are frozen before preparation.
Duplicate IDs/content/sources, changed validation inventories, invalid chronology,
currency/universe mismatches and oversized inputs fail with generic errors.
Preparation validates contracts but runs no strategy. Existing 1 MiB config,
16 MiB/source and 64 MiB total limits apply.

Train/validation reject known protected or revealed data-derived identities.
Final-test permits protected, unrevealed data but rejects any known reveal or
damaged history. Explicit private reservation protects final-test identities;
it never reveals them. Missing history means **unknown**, not untouched. Shared
adapter configuration does not permanently contaminate unrelated raw data;
generic catalog downloads still require disclosure while that config is guarded.
Existing journal records are never erased.

Version 3 snapshots use `submitted-comparison.json`, canonical/effective configs,
numbered input files and a final manifest. Verification checks an exact inventory,
independently pinned hashes and reconstructed semantics. V1/v2 stay unchanged;
partial snapshots are retained but cannot be verified or reused. Snapshot writing
does not accept/start a job or reserve roles. The G7a.3b integration reauthorizes
at acceptance/dispatch/finalization, verifies output correspondence, checks code
identity and keeps private captured records/hashes out of public DTOs and logs.

## Backend comparison jobs — G7a.3b

Authenticated `POST /api/v1/comparisons/preview` accepts exactly schema version 1,
`kind: comparison_run` and the private `configuration` form above. It returns
only an opaque preview fingerprint, fixed assumptions and unknown holdout history;
it does not reserve data, execute strategies or disclose final-test facts.

`POST /api/v1/comparisons/jobs` accepts the same fields plus `preview_fingerprint`,
`idempotency_key` and `timeout_seconds` (1–1800), without `config_origin` or
arbitrary paths/commands. Its separate envelope is capped at 1 MiB + 4 KiB;
the inner config retains the 1 MiB limit. Normal session/Origin/token rules apply.
The separate route preserves ordinary `/api/v1/jobs` 4/16 KiB submission limits;
comparison status, list, cancel and logs use the existing `/api/v1/jobs` routes.
The fixed worker runs `qte compare` from the immutable v3 snapshot. Both supported
workflows match direct CLI exports; registration remains an explicit action.

Acceptance reserves final-test identities without revealing them. Dispatch and
finalization reconstruct authorization records from the verified snapshot and
reject changed code, protected safe inputs, revealed final-test inputs or damaged
history. A valid but unrelated output is rejected by exact configuration, ordered
input and source correspondence. Only intrinsic test/mixed output is protected;
safe comparison views remain usable. Failed/unfinalized jobs cannot be registered.
All files are retained on failure; no automatic retry or overwrite occurs.

Comparison job responses withhold input hashes, role identity lists and child
exit codes; logs and failures use fixed generic text. Idempotency binds the
captured ordered inputs, configuration, code identity and timeout. It therefore
rejects reuse of a key for a different code identity (unlike legacy v1/v2 jobs).
Neither successful execution nor an absent disclosure record proves an untouched
holdout, authentic historical data, or a trading edge.

## Outputs and integrity

Inputs are captured as bounded regular files before execution: 1 MiB config,
16 MiB per source, 64 MiB aggregate data. Outputs are exclusive: existing
directories are never overwritten. Reports retain failures and selection
exclusions. Successful experiment scenarios include raw equity/fill/order files;
the legacy SMA API exposes summaries only, so no raw fills are invented.

Exports retain submitted/effective configurations, copied input bytes, source
and input identities, `report.json`, `comparison.csv`, a
`comparison_artifact.json` wrapper and a final recursive checksum inventory in
`complete.json`. A checksum is an integrity check, not an authenticity claim.
An interrupted export has no completion marker. Direct CLI output can contain
test data/results; inspecting it is a disclosure outside a guarded GUI protocol.

## Register an export in the GUI

In Research, explicitly register the output directory, for example
`build/comparison-experiment`. The catalog recognizes `comparison_export_v1`,
verifies the captured data/configuration, scenario mappings, event records and
checksums, and opens the safe comparison views. It does not rerun strategies.

Candidates show recorded validation scores and safe screening exclusions; open
a row for parameters and remaining fields. Windows show only train/validation.
Comparison requires an explicit safe window and shows base/stressed returns,
including failed attempts. Pages contain at most 25 rows and four columns;
record details retain exact integer values. Legacy SMA fields not recorded by
its report remain unavailable rather than inferred.

Final-test rows, outcomes and provenance remain withheld in these views even
after a raw-file disclosure. Global promotion eligibility is also withheld:
it can depend on final-test provenance. Evidence is shown per safe window, not
as an aggregate label. These are independent-window screening results, not a
continuous portfolio, walk-forward result or profitability claim.

Train/validation inputs and event files are available unless they inherit prior
protection. Raw test inputs/events and mixed metadata (including reports,
configuration, comparison CSV and the completion checksum inventory) require **reveal outside protocol** before
download; that action is durably recorded. Previously protected input bytes
also protect their derived outputs. Earlier unsupported imports keep their
journaled protection; verification does not erase disclosure history.

Incomplete, malformed or inconsistent exports remain entirely guarded. The
catalog does not expose selection, holdout outcome or failure reasons as generic
status; those fields remain unknown. Old valid source
identities remain readable, with code mismatch reported separately. Original
unsupported-reader advisory text in old exports is retained, not rewritten.

The verifier supports the declared maximum slate. Configuration/marker inputs
are capped at 1 MiB; reports and other artifact files at 16 MiB each, with a
256 MiB aggregate catalog limit. Verification establishes consistency, not
provider authenticity, untouched holdout history or profitability.
