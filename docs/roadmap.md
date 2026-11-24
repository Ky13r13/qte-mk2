# Incremental QTE development roadmap

## Current priority — path to live trading (2026-11-24)

The live status table is in [docs/README.md](README.md#path-to-live-trading-status).
The gates are, in order:
1. A strategy passes the search protocol: a deflated probability that holds up,
   consistency between dev and validation, and robust neighboring parameters.
2. It passes a single human-run holdout.
3. It is re-run through `BacktestEngine` with futures wiring (D2b.2b/D3).
4. It runs in shadow mode on live Schwab data.
5. It paper trades for a fixed number of sessions.
6. It goes through a Schwab order gateway with hard limits and a kill switch.

Sections below are the earlier milestone history and the reasoning behind it.

The [November development audit](development-audit.md) records verified entry points,
repairs and open integration gates. Strategy discovery is paused.


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
| M15 — acquisition (authenticated; 22 real SPY/QQQ 5-minute downloads 2016–2026 on 2026-11-24) | Alpaca GET pagination, immutable local cache, explicit feed, scale tool | Offline retries/corruption/pagination tests and synthetic benchmarks; real historical run requires credentials |
| M16 — paper architecture (offline controller implemented) | Journaled intent, persistent IDs, acknowledgements, recovery, controls | Fake-gateway tests; actual paper gateway and durable C++ ledger bridge remain separate integration work |

M3 uses an isolated open-event driver until the scheduler arrives. M6 similarly
uses a callback test driver; neither requires implementing the engine early.
M10 requires no new package merely because an example uses it; inspect existing
`.venv` and request approval for downloads/installations when needed.

Optional follow-on units, each with its own review: causal volume-capped partial
fills, OHLC-touch execution, split/dividend ledger events, fractional instruments,
advanced risk, walk-forward robustness, benchmarks, then paper-trading adapters.
Do not describe the first backtester as ready for live execution.

## Nasdaq derivative research (both hypotheses requested 2026-11-03)

Windows work remains paused and preserved. See the
[strategy specification](strategies/nasdaq-gamma-scalping.md) and
[accounting ADR](adr/0013-derivative-research-foundation.md).

| Milestone | Scope | Acceptance gate |
|---|---|---|
| D1 — shared foundation (implemented) | Canonical contracts, multiplier-aware existing ledger, pure hedge/dealer decision policies | Exact accounting/causality fixtures; unchanged equities; derivative replay/risk fails closed |
| D2a — variation settlement (implemented) | Explicit idempotent cash settlement, separate cash/economic bases, causal event journal | Basis/PnL conservation, unchanged equity/episodes, close/reversal/duplicate/cutoff/overflow regressions; no engine wiring |
| D2b.1 — margin budgets (implemented) | Declared per-contract initial/maintenance/fee schedule, no-offset pending budget, conservative available collateral; runnable synthetic C++ demo | Current/pending quantities, qualified metadata, causal/fresh inputs, quantity overflow; report only, no order approval |
| D2b.2a — submission admission (implemented) | Read-only candidate + pending margin decision, bounded nonreversing reductions under breaches, fresh target marks/cutoff, runnable accept/reject/cancel demo | Exact budget denials, competing reservations, long/short reduction and causal/lifecycle checks; no engine wiring |
| D2b.2b — fill-time admission (ledger and in-process lifecycle owner implemented; engine wiring open) | Recheck inventory/reservations at execution, authoritative per-contract fees, single pending-order owner (`FuturesOrderLifecycle`) | Gap/cost stress, partial fills, reduction remains nonreversing, rejection/cancel recovery and reservation release; no equity-notional cash assumptions |
| D3 — executable market observations | Quote/Greek availability, contract/session mapping, subsequent-quote execution | No look-ahead, stale/missing data rejection, realistic size/latency/cost handling |
| D4a — dealer-gamma futures strategy | Wire qualified dealer policy through normal lifecycle/config/reports | Rejection/cancel recovery, out-of-sample synthetic integrity checks, explicit flatten failures |
| D4b — option gamma scalping | Straddle selection, hedge cadence, partial legs and liquidation | Combined option/hedge accounting, late fills and entry timeout, no implicit exercise/delivery |
| D5 — research/GUI integration | Normal configuration, report provenance, comparison and library | Runnable paths only after D2–D4; all candidates retained; licensed historical-data validation separately required |

These milestones are implementation gates, not permission to trade or acquire
paid data. Neither synthetic paths nor a positive single backtest establish an edge.

V1 sidecar — **implemented offline**: [VIX expected-move analysis](strategies/vix-expected-moves.md)
adds saved Cboe VX settlement adaptation, explicit Nasdaq proxy assumptions,
calendar-time sigma bands, completed-candle OHLC endpoint distances and optional
external dollar-gamma scenarios. Deterministic math/availability tests and a
public settlement smoke check pass; the example target data remains synthetic.
Not a calibrated forecast, interactive GUI form or derivative strategy. D2b.2b–D5
remain gated; no futures/options execution is enabled by this analysis module.

GL1 — **implemented model calculator**: [Gamma Lab](gamma-lab.md) adds an
authenticated GUI surface, chosen-expiry slice and numerical curvature check
from manual Black-76 inputs. Recalculates on edit; no market feed, dealer
inventory, automatic VX-to-Nasdaq IV conversion or derivative execution.
Market-live surfaces require a separately qualified options-chain source.

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
| R2 — credible ETF datasets (in progress; not research-ready) | Finish M15 authenticated validation; point-in-time macro vintages; explicit session availability for daily bars; split/dividend ledger support before action-bearing runs | Credentials/data rights and action validation; UTC session/DST/holiday/short-day tests; no pretending 24-hour synthetic bars are exchange daily bars |
| R2a — offline session contract (implemented; synthetic verified) | Immutable explicit session/closure snapshot, daily/hourly windows, availability and close grid; [ADR 0011](adr/0011-session-calendar.md) | Complete coverage, declared DST/holiday/short-day fixtures, exact UTC ns, immutable source identity; actual-engine close-grid regression; no replay/adapter semantics changed |
| R2b — session-aware replay bridge (implemented; offline synthetic tests) | Opt-in explicit-availability envelope, callback clock, stale-mark protection, session adapter; [ADR 0012](adr/0012-explicit-availability.md) | Mixed durations and delayed publication cannot expose data early or fill at an already-passed open; legacy hash/tests retained; Python API only |
| R2c — session ingestion (single-run, chart and validation implemented; comparisons open) | Versioned local session/bar files and bounded config/GUI integration; explicit sampling/export/comparison semantics | Preserve timing/calendar provenance and physical-vs-replay bounds; reject guessed calendar/frequency; online acquisition and actions remain separate gates |
| W1 — native Windows Library/Connections (implemented; native acceptance pending) | Platform-gated routes, pinned handle-based document reads, PowerShell launcher | Linux regressions plus Windows build/handle/browser checks; no artifact/job fallback; [Windows plan](windows-gui.md) |
| W2 — Windows artifact/Data parity (W2a/W2b and W2c.1–.3 foundations implemented; native acceptance pending) | Native durable journal writes/SQLite containment and W2d integration remain | Reparse/race protections and holdout invariants before enabling historical results; routes remain blocked |
| W3 — Windows job parity (queued) | Job Objects, locks, owner-death cleanup, timeouts/cancellation/recovery and environment checks | Whole-process-tree cleanup, restart/double-owner tests; then research/test/benchmark launch |
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
reviewed. G2 through G5a are implemented/reviewed under the
[job contract](gui-jobs.md). G5b.1 shared validators, G5b.2a previews and G5b.2b
inline/fixed-lab jobs are reviewed. G5b.3 browser forms are test-build ready;
extended browser acceptance is pending. G6a local data validation and pinned
source reuse and G6b gated download controls are accepted offline. G7a.1 shared
configuration/CLI exports, G7a.2a guarded catalog access and G7a.2b safe comparison
views are reviewed. G7a.3a snapshots, G7a.3b backend preview/job/worker and
G7a.3c browser forms are accepted offline (1197 Python tests, 15/15 CTest,
25 JS; both workflows passed local synthetic browser runs). Downloads are
disabled by default. G8 followed in the requested sequence; each later increment
retains its own acceptance gate. The
G8a [evidence contract](gui-test-evidence.md) now has reviewed parsing, explicit
identity capture, trusted discovery, pinned requests and completion-last writing
(G8a.1/G8a.2a). Private supervised jobs (G8a.2b) are reviewed and accepted
(1417 Python / 15 CTest / 25 JS). G8a.3 System controls and guarded evidence views
are now accepted (1430 Python / 15 CTest / 37 JS), including actual browser
launch/reveal/current-observation checks. Raw test downloads remain disabled.
G8b [bounded benchmark jobs/evidence](gui-benchmarks.md) are implemented and
reviewed (1485 Python / 15 CTest / 45 JS; real authenticated local API jobs pass).
The earlier localhost browser block did not recur; an October 21 no-op
launch/status/evidence browser smoke check passed. Extended browser acceptance
remains separate.
G7b now exposes an explicit macro/router profile in single-run CLI/GUI research
and optional guarded as-of route diagnostics, reusing the existing strategy and
filter. [Profile usage](strategies/macro-router.md#gui-and-cli-profile) includes a
synthetic runnable example. G9a adds accepted offline connection prerequisite inspection;
G9b executable private reads remain deferred until approved setup and response
contracts are defined. Latest verification results are in the task log (G9a:
1535 Python / 51 JS / 15 CTest, plus actual Connections/benchmark browser smoke).
The
[forms contract](gui-research-forms.md) separates backend and browser gates.
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

## Personal terminal workstream — agreed goals, 2026-11-15

Status: planned, not implemented by this entry. The user endorses the following
goals and multiple-source validation. Wait for Schwab developer approval rather
than buying data now. No new subscription, package installation, account access,
or trading authorization is implied. Reuse existing research and GUI components;
these milestones do not supersede outstanding accounting or execution gates.

| Sequence | Small deliverable | Acceptance |
|---|---|---|
| T1a | One instrument-centered workspace using captured data | Linked candles, regime reasons, expected moves and existing research retain the same instrument/time selection; explicit observed/model/delayed labels; sparse monochrome layout and square corners |
| T1b | Saved watchlists and workspace selections | Local persistence and restart recovery; unsupported instruments remain visibly unavailable |
| T2a | Versioned recording and replay contract using local captures | Preserve raw payload hashes, instrument identity, event/availability/receipt times where actually supplied, sequence and gaps; deterministic replay; never invent receipt times |
| T2b | Source comparison and quality view | Match equivalent observations; surface stale/missing/conflicting values with reasons; preserve originals and chosen-source provenance |
| T2c | Verified Schwab acquisition and recorder | After approval, verify official contracts, entitlements and retention terms; authentication, reconnect and gap handling; do not assume historical option tick coverage |
| T3a | Options contract and chain view | Explicit expiry, exercise/settlement convention, multiplier, bid/ask, sizes, age and coverage; no modeled value presented as a quote |
| T3b | Volatility and scenario comparison | Link existing gamma tools to contract selection; compare IV, realized volatility, skew and term structure with declared conventions; dealer positioning remains an assumption unless supported by qualified data |
| T4 | Explainable screeners and local alerts | Replay-test fixed predicates, regime vetoes, freshness checks and deduplication; display why an alert fired |
| T5 | Macro/event context | Timestamped releases and revisions, point-in-time joins and regime explanations; retrospectively assigned event labels cannot become historical inputs |
| T6 | Read-only portfolio/risk workspace | Positions, orders, Greeks and scenarios with mark age and reconciliation status; preserve one accounting authority and existing broker integration gates |
| T7 | Hypothesis and decision journal | Link hypothesis, available evidence, quoted prices, invalidation and outcome; retain rejected candidates and untouched evaluation periods |

### Monitoring priority — November 19, 2026

Daily evidence collection is now scheduled through the Codex app heartbeat
`qte-daily-regime-evidence` at 13:30 America/Los_Angeles. It is research-only:
capture completed-session data, preserve provenance and contemporaneous regime
labels, add explanations and propose separately evaluated improvements. It does
not enable continuous monitoring, trade execution or automatic parameter fitting.
First scheduled acquisition and availability of runtime connectors remain to be
observed. Details and task checkpoint are in `docs/gui-task-log.md`.

User wants assistant-supported monitoring. First deliverable combines T2c, T4
and T6 as a read-only shadow monitor; it does not authorize trading or changing
broker alerts. Initial proposed scope: SPX/SPY, QQQ and VIX, selected option
contracts, and the designated trading account. Verify feed coverage before
enabling each instrument. Futures data is now verified for research: Schwab `/ES` daily since 1983 and intraday is stored in the truth database (2026-11-24). Futures *trading* remains unavailable until a verified source
is connected.

- Continuously record available quotes and receipt times in a local QTE process;
  display stale data, disconnects and missing observations explicitly.
- Evaluate deterministic regime changes, expected-move boundaries, volume
  confirmation and hypothesis invalidation. Label proxy/model assumptions.
- Reconcile broker positions/open orders; expose concentration, marked PnL,
  option Greeks when available, quote age and unresolved account state.
- Produce deduplicated event summaries with reasons and a decision journal for
  assistant review. Unknown risk values must not display as zero.
- Keep collection and rule evaluation independent of the language model. This
  chat is not an always-running monitor; Robinhood alert events require polling
  and do not wake it. Local monitoring requires a running host/process and an
  authenticated data connection. No background service or schedule is active
  merely because this plan exists.

First implementation gate: verify Schwab contracts/authorization, then implement
quote recording plus shadow signals. Set concrete notification thresholds and
delivery destination before creating external alerts. Actual order execution is
a separate milestone with explicit authorization and execution controls.

Build T1a and T2a against captured data first. Keep secondary detail collapsed;
this is an opt-in market workspace, not a replacement for the Library landing
page. Revise the GUI layout contract before implementing new navigation.

### Multiple-source validation policy

- Add sources for complementary evidence and independently checkable coverage,
  not simply source count. Track upstream lineage: two vendors redistributing
  the same exchange feed are not independent confirmations of a trading edge.
- Maintain a source inventory recording access/retention terms, cost, coverage,
  resolution, latency, timezone, adjustment basis, revision policy and known gaps.
  Evaluate free/permitted sources first; document approval needs before acquisition.
- Compare only matching contracts, sessions, timestamps and field meanings.
  SPX, SPY and ES are distinct instruments; quotes, trades and model marks are
  distinct observations. Asynchronous prices need an explicit comparison window.
- Define tolerances by instrument and field before evaluating results. Preserve
  discrepancies; never silently average quotes, fill missing observations or
  overwrite raw captures. Record any fallback and its reason in the manifest.
- Separate source agreement, data quality and strategy evidence. Agreement does
  not establish profitability; keep chronological holdouts, all trial outcomes,
  execution costs and sensitivity analysis in research reports.
- Candidate evidence categories: broker/exchange prices and contract metadata,
  official macro releases with vintages, volatility measures, timestamped news,
  and qualified volume/order-flow observations. Unavailable positioning or flow
  must remain unknown or explicitly modeled rather than inferred as fact.

The existing Roadmap entry in the GUI Library exposes this plan. The next coding
task should specify T1a's exact instrument/time navigation and captured-data
inputs before changing interfaces.

## November 17 priority update and loose-end register

User direction: prioritize usable research functionality and viable strategy
discovery over GUI polish or repeated browser acceptance. Existing tests remain;
run focused checks when calculations, accounting, availability or execution change.
Do not repeatedly rerun unchanged GUI suites or turn historical task-log entries
into new blockers. Preserve the pending items below without claiming completion.
This priority update puts research/data work ahead of T1 visual integration.

| Loose end / evidence | Status and next concrete action |
|---|---|
| Options studies are modeled, not observed option fills (`regime_study.py`, `reversal_tail.py`) | Research blocker: admit actual quotes with contract/session/condition provenance, then measure executable-price sensitivity; no validated profitable strategy yet |
| Theta sample: one day, 3 / 19 identified contracts, malformed rows, naive timestamps and an ordering inversion (`build/tick-data-evaluation/theta/audit.json`) | Raw preserved, replay admission pending. Resolve source timezone semantics and quarantine invalid records without inventing receive time; sample tests mechanics, not regime viability |
| Minute coverage: 40 selected sessions; last-hour tail days selected retrospectively | Obtain fixed-calendar coverage when access permits; do not relabel this inspected sample or tail winners as untouched validation |
| Scenario cohorts differ (`run_daily`, `run_intraday`) | November 17 diagnostics now expose unmatched counts and matched-trade costs; eligibility/capital-path differences still require a separately funded replay before portfolio comparisons |
| Walk-forward is aggregation over precomputed family sleeves (`walk_forward`) | Not a shared-account executable walk-forward. Replay chosen policies with one cash/risk state and fresh evaluation windows before account-return claims |
| Futures D2b.2b and D3–D5 (ADR 0013) | The ledger supports fill-time fee and margin checks, and `FuturesOrderLifecycle` owns pending orders, submission codes and fill/cancel transitions. Causal settlement and quote sources, expiry/roll and engine wiring remain |
| American options and index-option semantics | Python pricing/research does not complete assignment/exercise and portfolio execution. Preserve explicit European SPX/SPXW versus American ETF-option distinction |
| Schwab and live/paper integration (`brokers/readonly.py`, M17–M21) | OAuth and selected read-only GET routes verified. Recorded option-stream deltas have an offline replay path. Live stream entitlement, transport and durable accounting reconciliation remain open. No purchase or live trading implied |
| Windows W2/W3 (`windows-gui.md`) | Artifact/storage and process-supervision parity plus native acceptance remain; parked while research takes priority, not advertised as complete |
| Session integration R2c and corporate actions R2 | Single-run CLI/GUI ingestion, validation and candle export support declared sessions. Multi-window comparisons and corporate-action accounting remain open |
| Macro/news/dealer evidence | Timestamped release vintages, news availability and qualified positioning absent. VIX is a daily-move proxy; volume-based price features are not measured dealer flow |
| Recovery and documentation | Many uncommitted/untracked research files and ignored local datasets remain. Preserve all; user owns commits/backups. README and older log checkpoints are historical/incomplete capability summaries; this register and latest entries take precedence |

### Functional research triage delivered November 17

Run in the repository venv, with a new output directory each time:

```sh
.venv/bin/python -m qte.research_triage --study build/regime-study --output build/my-research-triage
```

Reports every family/vehicle, matching trade identity across scenarios, missing
cohorts, best-session concentration, and year/regime PnL. JSON preserves all
chronological slices; Markdown summarizes the already-inspected 2021+ slice.
Raw input and output hashes plus code identity are recorded. No parameter search,
new backtest, real quote calibration or fresh holdout is implied.

The November 17 run found all ten minute family/vehicle combinations negative
under both modeled base and wider costs. Daily SMA300-reclaim long options were
positive in 2021+ (11 trades; wide-cost PnL $8,135; $2,900 excluding the best day),
but negative in both earlier slices (wide costs: -$16,950 before 2016 and -$10,095
in 2016–2020). This is a hypothesis to investigate, not a viable strategy finding.
The existing walk-forward diagnostic sums to +$12,150 base but -$166,650 at wider
costs; sums are modeled trade PnL, not risk-matched account returns.

Conditional underlying forward outcomes at fixed 1/5/15/30-minute horizons are
now implemented in `qte.forward_report`, with prior-session regime/time-matched
controls and session-level sample counts. The first run keeps the four existing
reversal variants frozen; detailed rules/results are in
[the reversal study](strategies/reversal-tail-study.md#fixed-horizon-underlying-study--november-17-2026).
Counts remain too small to identify a viable strategy. Next: representative
session coverage and qualified recorded quotes for option ask-entry/bid-exit
diagnostics, retaining all losing cases. Historical source/time validation and
quote admission remain explicit gates; no paid signup is requested.

### Common volume strategies — implemented November 17

Five configurable long-only families now use the existing engine and shared
candidate lifecycle: relative-volume breakout, VWMA trend, OBV confirmation,
Chaikin Money Flow trend and MFI reversal. Registered for independent Research
runs and declarative comparisons; the synthetic lab/macro router fixed slate is
unchanged. Own-instrument OHLCV only, explicit observed-bar lookbacks and
next-open execution. [Rules and runnable examples](strategies/volume-strategies.md)
are in the GUI Library. November 19 frozen screening completed 264 daily/hourly
and 400 minute-session QTE runs with paired controls and cost stress. Daily SPY
OBV, QQQ VWMA and QQQ OBV pass the original screening gates, but incremental
bootstrap intervals include zero and recent PnL trails the 10% buy/hold baseline.
All five minute variants lose under both cost settings. Daily/hourly inputs are
constructed adjusted-price proxies, not validated executable ETF histories.
Independent action/quote validation and later uninspected sessions remain the
next gate; no live candidate or calibrated dealer-flow interpretation established.
