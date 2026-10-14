# Declarative comparisons — G7a

G7a.1 adds a shared configuration/preparation layer and the offline `qte compare`
command. It calls existing research APIs; it does not change engine behavior.
GUI launch, multi-source snapshots and role-filtered comparison views remain
the next increment, not capabilities of this first slice.

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

Until the next increment adds a protocol-aware reader, GUI registration treats
this new format as unsupported and protects all its files. It must not be
interpreted as a synthetic lab merely because it has a configuration file.
