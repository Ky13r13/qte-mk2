# QTE development audit — November 21, 2026

> **Status:** Historical snapshot (2026-11-21). For current capabilities see [docs/README.md](README.md).

The Linux research path runs from a captured dataset through the native engine
to verifiable results in the GUI. This review covered the native core and
bindings, ingestion and clocks, execution and accounting, Python research and
exports, GUI routes and jobs, broker adapters, packaging, documentation and
local evidence. Strategy discovery is paused. Historical studies remain available.

| Area | Verified or repaired | Remaining boundary |
|---|---|---|
| Equity engine and bindings | GCC C++20 build, lifecycle, execution, risk, ledger, analytics and callback tests | Adjustments, corporate-action events and multisettlement accounting need explicit contracts |
| Futures | Fill-time price, actual fee, partial fills and outstanding margin reservations use the same ledger logic as commit | In-process `FuturesOrderLifecycle` now owns admission and fill transitions. Durable (crash-safe) ownership, subsequent quotes, settlement source and expiry/roll integration remain |
| Session-aware research | CLI and GUI accept declared schedules, short sessions and publication delays. Exports retain both bar end and availability and rebuild the dataset hash | Venue and corporate-action evidence are caller supplied. Session-aware multi-window comparisons need common sampling semantics |
| Options | Actual Theta quotes replay from supplied files. Late ticks are discarded and observation gaps leave the exit unresolved. American pricing was checked against two published numerical cases | Representative historical books, executable liquidity, assignment/exercise accounting and partial-leg risk |
| Acquisition and brokers | Schwab REST captures, offline stream delta normalization, Robinhood observations, Alpaca request/cache validation, offline ticket staging and paper journal. Market-hours REST collector, locked forward protocol and hash-chained paper ledger with restart reconciliation, all tested against a simulated Schwab market | No live collection session has run yet. REST polling is not a tick tape. Paper fills are quote replays, not broker fills; the broker paper gateway remains unconnected |
| GUI, research and evidence | Restored Library links, aligned platform capabilities, validated source identity in the Book151 exporter, and retained catalog inspection for malformed lab reports with protected contents. A failed job-owner unlock now releases its descriptor, allowing the next manager to start | Native Windows storage, process supervision and acceptance. The five research roles use explicit opt-in model connections |
| Historical studies | Rebuilt the fixed one-year panel outside its capture directory. The daily JSON, CSV and extreme-session list matched the originals byte for byte. The report now derives coverage from its rows | Model prices, synthetic controls, partial quote records and observed fills retain distinct interpretations. Shared-account walk-forward and matched-capital comparison remain open |
| Build and recovery | Native builds use `build/local-dev`; Python uses the repository venv. Standard source and captured data stay separate from generated output | Two old generated directories returned OS read errors. Ignored captures and uncommitted source require separate backups |

## Reproduce the checks that matter

From the repository root with the existing venv:

```sh
.venv/bin/python -m qte run --config examples/session-run.json --export-version 2 --output build/my-session-run
.venv/bin/python -m qte audit-evidence build/spx-0dte-timing/final/manifest.json build/book151-schwab-timeframes-20261121/manifest.json
.venv/bin/python examples/daily_regime_year.py --output build/my-year-replay
.venv/bin/python -m qte.spx_0dte_quotes --output build/my-quote-replay
```

The last three commands need the corresponding local archives. The session
example runs from the checkout. Use new output directories for each replay.

The checksum audit streamed 1,421 listed files in 24 archived manifests and
found a match for each. It checks recorded output bytes. It does not establish
source authenticity, historical executable prices or unchanged source code.
All 164 source files in the archived year manifest also matched their recorded
digests. The replay retained all coverage and exclusion counts.
The Book151 chart export reproduced all 177 files. The Theta pilot reproduced
its archived P&Ls on 582,807 admitted quote observations for a single contract.
Its source coverage ended at 10:27:53 ET.

## External references used

Awesome Quant led to two relevant upstream projects. The calendar interface
helped specify explicit session open/close inputs, venue selection and breaks.
QuantLib provided independent American call reference values with flat curves
and one year to maturity. QTE's 1,024-step lattice produced 8.86401 and
12.33359 versus 8.87151 and 12.33600 respectively, within a cent per share in
both cases. These are pricing checks. Both references can be used without adding
a runtime dependency. [Awesome Quant](https://github.com/wilsonfreitas/awesome-quant) ·
[exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) ·
[QuantLib American option reference cases](https://github.com/lballabio/QuantLib/blob/master/test-suite/americanoption.cpp)

## Build and validation

The test build uses the installed Fedora GCC toolchain, CMake in the repository
venv and `build/local-dev`. The full native suite passed all 19 targets. The
JavaScript suite passed 65 tests. The full Python suite passed 2,275 tests and
skipped eight cases that require native Windows handles. The test log is in
`build/audit-tests-KEldvc/release.log`. Each of the 63 registered Library
documents resolves locally. QTE was rebuilt and installed editable in the
existing venv using the local toolchain and no package index. On that build
15 binding, research and session regressions passed, `pip check` passed,
and an end-to-end session run produced 14 verified files. The completed
GUI-registerable artifact is `build/audit-tests-KEldvc/installed-session-run`.

The initial full Python run exposed a catalog regression in 12 tests. The
catalog now protects every digest when a lab's role map is malformed and
allows inspection of its status. Structured experiment and raw-file access
continue to enforce their own permissions. Two stale tests were updated to
check the expanded research menu and the role-filtered candle endpoint.
The cleanup also replaced empty production Python exception handlers with
explicit error handling or cleanup reporting. File-close diagnostics record
the operation and errno only.

## Storage finding

Direct directory enumeration returned Linux error 5 at both paths below.
The failures occurred in old GUI test outputs. The audit left those trees intact.
The cause remains undiagnosed. Back up irreplaceable captures and working files
before any filesystem repair or deletion.

```text
build/test-gui-g5b1-final-20261006/test_role_mismatched_scenario_0/build/lab/hourly/runs
build/test-gui-g6a-resume-confirm-20261012/test_lone_surrogates_fail_clos5/build/lab/hourly/runs/train-low_vol_trend--trend-macro--base
```

## November 22 live desk integration

Schwab SPX/SPXW streaming, calendar-aware paper execution probes, displayed-size liquidation and spot/IV/time scenarios share a local GUI and a reproducible offline example. The forward protocol and its paper ledger remain separate from independent execution probes. [Live desk guide](live-desk.md)

## Next integration milestone

Record one permissioned, market-hours option journal with a synchronized
underlying, a known disconnect/reconnect, sizes and receipt clocks. Replay
the same captured bytes twice, and reconcile the resulting available and
unavailable quotes before connecting the existing paper coordinator. Paper
integration can then be tested against observed events without controlling an
account. [Capture and replay](tick-data-acquisition.md) · [Paper coordinator](adr/0007-paper-coordination.md)
