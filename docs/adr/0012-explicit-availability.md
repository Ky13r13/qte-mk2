# ADR 0012: Explicit availability and session replay

Status: R2b implemented; offline synthetic verification only. Python API access,
not a new GUI/CLI ingestion format or verified historical calendar.

## Decision

Keep canonical Bar v1 unchanged: UTC signed-int64 nanoseconds, half-open
observation interval `[start, end)`, owned OHLCV. Keep `Dataset.from_bars` and
fixed-duration preflight/hash bytes unchanged (`fixed_interval_v1`, `fnv1a64-v1`).

Opt in through `BarObservation(bar, available_at_ns)` and
`Dataset.from_observations(observations, source_id, timing_source_id, ...)`.
The `explicit_availability_v1` profile permits variable durations but retains
all price, instrument, currency, raw/action-free, chronological, nonoverlap and
gap checks. Require `available_at >= bar.end`; per-symbol releases must be
strictly increasing. Reject simultaneous/reordered releases, revisions and
duplicate bars rather than sorting or repairing them. Cross-symbol ties retain
byte-ordered batching. C++ preflight owns and validates bars and release vectors
together; Python envelopes and returned snapshots are immutable owned values.

Explicit datasets have no invented fixed interval (`interval_ns is None`;
C++ `bar_interval()` throws). Their `fnv1a64-timed-v1` hash includes timing policy,
source identity and every availability instant as well as existing data fields.
It is a reproducibility identifier, not an authenticity/security guarantee.

## Event and valuation semantics

| Time/fact | Meaning |
|---|---|
| Bar start/end | Physical observation interval; never rewritten to delivery time |
| Availability | Publish completed history and dispatch `on_bar` at this instant |
| `ctx.now_ns` / C++ `ctx.now()` | Current callback time, including lifecycle/fill/order callbacks |
| Opening | Independently observable at bar start (`at_bar_start_v1`) |
| `Dataset.end_ns` / manifest `replay_end_ns` | Last publication event, including delayed final bars |
| `observation_end_ns` | Last physical bar end, possibly earlier than replay end |

At one time: publish available bars/history → callbacks → commands → openings
and fills → fill callbacks → commands → equity. Zero-delay behavior is unchanged.
A release at the next open may trade that open under the existing idealized
zero-latency convention. A release after it cannot trade retroactively. Final
orders with no remaining opening are canceled by existing end-of-data handling.
Opening availability is an explicit simulation assumption, not proof of a
provider's live opening latency. No high/low/close/volume leaks through openings.

The engine tracks latest economic observation time per symbol. A delivered close
updates valuation only if its bar end is newer than that observation; an already
known opening wins an equal-economic-time tie. Stale bars still enter history and
invoke callbacks. All portfolio events apply at the current replay time, never a
past time: another symbol may already have advanced the global ledger. Existing
`position.mark_ns` reports **application time**, not the physical observation
age of a delayed close. No market-data-age risk limit is inferred from it.
Prior equity points are never rewritten. The ledger retains its single authority.

Built-in candidate macro gates and router decisions use `ctx.now_ns` for release
availability and staleness; router logs use the same time. Technical indicators
still consume delivered bars/history. Custom time-dependent strategies should
make this distinction explicitly. Context lifetime/thread guards also cover the
clock; saving a context does not grant access after its callback.

Run manifests record timing/source, observation/replay ends, opening availability
and `strict_per_symbol_v1` release ordering. Legacy core results/hashes remain
unchanged apart from additive manifest metadata and the new callback clock.

## Session boundary

`qte.adapters.build_session_dataset` binds already-canonical bars to exact
[caller-declared session windows](0011-session-calendar.md), hourly or daily.
It requires source ID/digest and explicit `action_free=True`, with USD, whole
shares and raw prices. Calendar snapshot digest, kind and delay enter timing
identity; provenance retains declared evidence and assumptions. Missing buckets
remain gaps. Closed/outside/misaligned buckets fail; nothing aggregates,
fabricates or relabels provider bars. Declared hashes do not verify source bytes,
official sessions, corporate actions or point-in-time knowledge.
`gap_count` still counts raw interval gaps (including overnight closures), not
missing exchange observations. A complete calendar does not imply complete bars.

Tradeoff: a small opt-in bridge supports short sessions and delayed research
without changing the legacy ingestion format. Existing CSV/Alpaca loaders and
GUI/CLI schemas remain fixed-duration. Current comparison/experiment APIs reject
the explicit profile until frequency, sampling and export contracts are extended.
Direct `BacktestEngine.run` and explicit analytics grids work; grids must retain
run start/end, with caller-specified staleness and annualization assumptions.
A physical close grid alone is not a delayed publication grid.
Analytics requires nondecreasing events and a total elapsed span no greater than
`INT64_MAX` nanoseconds; individual signed-int64 timestamps alone do not guarantee
that their difference is representable. Oversized spans fail before subtraction,
including with sampling/annualization. Replay timestamps are not silently clipped.

Acceptance includes legacy golden hash; release before/at/after next opening;
same/cross-symbol stale marks and ties; final delayed cancellation; pre-epoch
timestamps; strict ordered releases; owned bindings/expired contexts; macro
release/staleness at callback time; session tails and missing/closed days.
Online data acquisition, action accounting, exchange verification and paper/live
feeds remain separate gates. No profitability claim follows from these fixtures.
