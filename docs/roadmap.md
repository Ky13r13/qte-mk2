# Incremental QTE development roadmap

This plan implements the defaults in [architecture.md](architecture.md). ADRs
describe target behavior, not existing functionality. Each numbered row is a
separate reviewable change, not permission to build the whole engine in one turn.
Do not create empty modules merely to match this table.

## Milestones and acceptance gates

| Milestone | Scoped deliverable / depends on | Required acceptance before advancing |
|---|---|---|
| M0 — foundation (implemented) | Canonical Bar, strict validation, GCC/CMake/CTest, `.venv`, AGENTS | Existing eleven test functions pass; zero/NaN/infinity/interval/OHLC boundary fixtures retained |
| M1a — quantities and IDs (implemented) | Domain quantity/ID types; ADR 0005 | Positive magnitudes, exact arithmetic, overflow and fractional rejection; no engine |
| M1b — price policy and metadata (implemented) | Minimal instrument specification, tick conversion, profile config; M1a | Tick neighbors, adverse rounding, currency/tick validation; preserve Bar API |
| M2a — order requests/state (implemented) | Request validation, transition table, reasons; M1a/b, ADR 0001 | All type/price combinations, legal/illegal transitions and cancels; no matching |
| M2b — fills (implemented) | Immutable fill values, cumulative quantity checks and deduplication; M2a | Overfill, duplicate, terminal fill and metadata mismatch rejected without mutation |
| M3a — market/limit execution (implemented) | Pure open-only matching and costs; M2b, ADR 0003 | Buy/sell/equality/gap/tick/commission fixtures; future OHLCV cannot affect results |
| M3b — stop/stop-limit execution (implemented) | Persistent trigger state; M3a | Both sides, gap through limit, trigger persists, cost breach, same-open trigger/fill |
| M4a — position accounting (implemented) | Average cost, reductions/closures/reversals, marks; M2b, ADR 0004 | Long and short hand calculations; flat reset, no realized PnL on marks |
| M4b — cash ledger (implemented) | Portfolio fill transaction, cash/fees/exposure, duplicate protection; M4a | Full six-fill ADR fixture, atomic invalid-fill failure, equity identity, sequence tests |
| M4c — trade episodes (implemented) | Closed/open episode records and fee allocation; M4b | Scaling, reversal allocation, open episodes excluded from closed-trade statistics |
| M5 — risk (implemented) | Separate submission and fill-time checks; M3b/M4b | Pending reservations, long-only, leverage/cash gaps, reductions during breaches; no forced liquidation |
| M6 — C++ strategy contract (implemented) | Lifecycle, snapshot context, command buffering using test driver; M2a/M4b | No direct mutation, pending receipt semantics, on_end restriction, expired context/reentrancy checks |
| M7a — dataset preflight (implemented) | Instrument/dataset metadata and strict stream checks; M1b, ADR 0002 | Duplicate/order/overlap/duration/action/currency rejection, gaps reported, zero-price trade rejection while Bar zero test still passes |
| M7b — deterministic scheduler (implemented) | Ordered merge, event phases and bounded histories; M7a | Equal-time multi-symbol trace, stream-order invariance, missing bars, future-data perturbation |
| M8a — replay integration (implemented) | One symbol; wire strategy, risk, execution and atomic ledger commit; M3b–M7b | Exact next-open fills, callback ordering, cancel races, failure propagation, final cancellations; no implicit liquidation |
| M8b — multisymbol/results (implemented) | Marks, equity curve, order/fill/trade logs, positions and run manifest; M8a | Stable priority under competing orders, stale marks, deterministic replay and complete input/build identity |
| M9a — analytics basics (implemented) | Returns, total return, drawdown, trade statistics, turnover/exposure; M8b | Hand-computed flat/loss/zero-trade/open-trade examples; no division by zero |
| M9b — annualized analytics (implemented) | Explicit sampling/annualization contract; M9a | Irregular timestamps rejected; Sharpe/Sortino/Calmar and undefined edge cases |
| M10a — Python package (implemented) | Optional pybind11 target, value/config/result bindings; M8b, ADR 0006 | Import with existing local tooling, nanosecond preservation, owned snapshots, C++-only build works |
| M10b — Python strategies (implemented) | Trampoline, scoped context/GIL rules; M10a/M6 | C++/Python parity fixture, lifecycle/error/retention/reentrancy tests, repeatability |
| M11a — first provider adapter (implemented) | Alpaca saved-fixture parsing and normalized provenance; M7a/M10a | Offline contract tests, missing required fields fail, no provider branch in core; network/auth work separately approved |
| M11b — portability adapter (implemented) | CSV adapter with explicit mapping/schema; M11a | Equivalent Alpaca/CSV fixtures produce equal economic results without strategy edits |
| M12 — research baseline (implemented) | Formal deterministic SMA-regime strategy, config and comparison report; M9b/M10b/M11b | No duplicate pending orders, cost sensitivity and in/out-of-sample labels, reproducible reference run |
| M13a — order feedback (implemented) | Read-only lifecycle notifications and SMA recovery | Rejection, execution/user/end cancellation, fill, no recursive retries, retained snapshots |
| M13b — analytics sampling (implemented) | Explicit UTC grids, bounded carry, session observation convention | Actual-engine annualization, equal-time collapse, stale/gap rejection, raw-event drawdown |
| M13c — research integrity (implemented) | Chronological splits, strategy/code identity, metadata checks | Reject overlap, timeframe mismatch, missing pagination declaration, malformed CSV |
| M14 — research command (implemented) | JSON configuration to reports, manifests and CSV artifacts | Subprocess execution, two-fill example, unknown-key rejection, overwrite protection |
| M15 — acquisition (offline verified; authenticated validation pending) | Alpaca GET pagination, immutable local cache, explicit feed, scale tool | Offline retries/corruption/pagination tests and synthetic benchmarks; real historical run requires credentials |
| M16 — paper architecture (offline controller implemented) | Journaled intent, persistent IDs, acknowledgements, recovery, controls | Fake-gateway tests; actual paper gateway and durable C++ ledger bridge remain separate integration work |

