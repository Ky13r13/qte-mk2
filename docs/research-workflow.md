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
  --action-free --cache-dir .cache/alpaca
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
Real-history benchmarking remains pending authenticated data acquisition.

## Paper coordination

See [ADR 0007](adr/0007-paper-coordination.md). `qte.paper` implements a durable
offline-tested coordinator against a gateway protocol. It does not authenticate,
connect a strategy, or place brokerage orders. The local snapshot's applied
execution IDs must come from the eventual durable accounting bridge. A heartbeat
may be advanced only on confirmed transport health. Lease recovery is an explicit
operator action after the former owner has stopped; it persistently halts trading
until reconciliation and an explicit halt clear succeed.
