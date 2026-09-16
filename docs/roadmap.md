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

## Next scoped task

Choose one small follow-on milestone before implementation. Candidates are a
declared sampling/resampling layer for production analytics, an authenticated
Alpaca download tool outside the core, walk-forward/robustness reports for the
reference strategy, or paper-trading interfaces. Live credentials, network
access, and new dependencies require separate approval.
