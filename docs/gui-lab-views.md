# Role-filtered lab views (G4)

Status: G4 implementation active after the G3c review gate passed.
This is a read-only projection of `strategy_lab_v1`, not a second experiment
runner or selection algorithm. Existing 14- and 15-entry slates remain valid.

## Safety boundary

The service may internally read fixed, verified mixed-role report/slate/macro
files to produce allowlisted train/validation DTOs. That narrowly scoped operation
never returns raw bytes, accepts arbitrary paths/callbacks, changes file protection
or records a reveal. Generic downloads continue through `Catalog.read_file`.
The entire artifact generation is checked again before returning a projection.
All routes require the existing session and request token.

Structured test-role views remain unavailable even after explicit raw disclosure
in G4. The existing confirmation/journal/raw-download workflow remains separate.
Unmapped/cross-window macro rows are omitted with a generic warning, not their
values or hidden-row counts. Queries for a non-public window get one fixed
protected/unavailable response. No report, summary, generator or `config_json`
object is returned wholesale. Test errors, metrics, pass/fail and reasons never
enter safe DTOs. Catalog scenario outcome must not reveal mixed-summary failures;
use `unknown` until a separately verified safe-role outcome exists. Inventory
names/counts remain metadata, not a preview of protected file contents.

Before projection, require unique window/candidate identities, ordered,
nonoverlapping train -> validation -> test windows, compatible universe/currency/
interval, report/slate candidate agreement, and unambiguous scenario joins.
Scenario role/regime must match its window; cost is base/stress; duplicate
candidate/window/cost keys are invalid. Missing trials remain explicit, not
silently dropped. Selection and assessment fields are recorded facts, never
reranked or recalculated. Synthetic evidence cannot select/promote a candidate.

## API contract

`GET /api/v1/experiments/{id}?timeframe=hourly` returns `schema_version: 1` and
`experiment` with:

- `id`, `name`, `kind`, safe catalog `status`, `timeframe`, `timeframes`;
- recorded `protocol_id`, `source_identity`;
- `selection`: `selected_candidate` (nullable),
  `assessment_source: recorded_validation`;
- `holdout`: coarse `evaluation`, `structured_access: withheld`;
- `context`: currency, interval_ns, initial_cash, base_costs, stressed_costs;
- `windows`: only safe `{name, role, regime}` choices;
- `warnings`: synthetic/scripted macro/dealer limitations, independent windows,
  validation screening rather than significance, and withheld test contents.

Omitting timeframe selects the first recorded timeframe. Unknown/duplicate
queries fail explicitly. Only recorded hourly/daily_24h clocks are supported;
daily_24h is never labeled a US exchange session. No automatic artifact import.

`GET /api/v1/experiments/{id}/tables/{table}` accepts timeframe, offset and limit
(same 25-default/200-max bounds as run tables). For comparison, macro and
regime_decisions, an explicit safe `window` is required. It returns a flat
`schema_version`, `artifact_id`, `timeframe`, `table`, `columns`, `rows`, `total`,
`offset`, `limit`, `warnings` envelope. Pagination counts only permitted rows.
Artifact integers are decimal strings; bounded pagination fields are numbers.

| Table | Default columns | Complete record |
|---|---|---|
| candidates | candidate, eligible, validation_score, exclusions | Also recorded validation_mean_stressed_return and benchmark flag |
| windows | name, role, regime, interval_ns | Recorded safe window time/universe/currency/evidence/provenance facts |
| comparison | candidate, base_return, stress_return, result | Allowlisted complete base/stress scenario records, including errors, missing trials, zero trades and benchmarks |
| macro | feature, value, reference_ns, available_ns | Also recorded source and vintage; only observations whose reference and availability both belong to the selected safe window |
| regime_decisions | timestamp_ns, macro_regime, volatility_regime, permitted_families | Recorded allowed-family decisions, not fabricated router trades |

Candidate/comparison order is slate order, not best return. Comparison uses one
window and one compatible cost/currency context; no stitched equity or aggregate
portfolio return is invented. Macro requires reference <= availability and both
timestamps in the same unambiguous window; decision close times use
`(window.start_ns, window.end_ns]`. Empty rows retain the table schema.

## UI and acceptance

A verified lab opens the candidate overview with timeframe selection, one concise
evidence/selection/holdout line and at most four columns. Windows, comparison,
macro inputs and regime decisions are separate views. Selected-window controls
are required where applicable; full row details replace the table. Catalog and
disclosure controls remain reachable. No chart is synthesized from independent
scenario windows, and no job/network action starts on navigation.

Tests must use actual small lab exports plus isolated adversarial fixtures:
14/15 slates, failed/no-trade/benchmark rows, selected historical-like test rows,
role/window/cost mismatch, malformed/overlapping windows, macro availability
boundaries, test sentinels absent from every DTO/page, raw protection and durable
disclosure unchanged, generation swaps, auth/query rejection, and exact integers.
Measure cold verification and warm paging against the saved 1,080-scenario
artifact without modifying it or revealing its held-out data. Browser checks
exercise actual controls; native/Python regressions and Astra review gate G5.
