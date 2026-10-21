# G8b: bounded synthetic benchmarks

Open **System → Benchmarks**, choose **No-op (no trades)** or **SMA**, and
explicitly run 1–100,000 bars (default 10,000). The GUI timeout is 300 seconds.
Refresh lists/status manually; queued/running jobs use the existing cancellation,
single-active-job queue and restart-interruption behavior. Opening the page never
launches work. An uncertain submission retries the same frozen request/key.

This measures the existing `scripts/benchmark_replay.py` workload, now shared
through `qte.benchmark.run_benchmark`. The standalone CLI retains its flags,
10,000 default and 1,000,000-bar maximum; the GUI deliberately has a smaller cap.
No build, package installation, download or broker operation occurs.

## Measurement assumptions

- Synthetic SPY minute bars use the existing repeating 80-bar price pattern,
  ±1 high/low and volume 1,000. This is not a historical ETF/session dataset.
- `hold` means a no-op callback, **not buy-and-hold**. It makes no orders/fills.
- `sma` uses the existing moving-average strategy with periods 5/20, quantity 1,
  initial cash 100,000 and history capacity 64. No configuration sweep occurs.
- One sample, no warm-up. `perf_counter` measures synthetic generation plus
  dataset construction, then strategy construction plus engine replay separately.
  Throughput is bars divided by replay seconds. Startup, identity capture and
  evidence persistence are outside these timings.
- Timings vary with system load, scheduling, caches and hardware. These are not
  statistically controlled performance comparisons or evidence of trading merit.
  Fills/equity are secondary mechanics metadata, not strategy recommendations.

## Evidence and identity

The scheduler pins `benchmark-request.json` and `input-manifest.json` before
dispatch. Only the repository `.venv/bin/python` and fixed benchmark script/flags
may execute. The private worker reuses bounded output draining and process-tree
cleanup for cancellation, timeout, owner loss and signals. Provider credentials,
user Python paths and user caches are not inherited by the benchmark.

A successful command must return one strictly validated measurement. The worker
exclusively writes `manifest.json`, then `complete.json` with its SHA-256. Parent
acceptance independently checks the request, workload, runtimes and completion.
Failed, canceled, malformed or partial output is never promoted to a successful
timing record. A complete historical record with changed end identity remains
distinguishable from a successful stable run.

The manifest records fixed argv, nanosecond start/end times, workload, Python/OS/
architecture/CPU-count metadata, and start/end explicit file/runtime identities.
The existing Python-test preflight supplies this deliberately broad scope:
source, tests/docs/scripts, loaded extension/editable mapping and build-input
files/tool versions. It does **not run tests**. It currently requires those
preflight prerequisites; unrelated edits within the scope can invalidate a job.
These are consistency observations, not an execution attestation, a complete
environment fingerprint or compiled-source/flags proof. **Source-to-binary
correspondence remains unknown.** Reading history never rechecks current files.

The authenticated view exposes only a strict synthetic measurement projection.
Raw `runner.log`, request files and manifests have no download routes; benchmark
job artifacts cannot be registered through the generic research catalog. Unlike
test output, this projection contains no arbitrary test names or log excerpts
and does not require a holdout disclosure.

## API

| Route | Contract |
|---|---|
| `GET /api/v1/system/benchmarks` | Filtered history; offset/limit, maximum 200 |
| `POST /api/v1/system/benchmarks` | Exact schema 1, bars, strategy, timeout seconds (1–1800), idempotency key |
| `GET /api/v1/system/benchmarks/{id}` | Job plus verified projection or unavailable evidence; no query parameters |
| `POST /api/v1/jobs/{id}/cancel` | Existing explicit cancellation contract |

All routes require the existing session/request token; mutations require exact
same-origin checks. No browser-supplied commands, paths or environment fields.
