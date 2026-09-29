# M13–M16 validation record

Verified on 2026-09-17 with the existing native GCC toolchain, repository `.venv`,
C++ development build in `build/local-dev`, and Release Python extension in
`build/python-package`. No external dependency or system package was installed.

## Acceptance results

- M13a: lifecycle updates, read-only callback enforcement, rejection/cancellation
  recovery, fill state, and owned Python callback values pass.
- M13b: real-engine sampled annualization, duplicate timestamp collapse,
  causal bounded carry, explicit nonuniform observation grids, staleness failures,
  and undefined annualization overflow pass.
- M13c: overlapping train/test rejection, recorded parameters/code/data identity,
  and strict Alpaca timeframe/feed/completeness declarations pass.
- M14: executable CLI subprocess produces a two-fill/one-closed-trade report;
  rejects unknown configuration and existing output directories. Example output
  was generated at `build/m14-reviewed-run` with a completion checksum manifest.
- M15: paginated downloads, offline cache reuse, corruption rejection, incomplete
  pages, cyclic tokens, invalid/duplicate bars, retry bounds and sanitized errors
  pass offline. Both Alpaca credential environment variables were absent;
  authenticated acquisition and real historical replay remain unverified.
- M16: 14 fake-gateway tests cover durable identity, reconciliation, restart,
  timeout ambiguity, late fill recovery, duplicate/conflicting executions, partial
  fills, ledger watermarks, account mismatch, stale heartbeat, persistent halt,
  cancellation uncertainty, size/risk controls and journal ownership. There is
  no real paper gateway, strategy routing, or durable C++ ledger bridge yet.

Final suite results: **15/15 CTest executables; 47/47 Python tests**.
`git diff --check` passed. Git history and remotes were not changed.

## Build and test commands

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
PIP_NO_CACHE_DIR=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-final
.venv/bin/python -m qte run --config examples/research-run.json --output build/m14-reviewed-run
git diff --check
```

Focused Python suites were also run after each component landed. The recovery
suite was rerun after adding missing-execution import following a disconnect.

## Synthetic scale measurements

```sh
.venv/bin/python scripts/benchmark_replay.py --bars 100000 --strategy hold
.venv/bin/python scripts/benchmark_replay.py --bars 10000 --strategy sma
```

| Workload | Load/preflight | Replay | Bars/second | Fills |
|---|---:|---:|---:|---:|
| 100,000 bars, no-op Python strategy | 0.142 s | 0.515 s | ~194,224 | 0 |
| 10,000 bars, SMA strategy | 0.013 s | 0.432 s | ~23,131 | 250 |

Single observations from this machine, not performance guarantees. Both datasets
are synthetic. The second case includes history reads, Python feature calculation,
order feedback, execution and accounting. No optimization was justified solely
from these measurements.
