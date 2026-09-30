# Additive research export v2

Status: G3c implemented, regression-tested and independently reviewed.
G3b supplies the
[read-only owned values](gui-result-bindings.md). No engine/accounting change is
part of this format.

## Compatibility and completion

`qte run --export-version 2` and `run_research(..., export_version=2)` opt in.
The default remains version 1. Both write the existing manifest, report, orders,
fills, equity and optional sampled-equity payloads with unchanged v1 headers.
Version 2 adds the files below; it never edits a previous export. The existing
`complete.json` schema-1 checksum map covers every payload, including the new
wrapper, and is written last. Its inventory differs by design; there is no
second checksum authority or self-referential wrapper hash.

Compare v1/v2 payload compatibility using the same config, data and current
source identity, excluding completion markers and additive files. A newer source
or binary legitimately changes recorded source identity; that must not be
normalized away when evaluating provenance.

## Wrapper

`research_export.json` is an object with these exact fields:

- `schema_version`: integer `2` (not a boolean).
- `artifact_kind`: `research_export`.
- `lineage`: `legacy_format: single_run_v1`, `manifest: manifest.json`,
  `report: report.json`, and `source_identity` / `dataset_hash` equal to the
  legacy manifest's recorded values.
- `capabilities`: `event_sequence`, `order_snapshots`, `fill_sequence`,
  `positions`, `closed_trades`, `open_trades`, `order_events` are `recorded`;
  `sampled_event_sequence` is `recorded` only when sampling was configured,
  otherwise `not_recorded`.
- `files`: fixed semantic names mapped to the corresponding filenames below.
  The optional sampled file is present iff its capability is recorded. No
  configurable paths or arbitrary columns.

## Owned tables

CSV headers below are ordered contracts. An empty table retains its header.
`None` is an empty cell; booleans are `true` / `false`; enums use the uppercase
binding names. Every timestamp, quantity, ID and sequence is canonical decimal
text without exponent notation. Prices and financial totals are finite serialized
binary64 values, retaining Python's round-trip representation. Optional fields
remain absent; no cash, cost basis or missing accounting fact is inferred.

| Semantic name / filename | Columns |
|---|---|
| `equity_events` / `equity_events.csv` | timestamp_ns, sequence, equity, gross_exposure |
| `sampled_equity_events` / `sampled_equity_events.csv` (optional) | timestamp_ns, sequence, equity, gross_exposure |
| `fill_events` / `fill_events.csv` | fill_id, order_id, symbol, side, quantity, timestamp_ns, sequence, reference_open, price, gross_notional, commission |
| `order_snapshots` / `order_snapshots.csv` | order_id, symbol, side, quantity, order_type, limit_price, stop_price, time_in_force, submitted_ns, submission_sequence, eligible_after_sequence, status, filled_quantity, remaining_quantity, stop_triggered, rejection_reason, cancellation_reason, detail |
| `order_events` / `order_events.csv` | timestamp_ns, sequence, order_id, kind, detail |
| `positions` / `positions.csv` | symbol, quantity, mark_price, mark_ns, mark_sequence |
| `closed_trades` / `closed_trades.csv` | symbol, direction, opening_fill_id, opened_ns, opening_sequence, closing_fill_id, closed_ns, closing_sequence, opened_quantity, closed_quantity, remaining_quantity, realized_gross_pnl, allocated_commissions, net_realized_pnl, is_closed, outcome |
| `open_trades` / `open_trades.csv` | same columns as closed_trades |

Event equity timestamps may repeat; source order and engine sequences distinguish
events. Sample timestamps are strictly increasing, but their copied source-event
sequence may repeat under the configured carry policy. The sample timestamp is
not the source event's timestamp. Fills and order events have their own recorded
sequences; consumers must not infer a contiguous combined event journal from
these incomplete subsets.

## Verification and presentation

The GUI recognizes the tagged wrapper before the legacy single-run reader.
Verification requires the wrapper/version, fixed file map and capabilities,
complete checksum inventory, mandatory tables, exact columns, numeric/enum/
optional-field domains, chronological rows and consistent IDs/counts/projections
against v1 records. Referenced fills/orders must agree where present; unknown
cancel-order IDs are allowed only for the corresponding lifecycle event kind.
It checks recorded structure, not recalculated PnL or strategy economics.

All interpreted content and raw downloads pass through catalog protection.
Copied protected bytes do not become public merely by acquiring a v2 filename.
For verified v2 exports, each legacy/owned equity, sampled-equity, fill and order
pair inherits protection in both directions. The classification is persisted, so
later copies and raw downloads cannot bypass the richer table's restrictions.
The browser receives 64-bit fields as decimal strings and uses recorded metrics.
The v2 views prefer owned tables; legacy v1 views keep missing richer fields
explicitly `not_recorded`. Final positions contain only inventory and valuation
mark facts, not full position PnL. Each table has at most four default columns
and a focused complete-record view.

Acceptance includes actual-engine hand-calculated fixtures, v1/v2 payload parity,
exact integer transport, empty/open/closed records, corruption/schema/version/
reference mismatches, copied-content protection and independently reviewed
C++/Python/browser regressions. A valid checksum is not provider authentication,
proof of an untouched holdout, or evidence of profitability.