M3 uses an isolated open-event driver until the scheduler arrives. M6 similarly
uses a callback test driver; neither requires implementing the engine early.
M10 requires no new package merely because an example uses it; inspect existing
`.venv` and request approval for downloads/installations when needed.

Optional follow-on units, each with its own review: causal volume-capped partial
fills, OHLC-touch execution, split/dividend ledger events, fractional instruments,
advanced risk, walk-forward robustness, benchmarks, then paper-trading adapters.
Do not describe the first backtester as ready for live execution.

## Analytics defaults to settle before M9

Use mark-to-market equity returns, including transaction costs; no deposits or
withdrawals in the first profile. Initial equity anchors total return/drawdown.
Drawdown at point i is `E_i / max(E_0..E_i) - 1` for a positive peak; report
maximum drawdown as the largest positive loss magnitude. No trades does not mean
no drawdown. Negative/nonpositive equity makes logarithmic/annualized measures
undefined; retain the raw series and reason.

Before M9b, implement a declared equal-spacing sampling policy or reject
annualized requests on irregular event samples. Recommend user-specified sample
grid and periods-per-year, risk-free rate zero, sample standard deviation for
volatility/Sharpe, and downside RMS relative to zero for Sortino. For N declared
periods per year, arithmetic returns r and annual effective risk-free rate Rf,
use rf = (1+Rf)^(1/N)-1, volatility = sample_std(r)*sqrt(N), and Sharpe =
mean(r-rf)/sample_std(r-rf)*sqrt(N). Sortino uses mean(r) divided by
sqrt(mean(min(r,0)^2)), times sqrt(N), including all samples in that mean.
Fewer than two samples for sample deviation, or any zero denominator, yields
undefined with reason. A nonpositive previous equity makes its return undefined.
Record these choices; never infer 252 from bar count. Average exposure is elapsed-time weighted
between event points; turnover is total absolute filled notional / initial equity
(unannualized). Profit factor is gross winning episode net PnL / absolute gross
losing episode net PnL; zero losing sum yields undefined with reason. Expectancy
is mean closed-episode net PnL, including breakeven episodes. Win rate is winning
closed episodes / all closed episodes; no closed episodes is undefined. Average
winning and losing trade use their respective episode subsets, with signed loss
values and undefined empty means. Annual return uses
actual elapsed years (365.25 days) with positive starting/ending equity; zero
duration is undefined. Calmar divides annual return by maximum drawdown magnitude;
zero denominators never silently produce zero or an arbitrary infinity.

## Review and verification workflow

For each unit: inspect relevant files/status, explain any contract change, edit
the scoped component, add exact fixtures, compile, test, and report blockers.
Use fatal test guards before dereferencing containers. Tests may call components
directly before their production orchestration exists. Add invariant tests, not
only tests mirroring the implementation. Do not delete failing tests.

Known working commands, from repository root:

```sh
PATH="$PWD/.venv/bin:$PATH" CXX=/usr/bin/g++ cmake --preset dev
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
```

Use `build/local-dev`; never inherit the stale `build/dev` compiler cache. Before
M10 there are no Python tests to run. From M10 run `.venv/bin/python -m pytest
tests/python` as well. No installation, sudo, container execution or outside-
repository mutation is part of these steps. Preserve `.git` until its visibility
is resolved with explicit user approval; report absent Git identity honestly.

For documentation-only units, verify references/consistency and run the existing
suite; do not invent executable tests for prose. Numerical/financial changes must
include regression fixtures. Record deferred decisions in the architecture index
and revise the relevant ADR before implementing an incompatible behavior.

## Prioritized task list (2026-09-19)

The user prioritizes strategy research for US ETFs (hourly and daily) now and
explicitly permits synthetic fixtures while no data credentials are available.
Synthetic outcomes establish mechanics only; no candidate is promoted on them.
All candidates and failures remain in the research record. No account connection
or trade placement is authorized by this research request.

