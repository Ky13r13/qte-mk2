# Research workflow and acquisition

For the multi-family, macro-aware synthetic lab, see
[strategy lab](strategies/strategy-lab.md). For safe external API boundaries, see
[broker connections](broker-connections.md). Research results do not authorize
account access or order placement.

Use the repository's `.venv`; the C++ extension must be rebuilt after C++ edits:

```sh
cd /home/glsu6/Documents/qte
PIP_NO_CACHE_DIR=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
.venv/bin/python -m qte run --config examples/research-run.json --output build/my-research-run
```

The example uses seven synthetic hourly bars and produces a completed round
trip. Its annualization value (8766) is a synthetic continuous-hour convention,
not a US exchange calendar. Real experiments must choose their own sampling
grid/convention. Configuration is JSON and unknown keys fail. Data paths are
relative to the configuration file. The output directory must not already exist.

Artifacts: `report.json`, `manifest.json`, `fills.csv`, `orders.csv`, `equity.csv`,
and optional `sampled_equity.csv`. `complete.json` is written last and includes
artifact SHA-256 checksums; its absence means export did not finish. The manifest
records strategy parameters, normalized engine config, data provenance, sample
policy, and a SHA-256 fingerprint of the loaded native extension and Python
sources. A single run makes no train/test or optimization claim.

`compare_moving_average` requires train end <= test start, matching intervals and currencies,
and the strategy symbol in both datasets. Adjacent boundaries are valid because
bar intervals are half-open. Each split starts fresh, including indicator warmup;
pre-test warmup transfer and walk-forward training are separate future features.
Pre-registration remains a caller assertion; code cannot prove when a human
chose parameters. Reports retain all parameters and actual loaded-code identity.

## Alpaca historical downloads

The downloader follows Alpaca's documented GET endpoint, inclusive start/end,
and pagination token contract:
<https://docs.alpaca.markets/us/reference/stockbars>.

Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` locally in your shell. Credentials
are read only when a network request is needed, excluded from cache/manifest,
and never included in error output. Redirects are disabled. Example:

```sh
.venv/bin/python -m qte download-alpaca \
  --symbols SPY --timeframe 1Hour --feed iex \
  --start 2024-01-02T14:00:00Z --end 2024-01-02T21:00:00Z \
  --action-free --cache-dir data/truth/captures/alpaca
```

`--action-free` is an explicit declaration the user must verify. The tool does
not inspect corporate actions. The request pins raw adjustment, USD currency,
ascending sort, and `asof=-` (symbol mapping disabled). Only supported fixed
minute/hour intervals and explicitly selected IEX/SIP feeds are accepted.

Complete validated downloads are published atomically to a directory keyed by
request SHA-256. It holds normalized saved JSON pages, the merged fixture, and
request/checksum metadata. Cached requests are verified and reused offline;
refreshing requires a new cache directory. No implicit data revisions occur.
Pagination cycles, missing symbols, duplicate/out-of-order bars, malformed pages,
and page limits fail before publication. 429 and selected server/network failures
receive bounded retries; other HTTP failures stop with a sanitized status.

Historical revised bars use the existing explicit zero-publication-delay research
assumption; they are not a reconstruction of what a live feed knew at each instant.
Authentication/entitlements and real-data quality must still be validated on an
actual requested date range. No authenticated download was run during M15 because
the environment had no credentials.

## Scale measurement

```sh
.venv/bin/python scripts/benchmark_replay.py --bars 10000
```

This measures synthetic data construction/preflight and replay separately using
a no-op Python strategy. It reports code identity and bars/second. Results are
machine/build/workload dependent; they do not validate historical trading merit.
Authenticated acquisition works (2026-11-24): SPY/QQQ 5-minute bars 2016–2026 were downloaded and loaded into the [truth database](truth-database.md). Download one calendar year per request, because the downloader rejects combined files over 32 MiB.

## Paper coordination

See [ADR 0007](adr/0007-paper-coordination.md). `qte.paper` implements a durable
offline-tested coordinator against a gateway protocol. It does not authenticate,
connect a strategy, or place brokerage orders. The local snapshot's applied
execution IDs must come from the eventual durable accounting bridge. A heartbeat
may be advanced only on confirmed transport health. Lease recovery is an explicit
operator action after the former owner has stopped; it persistently halts trading
until reconciliation and an explicit halt clear succeed.

## Declared session data

`qte run` and `qte validate-data` accept a single captured JSON file with
`data.adapter = "session_bundle"`. The GUI offers the same adapter under
Research and Data. Try the included synthetic session, closure and early close.

```sh
.venv/bin/python -m qte run --config examples/session-run.json --export-version 2 --output build/my-session-run
```

Register `build/my-session-run` in Research to view its candles and fills.
The input is raw, action-free USD equity OHLCV with share volume. Every
calendar date in the declared coverage has either an open and close or two
nulls. Each bar must exactly match a daily or open-anchored hourly bucket.
The final hourly bucket may be short. Publish each bar at its physical end
plus the declared nanosecond delay. The chart file retains both clocks.
Sampling accepts an explicit grid spanning the first opening through final
publication. Choose the staleness bound and annualization convention for that
grid. Fixed-window comparisons still require their existing fixed interval.

For a historical export from `exchange_calendars`, select the venue, pin the
source version and coverage, and retain its original bytes and hash. Read its
UTC open and close for each declared session. Use an explicit closure for
each confirmed non-session within coverage. Check breaks before adapting a
venue to this one-session-per-day profile. The example file shows every
required field. [Session bundle](../examples/session-bars.json) ·
[exchange_calendars schedule API](https://github.com/gerrymanoim/exchange_calendars)
