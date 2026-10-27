# ADR 0011: Explicit offline session schedules

Status: R2a implemented and offline-tested; the separate R2b replay bridge is
defined in [ADR 0012](0012-explicit-availability.md).

## Decision and boundary

Add `qte.session_calendar` as a pure Python boundary utility. Preserve canonical
Bar v1, the C++ fixed-duration dataset contract, and availability at bar end.
Neither existing adapter nor GUI/CLI research configuration changes in R2a.

Use an immutable, finite **caller-declared** regular-session schedule. Each
calendar date in inclusive coverage must appear exactly once, in order, with
either UTC opening/closing nanoseconds or an explicit closed-day record. Missing
dates are errors, never inferred holidays. Requests outside coverage fail.
Dates are canonical `YYYY-MM-DD` New York trading-date labels; intervals must
remain within that UTC date, suitable for the initial daytime US ETF profile.
One contiguous session is supported; no extended hours, overnight sessions,
intraday breaks or instrument-specific halts are modeled.

The snapshot contains resolved UTC instants, not formulas for future sessions.
It does not consult system timezone data or generate exchange holiday rules.
DST changes, exceptional closures and early closes must be explicit in the
supplied schedule. Tests use invented declarations, including UTC shifts around
DST boundaries; they do not independently validate an official exchange calendar,
09:30/16:00 hours, or the date label against New York timezone rules.

Tradeoff: this is reproducible, dependency-free and cannot silently extrapolate
an incorrect calendar. It requires a separate, verified schedule acquisition/
normalization step before historical use. Source authority, market/venue coverage,
timezone conversion and revised schedules remain that step's responsibilities.

## Values and invariants

- `SessionDay(trading_date, open_ns=None, close_ns=None)` is explicitly closed
  only when both instants are absent. Otherwise require `open < close`.
- `SessionSchedule(coverage_start, coverage_end, source_id, source_sha256,
  evidence, days)` owns immutable day values, accepts at most 36,600 calendar
  dates, and rejects omissions, duplicates, unsorted/outside dates and overlaps.
  Evidence is `synthetic` or `historical_declared`, never automatically verified.
- `source_sha256` is the caller's declared source digest. `snapshot_sha256`
  separately hashes all normalized schedule/provenance fields and policy
  `session_windows_v1`. Neither hash proves authenticity, point-in-time knowledge
  or that a source file was actually read. A revision changes snapshot identity;
  nothing silently replaces a schedule already in use.
  Window kind and publication delay are separate projection inputs; record them
  alongside any future window export, not merely the schedule hash.
- All timestamps are exact signed 64-bit nanoseconds; booleans, floats and
  overflow are rejected. Dates and source metadata are bounded and validated.
- `day(date)` distinguishes an explicit closure from unknown coverage.
  `buckets(date, kind="hourly", publication_delay_ns=0)` returns immutable
  `SessionWindow(trading_date, start_ns, end_ns, available_at_ns)` values.

| Window kind | Interval | Availability |
|---|---|---|
| `daily` | Exactly `[session open, session close)` | Close plus explicit delay |
| `hourly` | One-hour intervals anchored at session open; truncate final tail | Each interval end plus explicit delay |
| Closed date | No windows | No invented market observation |

Intervals are half-open: a boundary belongs to the next bucket, not both. A
declared 6.5-hour session yields six full hourly windows plus a 30-minute tail;
a 3.5-hour session yields three plus a 30-minute tail. No discarded/padded tail,
overnight bucket, OHLC aggregation or fabricated price/volume. Provider hourly
aggregation may use another anchor and must not be relabeled without validation.

Publication delay is an exact nonnegative integer, validated even on closed
dates. Zero means an explicitly idealized at-close observation, not verified
publication latency. Availability may cross a session/date boundary but cannot
overflow. Session windows describe timing, not strategy-visible market events.

`session_close_grid()` returns declared open-session closes in order, omitting
closed dates. It does not infer 252 periods/year, add a run's initial equity
anchor, apply publication delays, or bypass existing sampling/staleness checks.
Callers must retain exact run boundaries and declare the annualization convention.

## Small offline example

This is a synthetic declaration, not an exchange-sourced schedule. Run with the
repository `.venv`:

```python
import hashlib
import json
from qte.adapters.common import parse_rfc3339_utc_ns as ns
from qte.session_calendar import SessionDay, SessionSchedule

raw = b'{"date":"2024-11-29","open":"2024-11-29T14:30:00Z","close":"2024-11-29T18:00:00Z"}'
record = json.loads(raw)
day = SessionDay(record["date"], ns(record["open"]), ns(record["close"]))
schedule = SessionSchedule(
    day.trading_date, day.trading_date, "synthetic-example-v1",
    hashlib.sha256(raw).hexdigest(), "synthetic", [day],
)
windows = schedule.buckets(day.trading_date, publication_delay_ns=1_000_000_000)
assert len(windows) == 4  # Three hours and a 30-minute tail; one-second delay.
assert windows[-1].available_at_ns == day.close_ns + 1_000_000_000
```

## Acceptance and next gate

Test regular/short/exact-hour sessions; explicit holiday/weekend/exceptional
closures and spring/fall UTC shifts; complete ordered coverage; immutable owned
inputs; stable/revision-sensitive identity; nanosecond precision and overflow;
daily/hourly endpoints and delayed availability; unchanged adapter/core rejection.

R2a alone does **not** enable mixed-duration daily/short-tail bars or delayed
publication in replay. R2b now supplies a separate opt-in dataset/event envelope
and offline adapter; see ADR 0012 for ordering and compatibility limits.
Corporate-action accounting is a separate gate
before action-bearing ETF history. No network, broker, calendar package, exchange
verification, live-feed or economic-performance claim belongs to this milestone.

See [time and visibility](0002-time-and-events.md),
[sampling](0008-equity-sampling.md), and [roadmap](../roadmap.md).