| Task | Scope | Acceptance / dependency |
|---|---|---|
| R1 — strategy lab (implemented; synthetic verified) | Six causal long-only families, macro router, macro/volatility filter, qualified external dealer overlay, synthetic stress scenarios, all-candidate comparison | Formal rules; actual-engine lifecycle/look-ahead tests; recorded seeds/parameters; no synthetic promotion; frozen validation selection before holdout; no real-market merit claimed |
| R2 — credible ETF datasets (queued) | Finish M15 authenticated validation; point-in-time macro vintages; explicit session availability for daily bars; split/dividend ledger support before action-bearing runs | Credentials/data rights and action validation; UTC session/DST/holiday/short-day tests; no pretending 24-hour synthetic bars are exchange daily bars |
| R3 — real-data research gate (queued) | Pre-register compact strategy slate; compare against cash and risk-matched buy-and-hold across disjoint regimes at base/stressed costs | Untouched chronological holdout; rejected candidates retained; sufficient trades/regime coverage; selection may return no winner |
| R4 — robustness (queued) | Rolling walk-forward, neighboring-parameter/cost sensitivity, block-resampled uncertainty, multiple-testing accounting | Training only in past folds; dependent returns not IID; disclose all search trials; no holdout reuse after tuning |
| R5 — usable research analysis (queued) | Strategy comparison charts, regime attribution, report review, real-history performance measurement | Match manifest/artifacts; profile before optimizing; separate synthetic throughput from financial evidence |
| R6 — measured scaling (queued) | Benchmark active-order iteration and indicator throughput on long frequent-trading runs | The current engine scans historical terminal orders each event; preserve fill/event priority and prove equivalent outputs before optimization |
| M17 — durable accounting bridge (queued) | Bridge broker execution IDs to authoritative C++ ledger, durable exactly-once application/recovery | Crash at each commit boundary, duplicate/conflicting fills, cash/position reconciliation; no second accounting authority |
| M18 — Alpaca paper gateway (queued) | Paper-only acknowledgements, submit/cancel/lookup/execution retrieval and timeout reconciliation | Fake transport/contract tests, paper-account identity, no blind resend after unknown outcome; depends on M17 |
| M19 — paper strategy runner (queued) | Feed/session availability, callbacks, risk/coordinator bridge, reconnect/staleness controls | Startup fail-closed, gaps/revisions/order races, deterministic replay of recorded events; depends on M18 and session contract |
| M20 — supervised paper validation (queued) | One explicitly approved strategy/account, small caps and monitored sessions | Verify reconciliation/restart/disconnect/kill switch; independent go/no-go review; never implies real-money approval |
| M21a — read-only broker connections (offline verified) | Robinhood official crypto reads; Schwab GET lookup from caller-supplied official OpenAPI, pinned hosts, no automatic account/trade actions | Mock transport/auth/path/redaction tests; production endpoints never called paper; Schwab schema access and authenticated checks pending; crypto isolated from equity core |
| M21b — authenticated connection validation (blocked on access) | Validate approved credentials/entitlements and provider contracts without trading | User-approved account read access; no secret output; OAuth/signing lifecycle and schema checks |
| M21c — broker execution integration (deferred) | Provider-specific acknowledgement/execution mapping behind durable reconciliation | Depends on M17–M20; official instrument support and numeric profile first; explicit trading authorization, never unofficial equities APIs |

M16 is an offline coordinator, not an operational paper trader. Actual dealer
positioning requires qualified external observations; price bars alone do not
establish whether dealers are buying or selling. The first macro score/routing
policy is a testable hypothesis, not a fitted or validated economic model.

## Local GUI workstream (implementation started)

The [GUI architecture and acceptance gates](gui-architecture.md) and
[ADR 0010](adr/0010-local-research-ui.md) define G0–G9: dependency approval,
minimal local shell/documentation, verified artifact catalog, run/lab views,
complete result exports, bounded research jobs, data workflows, declarative
comparison/router interfaces, test/benchmark evidence, and read-only connection
inspection. G0 dependencies are approved/installed and G1 is implemented and
reviewed. G2/G3a are implemented/reviewed; G3b owned-result bindings are underway.
See the [task log](gui-task-log.md)
and [usage guide](gui-usage.md) for tested capability and remaining work.

First deliverable: read-only library and existing results (G1a, G1b, G2, G3a, G4).
Job launch follows evidence validation; broker reads remain separately authorized.
GUI coverage does not imply completion of R2–R4 or M17–M20. Preserve current CLI,
canonical Bar semantics, accounting authority and all existing artifacts.

G0 preflight on 2026-09-23 is [recorded here](gui-g0-preflight.md). The
[post-boot proposal](gui-dependencies.md) adds resolved optional dependency locks
and first-screen contract examples after SSL/RPM checks passed. The user approved
the locked packages and available Sol/Terra builders with Astra review, and later
extended implementation through G4/G5 after the preceding gates. No current
system repair is requested or authorized.
