# Active GUI implementation checkpoint

## November 21 — trailing-year two-provider evidence

Compiled 365 dates and 251 NYSE sessions, Nov 21, 2025–Nov 20, 2026. Robinhood
covered all SPX daily and 30-minute sessions, 250 VIX daily sessions, and 205
ETF 30-minute sessions. Schwab and Robinhood daily SPY/QQQ/IWM covered all 251.
Schwab covered 181 ETF half-hour sessions; older-only probes did not fill the
earliest 46 intraday ETF sessions. Six SPX 2-sigma dates, four with usable
targeted minute captures. Causal prior labels and retrospective outcomes
preserved separately. Raw captures, hashes, CSV, JSON and report:
`build/daily-regimes/year-20261120/`. No refits or trades.

## November 21 — completed November 20 regime evidence

Robinhood supplied daily SPX/VIX and SPY/QQQ/IWM with weekday context through
November 20, plus 390 observed regular-session minutes each for SPX/SPY/QQQ.
SPX fell 3.70% on the provider daily bar; the minute low reached −2.28 prior
expected moves. VIX rose 26.20% to 35.89. The prior-known high-volatility
downtrend label was preserved. A completed-session tail event is retrospective.
No verified catalyst, futures or options history obtained. Schwab supplied only
the separate same-clock ETF volume cross-check from the previously saved panel.
See `build/daily-regimes/2026-11-20/hood-20261121/summary.json` and hashed raw
responses. No regime rules were refit.

## November 21 — five-agent options master

Added the Options master experiment to Five-agent research, plus the offline CLI
`python -m qte.options_master`. Strategy roles may nominate registered rules
from development results. QTE uses fixed costs, exposure and failure criteria
for up to three revisions and freezes before replaying the selected rule only
on the later sessions. Validated that changing later quotes cannot change the
frozen master. The failure table attributes same-fill midpoint versus executable
price and fee drag and preserves losing sessions, regime labels and misses.

Final run `build/options-master-20261121-final/report.html` and adjacent JSON
files contain the complete audit. Synthetic 1.5-sigma reentry with news/shock
veto selected. Stressed evaluation earned $654.73 on 16 trades in eight
sessions and failed sample size and uncertainty gates. Operational policy cash.
The only local vendor quote sample spans one session. The GUI model path was
verified using a test provider. See `docs/five-agent-research.md` for the
workflow, replay assumptions, capture command and evidence limitations.

## November 20 — fixed historical profitability screen

Completed `examples/book151_historical.py`: 162-run comparison of four Book151
families on cached SPY/QQQ/IWM adjusted daily proxies, three chronological windows,
base/stress costs, ungated versus prior-session regime veto, plus buy-and-hold.
Protocol written before outcomes, all results retained, independent fill cash and
closed-trade reconciliation on every run. New data-contract/gate/next-open tests.
Final artifacts: `build/book151-historical-20261120-final/report.html`, summary,
protocol, fills/trades/equity and hash manifest including loaded C++ extension.

No candidate passed the full screen. IWM channel reversal was positive in all
three windows but only 15 recent trades; all candidates trailed recent 10% buy/hold.
Universal bearish-regime veto reduced recent stressed PnL in 10/12 pairs. See
`docs/strategies/book151-historical-results.md` for all assumptions, selected
counterexamples and next bounded research decision. No option/futures/live claim.

Commands: `.venv/bin/python examples/book151_historical.py --output
build/book151-historical-20261120` failed before outcomes on naive/aware request
timestamps; fixed explicit UTC interpretation. `--output
build/book151-historical-20261120-v2` completed. After explicit adjustment/session
metadata validation and binary provenance, `--output
build/book151-historical-20261120-final` produced final artifacts. Initial output
folders preserved. No parameter changes or outcome-driven retuning.

Validation: `.venv/bin/python -m pytest -q tests/python/test_book151.py
tests/python/test_book151_historical.py tests/python/test_regime_study.py
--basetemp=build/test-book151-historical-final`: 36 passed.
`.venv/bin/cmake --build build/local-dev -j2`: succeeded (existing Ninja truncated
log recovery warning); `.venv/bin/ctest --test-dir build/local-dev
--output-on-failure`: 19/19 passed. No installs, broker writes or Git operations.

## November 19–20 — SPX/futures book strategies and daily evidence schedule

User selected ETFs/options first, then emphasized SPX and futures. Reviewed the
local SSRN PDF `151tradingstrategies.pdf`, extracted text to
`build/strategy-151/source.txt` and visually checked futures momentum formulas.
Cataloged 173 source TOC strategy headings including variants, with hash and
implementation/deferred statuses in `docs/strategies/book151-catalog.json`.
Publisher marks the published book retracted; reason not established; no claim
that publication validates profitability or every supplied formula is defective.

Implemented:
- `strategies/book151.py`: four cash-equity/ETF strategy adapters, shared existing
  lifecycle/accounting; pure causal futures momentum/contrarian target weights.
  Registered research/comparison configurations and form metadata. Explicit
  session grids handle calendar gaps; no futures-as-shares execution.
- `option_structures.py`: ten SPX-style European cash-settled option recipes,
  exact piecewise expiry payoff/risk, aggregate existing Black-76 gamma surfaces.
  Model scenarios, not executable quote backtests or American assignment.
- `examples/book151_research.py`: one reproducible command generating actual
  QTE equity runs, fills/equity/v2 manifests, option payoff charts/gamma grids and
  futures weight examples. Ran `--output build/book151-demo-20261119` successfully.
  Four synthetic runs generated 46/32/28/71 fills. No market profitability claim.
- Library guide `docs/strategies/book151.md` records exact rules, adaptations,
  execution/sizing assumptions, missing data and remaining derivative gates.

Validation: focused strategies/configuration/volume/Robinhood tests: 128 passed
before adding one explicit calendar-grid regression. Earlier handoff/broker/paper
suite: 179 passed. Native `.venv/bin/cmake --build build/local-dev -j2` succeeded;
`.venv/bin/ctest --test-dir build/local-dev --output-on-failure`: 19/19 passed.
Two old form registry expectations omitted the previously added volume families
as well as the new families. Updated strict expected registries/defaults and
expanded comparison round trips; 16 checks passed and remaining metadata check
passed after fixing test-local defaults reuse. A broader form test run stalled
in endpoint integration and was interrupted (exit 130); no full GUI acceptance
claim. Initial new test fixture errors used wrong BacktestConfig keyword and CSV
timestamp format; corrected fixtures to existing contracts, retained assertions.
No dependency installation, Git operations or trades.

Created app heartbeat `qte-daily-regime-evidence`, ACTIVE, daily 13:30 local
America/Los_Angeles (after normal US cash close), then viewed its card. It records
completed-session SPX/VIX/ETF observations, available intraday data, sigma/volume/
drawdown evidence and timestamped catalysts; skips duplicate closed sessions,
records gaps honestly, preserves original regime vintages and proposes rather
than silently refits thresholds. Futures/option coverage only with verified
sources. No trade calls. User's extreme-selloff description is a hypothesis to
verify against observations. Schedule creation does not mean a daily collection
has already completed; first scheduled run and runtime availability unverified.

## November 19 — Robinhood MCP handoff connected

Added `python/qte/brokers/robinhood_handoff.py` and focused tests. Fresh real
`get_accounts` and `get_trade_approval_setting` reads bound the designated MCP
account to `hood-mcp` in ignored `build/broker-drafts.sqlite`; no raw account
number stored in the binding table, no tokens stored. Approval observed enabled.
Use `connection hood-mcp` for the historical checkpoint; fresh reads required
before review/placement. Assistant host executes returned tools, not Python.
Review/placement receipts are durable, exact terms/account/review age are bound,
submission uncertainty is committed before handoff, and retries are blocked.
No broker review, placement, cancel or fill was executed. Fill accounting and
autonomous execution remain unavailable. Full workflow in broker staging guide.

`.venv/bin/python -m pytest -q tests/python/test_robinhood_handoff.py tests/python/test_broker_staging.py tests/python/test_paper.py tests/python/test_brokers.py --basetemp=build/test-robinhood-handoff` — 179 passed.
Real connect command: `.venv/bin/python -m qte.brokers.robinhood_handoff connect hood-mcp`,
fresh structured observations via stdin; returned masked connection checkpoint.

User additionally requested the local `151tradingstrategies.pdf`; prioritized
ETFs/options then SPX/futures. PDF found and extracted to ignored
`build/strategy-151/source.txt`. Research/implementation continues below.

## November 19 — Dual-broker execution setup: offline staging

Added `qte.brokers.staging`: explicit broker/account alias, whole-share long
equity or single-leg option limit/day/regular-session drafts, decimal tick
validation and SQLite persistence. Reusing an ID with changed terms fails;
restart preserves the draft and fingerprint. Robinhood MCP review arguments and
placement ref_id are separate; Schwab produces a candidate payload pending
official contract verification. No submit command, transport, account binding,
strategy routing or paper-coordinator changes. No real order or broker preview
was sent. No package installed. See `docs/broker-order-staging.md` and two
`examples/broker-draft-*.json` dummy tickets.

Validation:
- `.venv/bin/python -m pytest -q tests/python/test_broker_staging.py tests/python/test_brokers.py tests/python/test_paper.py tests/python/test_schwab_auth.py -k 'not real_loopback_tls' --basetemp=build/test-broker-staging-final` — 194 passed; unchanged socket-based TLS test deliberately deselected, not revalidated in this milestone.
- `cmake --build build/local-dev -j2` initially unavailable on PATH; discovered existing repository-local executable without installing anything.
- `.venv/bin/cmake --build build/local-dev -j2` — passed (39 targets; Ninja recovered a pre-existing incomplete log).
- `.venv/bin/ctest --test-dir build/local-dev --output-on-failure` — 19/19 passed.
- `.venv/bin/python -m qte.brokers.staging --journal build/broker-staging-smoke/drafts.sqlite stage examples/broker-draft-schwab.json --client-id qte-11111111111111111111111111111111` — passed, offline only.
- `.venv/bin/python -m qte.brokers.staging --journal build/broker-staging-smoke/drafts.sqlite stage examples/broker-draft-robinhood.json --client-id qte-22222222222222222222222222222222` — passed, offline only.

Next: authenticated account mapping/reconciliation and official Schwab trading
contract; fresh quote/risk/review binding; then durable live submission/cancel
and accounting recovery. User authorized execution setup, not a particular trade.

## November 19 — Read-only monitoring priority captured

User requested assistant-supported monitoring. Added concrete scope and runtime
boundaries to the terminal workstream in `docs/roadmap.md`: verified quote
recording, regime/expected-move alerts, account exposure and a decision journal.
Existing Robinhood tools do not push alerts to this chat; standalone QTE does
not inherit MCP tools. No service, schedule, external alert or order started.
Documentation only; no code or dependency changes and no test rerun.

## November 19 — Execution speed and broker routing research

Recorded sourced findings and a staged measurement plan in
`docs/execution-feasibility.md`. Both brokers accept PFOF generally; Robinhood's
latest Q3 report excludes index-option orders. Schwab's advertised 50 ms measures
stock routing-to-execution, not QTE-to-options-fill time. Direct Schwab integration
is a candidate architecture, not measured superiority. No orders, settings,
dependencies or production code changed. Documentation-only work; no test rerun.
Next: authenticated quote/stream timing and shadow decisions after official
contracts are available; actual fill comparisons require live-order authorization.

## November 19 — Frozen volume profitability screen completed

Executed 264 QTE daily/hourly runs plus 400 independent minute-session runs.
Original frozen protocol retained at `build/volume-profitability-20261117`;
offline runners `run_study.py`, `run_intraday.py`, `summarize.py` live there.
Results/fills/trades/event equity/manifests:
`build/volume-profitability-results-20261119` and
`build/volume-intraday-results-20261119`. Report in the former directory.

Daily proxy screen passed SPY OBV, QQQ VWMA and QQQ OBV under original four gates.
Recent stressed PnL: +$2,203.55 / +$3,123.89 / +$2,229.19 on $100k starting cash,
10% sizing, Jan 2024–Nov 16 2026. All trail 10%-allocated buy/hold. Paired 20-day
block-bootstrap 95% intervals include zero for all three; no incremental edge
established. Daily regimes are entry attribution only, not fitted entry filters.
All five minute variants lost at both cost settings over 20 selected sessions.
QQQ hourly breakout positive but only 13 trades, no earlier validation coverage.

Daily/hourly are constructed all-adjusted price proxies, not raw ETF shares or
audited total returns. Hourly mapping independently verifies a common additive
daily adjustment across OHLC; partial requested-range spillover excluded.
Intraday uses reported split-adjusted SPY prices, no overnight exposure, capital
reset independently each session. No options profitability or live promotion.

Commands/results:
- `.venv/bin/python build/volume-profitability-20261117/run_study.py --output build/volume-profitability-results-20261119` — 264 runs, all financial invariants passed.
- `.venv/bin/python build/volume-profitability-20261117/run_intraday.py --output build/volume-intraday-results-20261119` — 400 runs, all session-terminal positions flat.
- `.venv/bin/python build/volume-profitability-20261117/summarize.py` — all 15 daily candidate diagnostics; seed 20261117, 2000 circular 20-trading-day blocks, unadjusted descriptive intervals, no multiple-testing significance claim.
- `.venv/bin/python -m pytest -q tests/python/test_volume_strategies.py --basetemp=build/test-volume-profitability-20261119` — 25 passed.
- Independent `.venv/bin/python` ledger audit recomputed cash/quantity from every fill in all 664 runs; reconciled final PnL and verified output hashes. Compiled engine binary identity added to manifests.
No production code changed in this research step; native build/19 C++ tests
already passed earlier this session. No additional GUI tests, installs or trades.

Next: independently validate corporate actions/feed/quotes and collect later
uninspected sessions for the three daily candidates. Do not tune failed minute
strategies on the same data or infer option profitability from equity results.
Regime full-session capture and Schwab user authorization still remain pending.

## November 19 — Robinhood capability exploration; profitability study resumed

87 advertised MCP tools cataloged, 22 distinct reads/unsaved-preview tools
exercised; public samples at `build/robinhood-capabilities-20261119` and summary in
`docs/robinhood-mcp-capabilities.md`. Private account responses not persisted.
Confirmed Level 2 snapshots, provider indicator calculations, scanner ATM-IV
columns, and observed 15-second history on two sampled SPXW contracts. No writes,
trading, approval requests, upgrades or private banking/credit reads. No production
code changed or extra GUI tests run. User redirected work to profitable strategy
research; resume the frozen volume study rather than tune today's inspected move.

## November 19 — Schwab app ready; local OAuth command implemented

User created the Schwab app with `https://127.0.0.1:8182` and confirmed Ready for
Use. Added `python/qte/brokers/schwab_auth.py`: hidden app-key/secret prompts,
temporary loopback HTTPS callback, random state validation, one-use callback,
pinned OAuth code/refresh POST, bounded/redacted transport errors and explicit
refresh. Repository-local tokens use POSIX 0700/0600 permissions, atomic writes,
symlink rejection and a command lock; app secret is not persisted. Windows token
ACL support remains pending and fails explicitly. Existing GUI unchanged.

Commands: `.venv/bin/python -m qte.brokers.schwab_auth login`, `status`, `refresh`,
and contract-verified market-data-only `read`. Actual authorization must be run
by the user in their terminal; no real credentials/provider request used here.
The official Market Data contract export remains required for GET operations.
No new dependency, order capability, account read or background process added.
Details and sources: `docs/broker-connections.md`.

Validation:
- `.venv/bin/python -m pytest -q tests/python/test_schwab_auth.py tests/python/test_brokers.py --basetemp=build/test-schwab-auth` — initially 142 passed, one real-loopback test blocked by sandbox socket permissions.
- `.venv/bin/python -m pytest -q tests/python/test_schwab_auth.py -k real_loopback_tls --basetemp=build/test-schwab-loopback` — passed with scoped elevated loopback access; fake credentials, local certificate trusted explicitly, no external requests. The test uses `closing()` for HTTPConnection lifetime.
- `.venv/bin/python -m pytest -q tests/python/test_schwab_auth.py tests/python/test_brokers.py -k 'not real_loopback_tls' --basetemp=build/test-schwab-auth-final` — 144 passed, already-verified loopback test deselected. Total 145 distinct checks passed.
- Both module and `read --help` commands passed; `git diff --check` passed.
- `.venv/bin/cmake --build build/local-dev -j 2` — passed (existing premature Ninja log recovered; no native code edits).
- `.venv/bin/ctest --test-dir build/local-dev --output-on-failure` — 19/19 passed.

Pending research remains as recorded below: complete-session regime capture and
volume-strategy profitability validation. OAuth work does not establish a new
data entitlement, historical option coverage, or profitable strategy.

## November 19 — Selloff observations captured; Schwab portal account ready

Read-only collection: `build/regime-observation-20261119-1725`. Three minute
index series, ten equity series, daily warmup and 23 earlier five-minute ETF
sessions (20 used for same-clock volume). Coverage ends 09:23 Pacific, not the
requested 09:25 cutoff. Raw responses, retrieval times, hashes, normalized
research rows, CSV/JSON feature timeline and catalyst journal retained; see README.
Excluded unfinished/interpolated bars; anomalous QQQ high flagged, not repaired.
Existing `trend_down` label and stress latch unchanged despite nearly 3% drawdown
from the running high. This is an inspected case, not fresh holdout evidence.

Next: separate completed-session capture, consecutive comparison days, and
evaluation of path/volume/VIX features before modifying regime rules. No background
collector installed. Volume profitability validation remains pending; frozen
protocol/captures are in `build/volume-profitability-20261117`.

User confirms Schwab **portal account only**. Product/app approval, verified
contract and local OAuth integration remain outstanding. Existing GET-only client
does not implement OAuth or GUI authorization. No private reads or trading.
No production code changes, dependency installations or Git history operations.

Verification (once, no GUI suite):
- `.venv/bin/python build/regime-observation-20261119-1725/analyze.py` — completed;
  172 aligned timeline rows, explicit availability and prior-session baselines.
- Inline `.venv/bin/python` audit — verified five raw hashes, seven derived
  hashes, ordered timestamps and completed-bar receipt cutoffs.
- `.venv/bin/python -m pytest -q tests/python/test_regime_study.py tests/python/test_regimes.py tests/python/test_reversal_tail.py --basetemp=build/test-regime-observation-20261119` — 72 passed.
- `.venv/bin/cmake --build build/local-dev -j 2` — passed; Ninja recovered its
  existing premature log and rebuilt 39 targets without native source edits.
- `.venv/bin/ctest --test-dir build/local-dev --output-on-failure` — 19/19 passed.
- `git diff --check` — passed.

## November 17 — Five common volume strategies implemented

Added `VolumeConfig`/`VolumeStrategy`: volume breakout, VWMA trend, OBV
confirmation, CMF trend and MFI reversal. Extracted the existing private
long-only order lifecycle for reuse; CandidateStrategy's public constructor,
signals and accounting/execution ownership remain unchanged. New strategies
registered in research/config comparison factories and form metadata. No browser
redesign. Rules, formulas and sources in `docs/strategies/volume-strategies.md`,
registered in Library. Five JSON configurations share a 180-bar explicitly
synthetic hourly fixture; no market-calendar or profitability claim.

Engine demos at `build/volume-strategies-20261117` produced 2/11/7/7/4 fills for
the five families respectively, with orders, equity, metrics, provenance and
version-2 candle artifacts. Open positions/unfilled final orders remain explicit.
Own-symbol volume is required; SPY/ES volume is not silently substituted for SPX.
Fixed lab/router slate remains unchanged. Next research: representative observed
ETF-volume windows, price-only baselines, cost stress and causal regime splits.

Focused checks initially caught a real VWMA numerical defect: normalized
weight rounding made equal-price fast/slow windows differ, causing a false
entry. Centered accumulation fixes it; regression checks equal-price neutrality.
Other initial failures were incorrect new test keys for the existing
`research_run` form schema, corrected without loosening assertions.

Commands/results:
- `.venv/bin/python -m pytest -q tests/python/test_candidates.py tests/python/test_candidate_cli.py tests/python/test_router.py` — 117 passed; shared lifecycle compatibility.
- `.venv/bin/python -m pytest -q tests/python/test_volume_strategies.py` — initially 7 failures diagnosed above; then 25 passed.
- `.venv/bin/python -m pytest -q tests/python/test_volume_strategies.py tests/python/test_comparison_config.py --basetemp=build/test-volume-final` — 53 passed after comparison registry integration. Combined 170 distinct focused cases, not a full-suite rerun.
- `.venv/bin/python` inline demo runner called `qte.cli.run_research(..., export_version=2)` for all five `examples/*-run.json` volume configurations into `build/volume-strategies-20261117/<family>`; all completed.
- `.venv/bin/python -m qte run --config examples/volume-breakout-run.json --output build/volume-breakout-cli-20261117 --export-version 2` — CLI reproduction completed.
- `.venv/bin/cmake --build build/local-dev -j 2` — successful; recovered existing premature Ninja log. `.venv/bin/ctest --test-dir build/local-dev --output-on-failure` — 19/19 passed in 0.19 seconds.
- `git diff --check` — passed. No new dependencies, purchases, broker actions or Git history changes. No browser/layout test suites run; only config-to-strategy plumbing checked.

## November 17 — Frozen reversal forward outcomes implemented

Added `qte.forward_outcomes` and the offline `qte.forward_report` command. Reuses
existing reversal rules and stress latch; no C++/GUI changes, price calibration,
account actions or network acquisition. Four variants, four fixed horizons,
next-completed-close anchor and explicit missing/cutoff statuses. Signal roster
does not depend on subsequent data availability. Controls use only earlier
captured sessions, matching regime/30-minute ET bucket, with equal session
weights and a minimum of three prior sessions. Full per-event/control summaries
and input/code/output hashes retained. Regression coverage targets causality,
missing paths, anchor timing, control matching and session weighting.

Actual run: `build/forward-outcomes-20261117`, 40 usable sessions / 15,600 bars.
Guarded -1 sigma: four events on one day; insufficient earlier controls.
Guarded -2 sigma: two events on two days; 1/5/15/30-minute session means
+2.45/+0.59/-28.58/-39.44 bp. Small rebound does not persist at longer horizons;
no viable strategy or optimal horizon established. Event-selected samples remain
previously inspected. Rules and reproduction command are in the registered
`docs/strategies/reversal-tail-study.md` Library document.

Next: qualify captured quote timestamps/conditions and obtain representative
session coverage when access permits; do not rescue inspected results by tuning
thresholds. Schwab approval is still pending. No GUI test/polish task added.

Commands/results (repository root):
- `.venv/bin/python -m pytest -q tests/python/test_forward_outcomes.py tests/python/test_reversal_tail.py tests/python/test_regime_study.py` — 19 passed.
- `.venv/bin/python -m pytest -q tests/python/test_forward_outcomes.py -k unmatched_regime_or_time` — two additional matching regressions passed; four existing cases deselected. Total 21 distinct focused Python cases passed across these calls.
- `.venv/bin/python -m qte.forward_report --captures build/robinhood-regimes-20261115 --output build/forward-outcomes-20261117` — completed, 40 usable sessions; four output hashes verified against the manifest.
- `.venv/bin/cmake --build build/local-dev -j 2` — passed, recovering the existing premature Ninja log; no native source edits.
- `.venv/bin/ctest --test-dir build/local-dev --output-on-failure` — 19/19 passed in 0.22 seconds.
- `git diff --check` — passed. No GUI/browser suites or dependency installations. Plotting library is absent in the venv; table/JSON reports require no additional packages.

## November 17 — Loose ends recorded; functional research triage

User prioritizes functionality and strategy research over repeated GUI testing.
Added a current loose-end register to `docs/roadmap.md`: derivative execution,
quote admission, sampled/inspected historical coverage, scenario cohort mismatch,
walk-forward accounting, broker access/reconciliation, Windows parity, sessions,
macro vintages and local artifact durability. Old task-log checkpoints are not
new work requests. Paused the obsolete immediate data-signup recommendation.

Implemented `qte.research_triage`: separate saved-report analysis, no engine or
GUI edits. Matches recorded intents across cost/IV scenarios, rejects duplicate
or incompatible identities, retains all candidates, and reports concentration,
year/regime breakdown and input/output/code hashes. Empty matched cohorts are
unavailable, not profitable flat strategies. Matching is retrospective and not
a replacement for actual portfolio replay.

Existing minute variants all lose at modeled base/wide costs. SMA300-reclaim long
options have 11 positive-aggregate recent observations but lose in both earlier
slices; no strategy promoted. Full results in `build/research-triage-20261117`
and the final report directory noted below. Next functional work: fixed-horizon
conditional outcomes with matched regime/time baselines, then qualified quote
replay. Do not tune thresholds to rescue these inspected historical results.

Commands/results from repository root:
- `.venv/bin/python -m pytest -q tests/python/test_research_triage.py tests/python/test_regime_study.py` — 11 passed (identity matching, cost cohorts, concentration and existing financial logic).
- `.venv/bin/python -m qte.research_triage --study build/regime-study --output build/research-triage-20261117` — completed; initial report retained.
- Same command with `--output build/research-triage-20261117-final` — final report after displaying empty cohorts as unavailable; this is the deliverable directory.
- `.venv/bin/cmake --build build/local-dev -j 2` — passed; Ninja recovered a prematurely ended prior log. No C++ source changes.
- `.venv/bin/ctest --test-dir build/local-dev --output-on-failure` — 19/19 passed in 0.20 seconds.
- `git diff --check` — passed. No GUI/browser suites, dependencies, network requests, account actions or Git history operations.

## November 15 — Personal terminal goals and source validation

Recorded user-endorsed terminal goals as T1a–T7 in `docs/roadmap.md`, including
instrument workspace, recorder/replay, source comparison, options analysis,
alerts, macro context, risk and journal. Multiple-source validation retains
upstream lineage, timestamp/contract comparability, disagreements and provenance;
source agreement is separate from strategy evidence. Wait for Schwab approval
rather than purchasing data. Next proposed implementation: T1a then T2a against
existing captures. This supersedes the earlier signup recommendation below as
the immediate next action. Documentation only; no runtime or dependencies changed.

## November 15 — Real tick-data acquisition (no paid signup yet)

Downloaded selected public ThetaData sample members for 2025-08-19 using
bounded HTTP ranges from the 3.77 GB official ZIP. About 32 MB compressed,
331.7 MB extracted, six files under `build/tick-data-evaluation/theta`:
22,344 SPX index reports; 1,018,718 SPXW trades; 715,116 same-day-expiry quote
rows and 1,878,395 seven-day-expiry quote rows, plus EOD context. Quote sample
covers only 3 / 19 fully identified contracts, not complete chains.

ZIP CRC and six SHA-256 hashes pass. Structural audit found one malformed final
row in each quote CSV, one per-contract timestamp inversion in the 0DTE sample,
and 933 unusable price/size rows in the 7DTE sample. Raw inputs preserved;
`audit.json` records all counts. Naive timestamps and no collector receive time
remain unresolved for replay. No backtest or executable-fill claim from samples.

Official vendor comparison and bounded pilot scope saved in
`docs/tick-data-acquisition.md`. Recommend free Databento signup for historical
usage-based pilot, currently $125 new-team credits; no paid plan/data request
approved. OPRA tick NBBO availability starts 2023-03-28 (earlier history only
minute BBO). Futures require GLBX.MDP3 explicit contracts; index feed separately
verified. ThetaData Options Standard alternative $80/mo, underlying entitlement
extra. No provider credentials available in inspected environment variables.

Commands: initial bounded curl stopped at archive size guard; HEAD and range
requests used approved network access. `.venv/bin/python
build/tick-data-evaluation/inspect_theta_archive.py`, `fetch_theta_sample.py`,
`audit_theta_sample.py` (same directory). Audit first failed on actual malformed
source rows; updated to count/quarantine malformed rows, then completed. No
package/runtime installed, core/GUI code changed, or Git history modified.
No engine tests rerun for this documentation/raw-data-only acquisition step.
Next: user signup, locally configured data key, concrete cost estimate, then
bounded historical purchase/download authorization if credits are insufficient.

## November 15 — Expected-move reversals, futures snapshots and put tails

Implemented `qte.reversal_tail` and offline `qte.reversal_tail_report`, with a
new panel under Research → SPX regimes. Documentation:
`docs/strategies/reversal-tail-study.md` (registered in Library). GUI adds
`reversal_tail.js`, fixed authenticated tail-summary/day/manifest resources,
trade markers, sigma/Bollinger overlays, causal stress shading, cost comparison,
put payoff paths and IV sensitivity. No C++ execution functionality changed.

Acquisition: 26 monthly 30-minute SPX captures, 20 selected full-minute days,
two expired-option probes and two delayed CME futures snapshots, saved under
`build/reversal-tail-captures`. Screen: 528 sessions, 2024-10-01–2026-11-13;
20 worst final hours selected retrospectively, 17 complete for minute analysis;
3,402 modeled put combinations retained. Nine days have base-model 10x peaks;
**zero verified actual 10x returns**. The two targeted contracts each have
59 interpolated and one observed bar, no observed entry/exit pair. CME MES/NQ
snapshots are delayed, static, not a connected futures feed/history.

Reversal universe remains the original 40 eligible sessions (event-selected
new days are not added to its performance). Guarded -1 sigma: four trades,
all on 2026-10-22, model call P&L -$40 base/-$640 wide. Guarded -2 sigma:
two trades, -$75/-$390. No-veto -2 sigma: six trades, -$3,895/-$4,830.
This does not establish positive expectancy. Extreme put multiples collapse
under higher assumed IV; do not present model premiums as mispricing evidence.

User clarified three years of options experience and wants data-driven edges,
not chart interpretation. Next priority: conditional forward distributions,
filter ablations, timestamped events and executable historical bid/ask; then
sizing/target-hit/drawdown distributions. $10k→$50k/month is a scenario to test,
not a promised result or optimization target for these samples.

Commands/verification:
- `.venv/bin/python -m qte.reversal_tail_report --captures build/robinhood-regimes-20261115 --tail-captures build/reversal-tail-captures --output build/reversal-tail-study`
  (own report regenerated via `generate(...)` after refinements).
- `.venv/bin/python -m pytest tests/python/test_reversal_tail.py tests/python/test_regime_study.py tests/python/test_gui_regime_study.py tests/python/test_gui_documents.py -q`
  — 27 passed; includes causal entry, missing-exit loss, gap handling, fixed
  strikes, losers, target execution delay, stress latch and screen filtering.
- `.venv/bin/cmake --build build/local-dev -j 1` — successful native build;
  Ninja recovered a prematurely ended log. `.venv/bin/ctest --test-dir
  build/local-dev --output-on-failure` — 19/19 passed after build completion.
- Bundled Node `--check` passed both research JS modules; `git diff --check`
  passed. Verified 26 output hashes and five code hashes in report manifest.
- Browser verified tables, tail selection and 2026-10-22 reversal entries/exits;
  screenshot `build/reversal-tail-study-preview.jpg`.

Local GUI command: `.venv/bin/python -u -m qte gui --repo-root
/home/glsu6/Documents/qte --port 8769`. Sandbox loopback bind initially denied;
approved normal-OS launch worked. A process ended during a user-turn change;
restarted and reverified. Run this command in a persistent user terminal if the
preview stops again. No packages installed, orders sent or Git history changed.

## November 15 — Robinhood history, regime GUI and option model study

Completed a reproducible offline research pass and **Research → SPX regimes**
(`/research/regimes`). Raw read-only MCP captures are in
`build/robinhood-regimes-20261115`; generated JSON/JSONL and hash manifest are
in `build/regime-study`. Daily SPX: 9,042 observed bars since 1990-11-09;
VIX: 6,159 since 2002-05-10; SPY: 6,833 since 1999-09-15. Minute SPX boundary
probes are empty through 2024-09-30 and return data on 2024-10-01. Experiment
captures April 2025 and latest 20 sessions: 40 eligible / 15,600 RTH bars.
Active and expired option samples remain entirely interpolated, including
April 2025. No observed option execution backtest is claimed.

New adapter, causal research module, exporter, authenticated fixed-file routes
and canvas GUI support regime starts/ends/reasons, daily/minute candles,
sigma/Bollinger/SMA300 overlays, trade markers, strategy/scenario comparisons
and prior-five-year walk-forward selection. Explicitly modeled European options
reuse QTE Black-76; the C++ execution engine is not used for hypothetical
contracts. Full rules, sources, result table and recommendations are in
`docs/strategies/spx-regime-study.md`, registered in the Library.

Main result: walk-forward base +$12,150, wide costs -$166,650, VIX repricing
+$41,620. Do not deploy. Investigate daily range and sparse SMA300 reclaim
events with executable quotes. Minute reversal loses after costs. Matched
volume coverage: EMA -$1,067.50 versus shifted EMA -$1,547.50, 95 trades each.
All candidates and losses are preserved. News annotation is context only;
price-driven stress latch guards intraday reversal entries. Interval end dates
are retrospective display boundaries and are never consumed by the strategy.

Validation: native GCC build and all 19 CTest targets passed. 49 distinct focused
Python tests passed across research/adapter, routes, static GUI, existing SPX
study, app integration and documents. The sandboxed app integration run stalled
in AnyIO/asyncio; stopped it, diagnosed the stack, and the same app/document
tests passed with normal OS primitives (17 tests, no weakened assertions).
Final research/route/document check passed 20 tests. JavaScript syntax and
`git diff --check` passed. Browser verified daily April chart, one-minute stress
trades and comparison controls. Preview saved at `build/regime-study-preview.jpg`.
Local preview started on 127.0.0.1:8769; session may need a new terminal login
if the process is stopped or authentication expires.

Commands (repository root, existing venv; no installs):

```bash
.venv/bin/python -m qte.regime_report --captures build/robinhood-regimes-20261115 --output build/regime-study
.venv/bin/cmake --build build/local-dev -j 1
.venv/bin/ctest --test-dir build/local-dev --output-on-failure
.venv/bin/python -m pytest -q tests/python/test_regime_study.py tests/python/test_gui_regime_study.py tests/python/test_spx_single_study.py tests/python/test_gui_static.py --basetemp=build/test-regime-study --tb=short
.venv/bin/python -m pytest -q tests/python/test_gui_app.py tests/python/test_gui_documents.py --basetemp=build/test-regime-gui-integration -o faulthandler_timeout=20 --tb=short
.venv/bin/python -m pytest -q tests/python/test_regime_study.py tests/python/test_gui_regime_study.py tests/python/test_gui_documents.py --basetemp=build/test-regime-docs-final --tb=short
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --check python/qte/gui/static/regime_study.js
.venv/bin/python -u -m qte gui --repo-root /home/glsu6/Documents/qte --port 8769
git diff --check
```

After incremental fixes, regenerated our generated report using
`qte.regime_report.generate(Path('build/robinhood-regimes-20261115'), Path('build/regime-study'))`.
CLI reproduction should use a fresh output directory. No trades, account reads,
installs or Git history operations. Existing uncommitted work preserved.

## November 15 — authenticated Robinhood SPX data check

Read-only MCP access now works. Captured SPX/SPXW chains, November 20
7600 call/put identifiers and quote snapshots (November 13 at 21:14:59 UTC),
plus November 13 regular-session history in ignored
`build/robinhood-spx-auth-20261115/capture.json`.
SPX history returned 78 five-minute bars with no interpolated flags set.
Each option returned 390 one-minute bars, **all explicitly interpolated**;
exclude these from intraday strategy validation. Snapshot IV/Greeks are
available, but cannot be backfilled into earlier signals. No historical
bid/ask coverage or decade archive has been established. Next research step:
establish non-interpolated option history and execution-price coverage before
replacing modeled option replay with observed data. No trading, account reads,
dependency installs, or application code changes. Capture JSON and counts
verified locally; no build/test rerun needed for this data-only check.

## November 14 — SPXW outright sizing on free SPX bars

Replayed outright calls and puts at 1, 2 and 5 contracts and with 2%, 8% and
16% cash budgets. FirstRate sample has 12 complete cash-SPX sessions and
4,680 one-minute bars. Prior Cboe VIX1D and VIX9D closes drive the band and
modeled Black-76 premiums. Three qualifying signals produced two calls and
one put. The 2% budget could buy none. See [rules and replay command](strategies/spxw-outright-research.md)
and `build/spx-public-data/spx-outright-firstrate-20261114.json`. The inputs
are cached locally under `build/spx-public-data`. The 2025 INX evaluation shard
query worked in Hugging Face Data Studio. Local ingestion awaits a Parquet
reader or CSV export and timestamp validation. Decade minute data remains open.
Verification passed 32 focused Python tests and all 19 C++ targets.

## November 14 — SPXW vertical-spread replay

Added provider-neutral paired execution for 5-, 10-, and 20-point SPXW debit
verticals and a CLI with session-wise replay, observation/signal counts,
year-by-regime breakout, stress costs, and unresolved exits. See
[SPXW debit spread research](strategies/spxw-debit-spread-research.md) for
contract semantics and the JSON Lines capture format. Readout shows modeled
five-minute exits and separate hypothetical expiry payoffs. Results are
synthetic. Ten-year historical results are pending quote, IV, and news
coverage. The public Cboe Legacy MDR sample was OEX, so it was excluded.
The synthetic report at `build/spx-spread-study-20261114.json` reproduced
byte for byte. The capture importer checks source hash and missing-IV
coverage. Verification passed 26 focused Python tests and 19 C++ targets.

## November 14 — American weekly reversal and local model provider

Implemented a fixed ten-rule five-minute replay using an opening same-day ATM
IV expected-move band and separate 5–9 DTE American options. Added a CRR gamma
scenario grid, news and excursion vetoes, bid/ask replay under two cost
assumptions, and supplied-data CLI input. The Five-agent Research view has both
the original straddle and the weekly reversal experiment. The new Ollama
provider uses a fixed loopback endpoint and can run without the OpenAI key.

Saved synthetic run `7d4f125dfc074c7f974d7c6f5dee0115` under
`build/qte-agents/runs/`. Read [the weekly research note](strategies/weekly-reversal-research.md)
for rules, results, papers and the capture contract. Browser verified the
saved strategy comparison and gamma grid on a separate port 8772 preview.
Tests: 85 focused Python, 20 research UI JavaScript, and 19 C++ targets passed.
GCC failed on the parallel build inside libstdc++/cc1plus. The existing native
build succeeded with `.venv/bin/cmake --build build/local-dev -j 1`.

Ollama runtime and Qwen3 4B download await explicit approval. The provider
contract was tested with a fake local response. Historical validation awaits
timestamped underlying, options NBBO and news capture.

## November 13 — five-agent options research

Added the optional role workflow at `/research/agents`, a fixed synthetic
option-quote replay, durable reports, and a prefilled Gamma Lab link. See
[Five-agent options research](five-agent-research.md) for setup, the frozen
rules, results, references, and data needed for chronological validation.
The first saved report uses Codex collaborator notes. The OpenAI API returned
`usage_limit_exceeded` before a live agent turn completed. The seven-day key
is in the approved ignored location. Local backtests are available without it.
Browser verified role defaults, the saved report, and Gamma Lab parameters.
Native build and 19 C++ targets passed. Focused Python tests: 69 passed.
Research UI JavaScript tests: 19 passed.

## November 11 — research candles and form guidance

Implemented in the separate research visualization task. New Candles & regimes
tabs align raw candles, factual fills, as-of regime context and recorded equity;
lab candidates compare on the same safe window and base/stress costs. New v2
single exports include validated canonical `market_data.json`; old exports
without OHLC explicitly remain unavailable. Form presets replace dirty drafts,
and units/defaults/bounds plus actionable validation messages are now visible.
See `docs/gui-usage.md` for expected values and usage.

Files: adapters `common.py`, `csv_bars.py`, `alpaca.py`; `cli.py`,
`market_export.py`, `research_export.py`, `strategy_lab.py`; GUI `app.py`,
`artifact_routes.py`, `catalog.py`, `export_v2.py`, `market_views.py`,
`run_views.py`, `research_forms.py`; static `app.js`, `app.css`, `experiments.js`,
`market_chart.js`, `research_forms.js`; corresponding market/form/export tests.
No C++ source edits, dependencies, provider calls or Git operations this task.

Browser verification found blanket mixed-file journaling blocked safe lab
charts. New registrations now avoid permanently tainting validated safe-window
files solely for residing in a mixed lab; raw downloads remain guarded. Old
protection records are preserved. A regression verifies a fresh safe chart works
and explicit inherited protection still blocks it. Test windows remain denied,
including after reveal. Candle tampering fails even after recomputing checksums.

Commands / results:

```sh
.venv/bin/cmake --build build/local-dev -j 2
.venv/bin/ctest --test-dir build/local-dev --output-on-failure
.venv/bin/python -m pytest -q tests/python/test_gui_market_export.py tests/python/test_gui_market_views.py tests/python/test_gui_catalog.py tests/python/test_gui_run_views.py tests/python/test_gui_research_forms.py tests/python/test_research_export.py tests/python/test_research_adapters.py tests/python/test_gui_static.py --basetemp=build/test-research-charts-delivery --tb=short
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node tests/javascript/market_chart.test.mjs
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node tests/javascript/research_ui.test.mjs
git diff --check
```

Build succeeded; 19/19 C++ targets, 109 Python tests and 22 JS tests passed.
Bare `cmake`/`ctest` were absent from PATH; repository-local executables worked.
Node form tests required scoped permission to spawn `.venv` Python; no install.
Ninja recovered an existing truncated log. The full unrelated suite was not run.
An isolated loopback preview on port 8769 verified the new chart controls and
raw candle rendering. Existing user servers were not restarted. Demo is
`build/chart-demo-20261111/visual-lab`, seed 7319, hourly regime-switch fixture;
catalog ID `68d3f5fca782402dbb098b0eaf54b03f`. Synthetic mechanics only.

Limits: 4096 candles/instrument; unavailable historical OHLC is not fabricated;
comparison choices reset on reload; old durable mixed-file protections can
require deliberate disclosure. No live data or strategy profitability claim.

Latest checkpoint, November 10: **D2b.2a futures submission admission implemented.**
The completed Gamma Lab remains unchanged. A NEW future order now gets a
read-only margin decision using authoritative instrument metadata, a fresh target
mark, the inclusive last-trade cutoff and candidate-plus-pending reservations.
Bounded nonreversing reductions can be admitted during a margin breach. Decisions
do not mutate orders or accounting. Full derivative replay remains gated.

- Changed: `include/qte/risk/futures_margin.hpp`, `src/risk/futures_margin.cpp`,
  `tests/cpp/test_futures_margin.cpp`; Portfolio's header/source and its C++ test
  gain a borrowed read-only instrument lookup; `examples/futures_accounting.cpp`
  gains an accept/reject/cancel/retry demonstration. This log, the roadmap and
  ADR 0013 record the contract and next step. Prior dirty user work is preserved.
- Sol implemented admission and regressions; Astra reviewed read-only with no
  unsafe-approval blocker. Missing-mark denial deliberately short-circuits later
  validation; this precedence is documented and tested. Root corrected a header
  declaration placement and two test fixtures whose first FillId violated the
  ledger's existing sequence contract. No test was removed or weakened.
- Native GCC build succeeded in `build/local-dev`; **19/19 CTest targets and
  47 focused Python tests passed**. The demo passed: accounting still ends at
  10195; its separate submission ledger stays at 10000 with zero fills.
  Ninja reported recovery of a truncated incremental build log and completed.
  The full Python/GUI suite was not repeated; no GUI code changed.
- **Next: D2b.2b**, fill-time margin revalidation, actual per-contract fees and
  pending lifecycle integration. Submission approval is not an execution promise
  or persistent reduce-only flag. D2a still needs causal event-source wiring;
  D3–D5 remain gated. No new approval is needed for the next offline code step.

Commands run from the repository root (build/CTest repeated after fixture repair):

```sh
/usr/bin/g++ --version
.venv/bin/python -c 'import sys; print(sys.executable)'
git status --short
PATH="$PWD/.venv/bin:$PATH" cmake --preset dev -DQTE_BUILD_EXAMPLES=ON
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev -j2
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
./build/local-dev/qte_futures_accounting_demo
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" CMAKE_BUILD_PARALLEL_LEVEL=2 .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest tests/python/test_bindings.py tests/python/test_lifecycle.py tests/python/test_research_config.py tests/python/test_research_baseline.py -q --maxfail=1 --tb=short --basetemp=build/test-futures-admission-20261110
git diff --check
```

Only the existing local QTE extension was rebuilt inside `.venv`, offline; no
new dependencies, downloads, broker connections, Git history operations, server
restarts or outside-repository writes. No stale `build/dev` use.

Previous checkpoint, November 9: **Gamma Lab now defaults to a rotatable 3D
surface**, using the same model grid, with a heatmap toggle and reset view.
Mouse/touch drag and arrow keys control the camera; log-day and linear-gamma
scales are explicit. Exact sample selectors and calculation diagnostics remain.
No pricing/backend/C++ changes or dependency installs. Astra's bounded read-only
review found no blocker; root checked the actual local browser. The running
8768 preview serves these static edits on refresh; no server restart required.
Prior model-only/live-feed and derivative-execution boundaries remain unchanged.
Browser checks confirmed drag and arrow-key rotation, reset, and heatmap/3D
switching. **38 focused tests passed**; unchanged C++/full Python suites were not
repeated. Exact command:

```sh
.venv/bin/python -m pytest tests/python/test_gamma_surface.py tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-gamma-3d-final-20261109
```

Previous checkpoint, November 9: **GL1 Gamma Lab implemented** at `/gamma`.
Manual-input Black-76 model surface (futures price × calendar time), selected
expiry slice, signed USD/point² exposure and analytic-versus-numerical check.
This is live recalculation, **not live market data**, an options-chain surface,
dealer positioning or execution. No derivative gate was removed. See
[Gamma Lab](gamma-lab.md) for formulas, units and source convention.

- Sol implemented the pure Python model/routes/tests; Terra drafted the UI
  before reaching a usage limit. Root completed responsive charts, integration,
  stale-result disposal, keyboard inspection and documentation. Astra's bounded
  backend and final UI reviews found no blockers. No new dependencies.
- Browser smoke on a separate loopback preview (`8768`) showed the surface/slice,
  IV edits updating gamma, analytic/numerical agreement, and invalid inputs
  clearing all old results. Preview used only synthetic/manual inputs. The
  existing `8765` process was not stopped; lazy loading keeps its older routes
  usable. Restart that process to make the new capability available there.
- Focused verification: **98 passed, 3 native-Windows checks skipped**; after the
  final UI update, **19 static regressions passed** (includes one new regression).
  C++ was unchanged; the previously passing 19-test C++ suite was not rerun.
  Full Python regression was intentionally not repeated.

```sh
.venv/bin/python -m pytest tests/python/test_gamma_surface.py tests/python/test_gui_gamma.py tests/python/test_gui_gamma_integration.py tests/python/test_gui_app.py tests/python/test_gui_static.py tests/python/test_gui_documents.py tests/python/test_gui_windows.py -q --maxfail=1 --tb=short -o faulthandler_timeout=30 --basetemp=build/test-gamma-gui-local-20261109
.venv/bin/python -m pytest tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-gamma-static-final-20261109
PYTHONUNBUFFERED=1 .venv/bin/python -m qte gui --port 8768
```

The initial sandboxed GUI bind was denied; the sandboxed integrated tests stalled
in the existing AnyIO worker/event loop and were interrupted after diagnosis.
Scoped local-process permission allowed the above focused suite and preview to
run; no tests were weakened. No broker/account/data-feed connection, Git history
operation, package install or outside-repository write. Market-live surfaces
still require an entitled, timestamp-qualified options source and separate
provider integration; D2b.2 execution work remains the next engine milestone.

Previous checkpoint, November 8: **D2a settlement accounting and D2b.1 futures
margin budgeting implemented; 19/19 CTest and 80 focused Python tests passed.**
The runnable `qte_futures_accounting_demo` shows entry, opposing pending orders,
cash settlement and closing without double-counted PnL. This is synthetic
accounting/risk functionality, not an enabled derivatives strategy or broker.
No equity, Bar, strategy-selection or GUI execution gates were removed.
Preserve all prior Windows/D1/V1 changes.

### D2a / D2b.1 changes and handoff

- Position keeps economic entry price separate from the futures cash settlement
  basis. Portfolio owns an immutable settlement journal, shared event ordering,
  exact duplicate handling, provenance and atomic cash/position updates.
  Settlement preserves equity at an unchanged mark; later closes do not realize
  the same cash twice. Late events requiring historical inventory reconstruction,
  reference corrections and expiry processing remain unsupported and fail closed.
- The read-only futures margin evaluator consumes the authoritative Portfolio
  and accepted pending-order snapshots. It reports current initial/maintenance
  requirements, conservative pending reservations, estimated fees and collateral.
  Opposing pending orders receive no netting credit. Visible, fresh marks and a
  sourced, time-valid, matching futures-only margin schedule are required.
- `examples/futures_accounting.cpp` uses artificial NQ-like values and margin
  rates. Its final equity is 10195 from initial cash 10000, economic gross PnL
  200 and fees 5. These are fixture arithmetic, not strategy performance.
- Sol implemented the accounting/margin components; root completed integration,
  the runnable example and documentation. Astra's bounded read-only reviews
  found no remaining blockers. Draft test expectations that double-counted
  settlement cash and an incorrect getter name were corrected; no checks were
  weakened or tests deleted. The settlement path regression compares 32
  deterministic paths with and without cash settlement.
- **Next: D2b.2**, use margin budgets for order admission/fill decisions and
  integrate derivative execution fees. A causal settlement-event source still
  needs event-loop integration. D3 quote execution, D4 strategy lifecycle and D5
  GUI execution remain gated; neither this margin report nor the demo grants
  trading permission. See [ADR 0013](adr/0013-derivative-research-foundation.md).

### Final verification (repository root, November 8)

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --preset dev -DQTE_BUILD_EXAMPLES=ON
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev -j2
# build/local-dev, native /usr/bin/g++; successful
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# 19/19 passed
./build/local-dev/qte_futures_accounting_demo
# successful; settlement preserves equity, final cash/equity 10195
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" CMAKE_BUILD_PARALLEL_LEVEL=2 .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
# existing QTE rebuilt/reinstalled inside .venv, offline; no new dependencies
.venv/bin/python -m pytest tests/python/test_bindings.py tests/python/test_lifecycle.py tests/python/test_explicit_availability.py tests/python/test_research_config.py tests/python/test_research_baseline.py tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-d2-focused-20261108
# 80 passed
```

Verification was deliberately bounded to the complete C++ suite and affected
Python integration tests; the full Python suite was not rerun (last complete
run: V1 below). No Git history/remote operations, GUI restart, broker connection,
system installation or outside-repository write. No new market data downloaded.
Generated build/test artifacts remain in ignored repository paths.

Previous checkpoint, November 5: **V1 VIX analysis sidecar implemented and reviewed;
1963 Python passed / 8 native-Windows tests skipped; 16/16 CTest passed**.
This resumes the November 4 usage interruption.
No D2 trading gates were removed. Prior D1 and Windows edits remain preserved.

### V1 changes and review

- `python/qte/volatility.py`: pure point-in-time selected-VX proxy analysis,
  explicit target scale, Calendar365 durations, ±1/2/3σ endpoint bands, normal
  expected absolute moves, all supplied completed-candle OHLC level distances,
  and optional independently supplied signed USD/point² gamma scenarios.
  The interpretation of “all candle levels” defaults to both timeframes and
  completed OHLC levels; no clarification answer arrived.
- `python/qte/adapters/cboe_vix.py`: strict offline saved-byte Cboe adapter,
  exact-byte hash and retrieval-based vintage, monthly/weekly VX identities
  qualified by expiry, conservative date/expiry assumptions. An absent CSV
  discretionary marker is unknown, not evidence of a non-discretionary price.
- Sol completed adapter tests and the offline `examples/vix_expected_moves.py`.
  Root implemented analysis/tests/documentation; Astra independently reviewed
  both passes. Review corrected weekly-symbol rejection, decade-colliding IDs,
  intermediate gamma underflow/overflow, and asynchronous spread disclosure.
  CLI tests use `sys.executable`, preserving Windows-compatible invocation.
- [VIX scenario documentation](strategies/vix-expected-moves.md) is registered
  in the GUI library with research/mixed status and linked from the roadmap
  and Nasdaq specification. No interactive form, strategy registration or
  broker execution is claimed. Existing GUI process was not restarted.
- Default example is entirely synthetic. Saved Cboe mode is explicitly mixed:
  actual VX file plus synthetic NQ candles/gamma. The analyzer has no automatic
  network I/O, auto-roll, option pricing or dealer-inventory inference.

### V1 verification (repository root, November 5)

```sh
command -v g++
# /usr/bin/g++; no compiler or package installed
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev -j2
# build/local-dev: no work to do
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# 16/16 passed
.venv/bin/python -m pytest tests/python/test_cboe_vix.py -q --maxfail=1 --tb=short
# 34 passed
.venv/bin/python -m pytest tests/python/test_vix_example.py -q --maxfail=1 --tb=short
# 15 passed after cross-platform/default-gamma review fixes
.venv/bin/python -m pytest tests/python/test_volatility.py tests/python/test_cboe_vix.py -q --maxfail=1 --tb=short --basetemp=build/test-vix-focused-20261105
# 112 passed
.venv/bin/python -m pytest tests/python/test_volatility.py tests/python/test_cboe_vix.py tests/python/test_vix_example.py tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-vix-integration-20261105
# 138 passed
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-vix-full-20261105
# 1963 passed, 8 skipped, 122.84s
.venv/bin/python -m pytest tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-vix-docs-20261105
# 11 passed after final checkpoint update
git diff --check
# passed
```

Full regression uses scoped execution permission for local loopback/subprocess
tests, not broker/external-service connectivity. Python runs in the repository
`.venv`; test artifacts stay in ignored `build/`. No dependency installs.
The 127 dedicated adapter/analyzer/CLI tests are included in the full count.
Astra's final read-only review found no blocking defects or documentation
mismatches. The synthetic CLI smoke also confirmed gamma 0.02, seven horizons,
and a daily one-sigma move of 209.788308 points from its artificial inputs.

### Public-data smoke and next boundary

One unauthenticated, public Cboe CSV GET was performed, with scoped network
permission and no redirects, credentials or account operations:

```sh
mktemp -d build/vix-cboe-20261105.XXXXXX
# build/vix-cboe-20261105.jHHM20
curl --fail --silent --show-error --max-time 20 --max-filesize 1048576 'https://www-api.cboe.com/us/futures/market_statistics/settlement/csv/?dt=2026-11-03' --output build/vix-cboe-20261105.jHHM20/settlement.csv
.venv/bin/python examples/vix_expected_moves.py --vx-csv build/vix-cboe-20261105.jHHM20/settlement.csv --settlement-date 2026-11-03 --retrieved-at-ns 1793940954542200349 --contract-id 'VX/X6@2026-11-18' --volatility-scale 1.0
```

The exact retrieved bytes (1728) have SHA-256
`a0b5c5279cacce9b34d8525a54adad9bf693be68679f2c441b4451400dfa0ca5`.
`retrieval.json` alongside the file records a conservative local timestamp
after download completion; this is not historical publication time.
Parsing yields 14 VX rows, 13 unexpired as of that timestamp. Selected monthly
VX/X6@2026-11-18 is 19.8993. With synthetic NQ anchor 20040 and scenario scale
1.0, the 24-hour one-sigma move is 208.732024 points across seven horizons/eight
OHLC levels each. This validates mechanics, **not** historical NQ forecasting,
option pricing, dealer activity or profitability. Full JSON was checked via
a stdout JSON parser; no report was represented as a historical strategy run.

Next research boundary: qualified causal Nasdaq/options data, matched-horizon
calibration and chronological out-of-sample endpoint coverage tests. No licensed
data or calibrated probability exists yet. Derivative implementation next remains
**D2 settlement/margin**, then D3 quote execution and D4/D5 lifecycle/GUI. Windows
work remains paused. No Git history/remote operations, GUI restart, system
installation or outside-repository write was performed. Account connections,
paid datasets and broker trading need separate authorization.

Previous checkpoint, November 4: **D1 derivative foundation implemented and
reviewed — 1835 Python passed / 8 native-Windows tests skipped; 16/16 CTest passed**. The user selected
both Nasdaq options gamma scalping and futures-only dealer-gamma research on
November 3. Canonical contract metadata, existing-ledger multiplier/accounting
support, and both pure causal decision policies are implemented. Windows work
below remains paused/preserved. Derivative backtests and GUI/CLI execution remain
blocked until settlement, margin, quote execution and lifecycle gates pass.

### D1 changes and review

- Existing InstrumentSpec/Fill/Position/Portfolio now handle explicit contract
  multipliers and premium-paid options versus unsettled futures carrying value.
  Futures notional is exposure, not an equity purchase cashflow. Zero option
  marks are supported; zero-priced fills, settlement and exercise are not.
- ResearchProfile, dataset preflight, open-only execution and equity risk reject
  derivatives, including flat mixed portfolios. The binding exposes read-only
  fill multiplier without exposing a second mutable Python ledger.
- `python/qte/strategies/nasdaq_gamma.py` provides GammaHedgePolicy and
  DealerGammaPolicy. Pure target decisions only; no broker calls, orders or
  fabricated fills. There are 69 dedicated policy tests.
- Regressions cover reference-time Greek freshness, visible revision selection,
  future-source isolation, lagged price revision cutoff, no hedge removal on
  missing Greeks, exact half-away rounding, overflow and true range breakout.
  Initial draft tests incorrectly rejected rounding 1.1 and expected a reversal
  reason after an earlier mean-cross exit. Corrected those test specifications;
  retained strict quantity checks and asserted the actual flatten/no-reversal rule.
- Sol implemented C++ accounting; Terra drafted policies before a usage limit;
  root completed/reviewed the policy fixes. Independent Astra reviews passed
  the execution guards, core accounting and final policy layer.
- Added [ADR 0013](adr/0013-derivative-research-foundation.md), updated the
  [strategy specification](strategies/nasdaq-gamma-scalping.md), and registered
  both documents in the GUI library as mixed/incomplete capability. Added
  `examples/nasdaq_gamma_policy.py`, a clearly synthetic offline smoke example.

### Exact final verification (repository root, November 4)

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --preset dev
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev -j2
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build/local-dev, native /usr/bin/g++; 16/16 passed
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
# Existing QTE rebuilt/installed into .venv; no dependencies downloaded/installed
.venv/bin/python -m pytest tests/python/test_nasdaq_gamma.py -q --maxfail=1 --tb=short --basetemp=build/test-gamma-policy-20261104
# 69 passed
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-gamma-full-20261104
# 1835 passed, 8 skipped, 126.75s
.venv/bin/python examples/nasdaq_gamma_policy.py
# synthetic-policy-only targets: hedge -3, dealer breakout +1
.venv/bin/python -m pytest tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-gamma-docs-20261104
# 10 passed after final documentation updates
git diff --check
# passed
```

Full Python regression used scoped execution permission for local
loopback/subprocess tests; no external services. No JS logic changed. A November
3 editable build was interrupted at the tool-output level by usage exhaustion;
November 4 rebuild completed explicitly. The earlier doc/config test first
failed because its fixture omitted required `symbol`; fixing the fixture tested
the intended unsupported-strategy rejection (42 passed). No production guard
or existing assertion was weakened.

### Resume here

Next is **D2**, split into reviewable increments: explicit idempotent futures
variation-settlement events/basis conservation, then contract-aware margin and
pending-order reservations/per-contract costs. Test settlement-then-close,
duplicate/conflicting settlements, reversals, option/hedge combined equity and
rollback before removing any gate. D3 supplies causal quote/Greek events and
execution; D4a/D4b wire the two full strategy lifecycles; D5 adds normal research
and GUI execution. Keep the current cash-equity path unchanged.

No licensed historical quotes/dealer data or profitability evidence exists.
Public CME research only was consulted November 3, as requested. Account access,
paid data acquisition and live/paper broker execution require separate authority.
No Git history/remote operations, GUI server restart, system installs or
outside-repository writes were performed. All prior Windows changes are preserved.

October 28 priority change: the user requested Nasdaq futures gamma-scalping
research and implementation. Windows work is paused, with all changes below
preserved. See [research specification and implementation gates](strategies/nasdaq-gamma-scalping.md):
the prior scope question was resolved November 3 (both). No derivative strategy
is executable yet.

Latest checkpoint, October 27: **W2c.1–.3 persistence foundation implemented
and reviewed**. Combined regression: **1763 Python / 15 CTest**, eight native
Windows tests skipped. SQLite ownership/URI fixes, hardened Linux journal
publication and bounded Windows disclosure reads are complete for this slice.
Native durable writes, SQLite containment and W2d integration still remain.
No Windows GUI routes or job capabilities were enabled. The worktree was clean
at the start; preserve all new changes. Commands and remaining gates follow below.

Prior checkpoint: **W2b private Windows artifact reader implemented** on 2026-10-25:
**1709 Python / 15 CTest**, seven native Windows tests skipped; independent Astra
review passed. The storage facade and GUI remain Windows-blocked. W1/W2b native
acceptance and W2c persistence/W2d integration remain; do not enable routes yet.
Prior W2a gate: **1675 Python / 15 CTest** on 2026-10-24.
User confirmed
PowerShell failed on a Linux-only module and explicitly requested Windows GUI
functionality. First increment isolates platform routes and adds pinned Windows
document reads; W2 artifact storage and W3 supervised jobs remain separate gates.
No Windows runtime is available here; native build/handle/browser acceptance is
required before declaring support verified. W1 Linux regression passed on
2026-10-23: **1655 Python / 53 JavaScript / 15 CTest**; three native Windows
tests skipped. Limited-mode browser smoke and independent Astra review passed.
W2a isolates the existing Linux artifact backend and centralizes snapshot identity
without enabling Windows storage/jobs. W1 acceptance still requires Windows.

Last accepted checkpoint: 2026-10-22, **R2b session-aware replay**.
Verification: **1613 Python / 51 JavaScript / 15 CTest**, independent Astra
review, offline extension rebuild and executable session example passed.
R2c local session configuration/export/GUI integration remains queued after the
user-requested Windows work. No network, new dependencies or Git operations.
Prior G9a verification: **1535 Python / 51 JavaScript / 15 CTest**, independent Astra
review, and real local Connections/benchmark browser smoke checks passed.
The local research test build is usable; see [start/data-flow/strategy guide](test-build-guide.md).
Status checks environment names only, never credential values. No broker
transport, signer, contract scan, response persistence or enable flag was added.
G9b actual reads remain deferred: approved setup, selected operations and bounded
private-response contract must precede any separately approved online read.
No broker GET or account action is authorized. No schedule is active. Stay
offline; do not install packages or perform Git history/remote operations.
**Read this file before resuming.**

## Authority and scope

User approved installation of the pinned GUI/test requirements in the existing
`.venv`, Sol and Terra build agents, and GPT-6 Astra review. Work through G1,
G2 and G3a/G3b/G3c in sequence. On 2026-09-24 the user extended authorization to
G4 and G5 after G1–G3 pass their gates. No broker requests, system package
installations, Git history changes or outside-repository
writes. Preserve all existing modified/untracked files and research artifacts.
User explicitly wants to keep pushing manually. Local inspection found `origin`
at `github.com:Ky13r13/qte-mk2.git`; push credentials/rights were not tested. Do not
commit, push, probe remote write access, or change remotes/history.
Read `AGENTS.md`, `docs/gui-architecture.md` and `docs/gui-dependencies.md`.
On October 8 the user explicitly authorized continuing all remaining G6–G9
milestones to support strategy development, while retaining the request for
token-efficient work and concise documentation. This authorizes implementation
and offline/mock checks, not credential acquisition, live downloads, broker
requests, new dependencies or Git operations. Each external capability retains
its explicit approval/prerequisite boundary.
On October 12 the user authorized task-scoped local commands to finish the GUI
and explicitly prohibited internet connections. Continue offline with the
existing tools; no provider/broker requests, remote Git operations or downloads.
The one-time resume heartbeat was updated for October 13 at 03:45 Los Angeles
time. It fired, resumed this work, and was deleted after use; it is not a
nightly schedule or usage-reset guarantee.

## Baseline / environment

### October 27 W2c persistence foundation

W2c.1 centralizes GUI SQLite connection ownership: escaped lexical absolute
read-only URIs, existing transaction semantics, explicit closure on every
post-open path, unchanged timeouts and materialized query results. Catalog,
job manager, dataset and comparison call sites use the owned scope; existing
test/benchmark scopes inherit it. No `immutable=1`, schema migration or new
dependency. Regressions cover escaped checkout names, missing/read-only DBs,
commit/rollback/deferred commit errors, closure, historical finalized-job schemas
and preserved job-kind/idempotency gates.

W2c.2 retains independent immutable schema-v1 JSON evidence. Records are bounded
before storage creation; collisions validate through the pinned publication
directory and fail closed without overwriting evidence. Cleanup only removes
temporaries created by this operation; primary errors and published evidence
are preserved. Tests cover corrupt/wrong-identity records, deep JSON, symlinks,
FIFO nonblocking rejection, root replacement, owned descriptors and injected
write/flush/link/cleanup failures. A failed post-publication flush can leave a
valid record; this is not batch rollback or a power-loss certification.

Sol implemented SQLite changes; Terra implemented journal fixes; root added
catalog/finalized-job integration tests and reviewed both; Astra approved the
Linux foundation. Review fixed an unowned-temp cleanup risk and a FIFO-blocking
collision reader. The job test fixture now loads its real inline-input dependency
before installing its narrow legacy stub, fixing isolated-test import ordering
without changing production snapshot acceptance. The proposed extra recursion
catch was unnecessary: `strict_json` already translates parser recursion into
validation failure; new regressions verify that behavior.

Root verification commands from the repository root:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build/local-dev: no work; 15/15 passed in 0.08s
.venv/bin/python -m pytest tests/python/test_gui_sqlite.py tests/python/test_gui_disclosures.py tests/python/test_gui_disclosure_integration.py tests/python/test_gui_catalog.py tests/python/test_gui_job_protection.py tests/python/test_gui_jobs.py -q --maxfail=1 --tb=short --basetemp=build/test-w2c-final-focused-20261027
# 95 passed in 1.98s, before three additional nested-JSON regressions
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-w2c-full-20261027
# 1749 passed, 7 native-only skipped in 113.86s
.venv/bin/python -m pytest tests/python/test_gui_sqlite.py tests/python/test_gui_disclosures.py tests/python/test_gui_disclosure_integration.py -q --maxfail=1 --tb=short --basetemp=build/test-w2c-review-regressions-20261027
# 43 passed in 0.55s, including the additional regressions
git diff --check
# passed
```

Full regression uses the existing scoped local subprocess/loopback permissions.
No external network, package installation, Git mutation or user GUI restart.
Native GCC exists; PowerShell/Wine/MinGW were not found by read-only inspection.

The user then requested continued work. W2c.3 reuses the private Windows stable
reader for deterministic disclosure/protection record names, a 4096-byte limit
and the shared strict schema. Linux keeps its existing reader; unsupported hosts
return unavailable without I/O. Non-Linux mutators explicitly reject even empty
requests before filesystem access. A native-only fixture covers valid, oversized,
wrong-identity and malformed records; mock tests verify routing and write guards.
Astra approved the product boundary. Its required Linux-only test skip was fixed
before the final run; platform mocks do not mutate global `sys.platform`.

```sh
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-w2c-full-final-20261027
# 1763 passed, 8 native-only skipped in 113.67s
git diff --check
# passed
```

Changed files: GUI `sqlite.py` (new), `catalog.py`, `jobs.py`,
`comparison_inputs.py`, `dataset_views.py`, `disclosures.py`; tests
`test_gui_sqlite.py`, `test_gui_disclosures.py`,
`test_gui_disclosure_integration.py`, `test_gui_disclosure_platform.py` (new),
and `test_gui_jobs.py`; this log, `windows-gui.md` and `roadmap.md`.
JavaScript/browser checks were not rerun: no UI code or capabilities changed.

Next: implement and natively validate the Windows durable journal writer and
SQLite connection-lifetime database/sidecar containment, then W2d integration.
Do not substitute path prechecks for containment, migrate disclosure history
into the rebuildable catalog, or enable Windows artifact routes early. The
[Windows acceptance command](windows-gui.md#native-acceptance-and-remaining-work)
now includes the new disclosure read test. A Windows host is required for native
verification; installing any runtime/toolchain or enabling network access still
requires separate approval. No such installation or access is needed to use the
existing Linux GUI.

### October 24–25 W2b private Windows artifact reader

Implemented privately. Reuses W1's no-follow, read-shared handle opens and volume-GUID paths;
adds full 64-bit volume/128-bit file identity and raw Windows last-write/change
times through an independent handle extension. A tagged Windows snapshot avoids
fabricating POSIX inode/ctime equivalents. Bounded iterative inventory uses names
only from directory enumeration, checks each entry through a handle, retains
ancestors, checks directories for changes, and closes iterators/handles on errors.
Stable reads check expected identity, size, and before/after metadata. No ordinary
path-open fallback. This is change-detecting traversal, not an atomic snapshot.

Sol's two identity files survived the October 24 usage interruption; root recovered
them and Sol completed their tests on October 25. Root owns the private reader;
Terra owns its independent tests and Astra reviews. Files so far:
`windows_artifact_handles.py`, `_artifact_io_windows.py`, Windows handle/reader
tests, Windows guide, roadmap and this log. Neither `artifact_io` dispatch nor GUI
capabilities is changed. Native Windows, W2c persistence and W2d integration remain
separate gates. Existing Linux filesystem behavior and the C++ core are unchanged.

Verification commands from the repository root:

```sh
.venv/bin/python -m pytest tests/python/test_gui_windows_handles.py tests/python/test_gui_windows_artifacts.py tests/python/test_gui_artifact_io.py tests/python/test_gui_catalog.py -q --maxfail=1 --tb=short --basetemp=build/test-w2b-final-focused-20261025
# 71 passed, 4 native-only skipped in 0.95s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build/local-dev: no work; 15/15 passed in 0.08s
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-w2b-full-20261025
# 1709 passed, 7 native-only skipped in 119.27s
.venv/bin/python -m pytest tests/python/test_gui_windows_handles.py tests/python/test_gui_windows_artifacts.py -q --maxfail=1 --tb=short --basetemp=build/test-w2b-acceptance-fixtures-20261025
# 34 passed, 4 native-only skipped in 0.51s after native-fixture refinement
git diff --check
# passed
```

Full regression used the previously required scoped local loopback/subprocess
permissions. All Python stayed in `.venv`. No new dependencies, external network,
Git mutations or foreground GUI restart. Read-only tool inspection still found
native GCC but no PowerShell, Wine or MinGW Windows toolchain.

Review corrected test fixtures rather than production safeguards: the total-size
fixture now reaches the actual size limit instead of failing on its fake root;
native tests call real wrappers rather than a nonexistent helper; the unsupported
platform test injects Linux explicitly even when run on Windows. The native
replacement fixture creates a distinct file before replacement to avoid relying
on recycled IDs/timestamp resolution. Native volume IDs use the documented full
unsigned range, not an invented nonzero prerequisite. Injected tests cover scanner
and handle cleanup, identity changes, bounded reads, aliases/reparse points and
independent limits. Astra's source/test reviews have no remaining required fixes.

Native Windows identity/sharing/enumeration and replacement checks are still
unrun; the seven native-only tests are listed in `windows-gui.md`. Next work is
W2c's durable protection/disclosure and SQLite containment design, followed by
implementation/integration. Do not select this backend for the GUI until the
combined persistence and native acceptance gates pass. W3 jobs remain separate.

### October 24 W2a artifact backend boundary

Implemented: extracted existing descriptor-based artifact operations into private
`_artifact_io_posix.py`, retaining the `artifact_io` facade and public signatures.
Non-Linux hosts reject artifact filesystem operations before importing the backend
or touching files; there is no ordinary-open fallback. Shared `Snapshot.identity()`
now drives stable reads and sorted catalog fingerprints. No persistent format,
holdout policy, engine behavior or Windows route availability changed.

Sol implemented extraction, Terra added independent boundary tests, root integrated
catalog cache checks and reviewed test sensitivity, and Astra reviewed the product
change. New tests cover limits, forbidden entries, all expected identity fields,
replacement/mutation, descriptor cleanup and unsupported hosts. Root's catalog
regression proves reorder-only inventories reuse verification while same-content
file replacement forces reinspection. Astra's product and final test reviews found
no blockers. W1 native acceptance remains unavailable on this Linux host.

Files changed this increment: `artifact_io.py`, new `_artifact_io_posix.py`,
`catalog.py`, new `test_gui_artifact_io.py`, catalog tests, Windows guide, roadmap
and this log. No core, frontend, dependency, disclosure format or database change.
No internet, installations, Git mutations or foreground GUI restart.

Commands run from the repository root:

```sh
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_catalog.py tests/python/test_gui_router_protection.py tests/python/test_gui_job_protection.py tests/python/test_gui_form_protection.py tests/python/test_gui_comparison_catalog.py -q --maxfail=1 --tb=short --basetemp=build/test-w2a-protection-scoped-20261024
# 96 passed in 6.77s
.venv/bin/python -m pytest tests/python/test_gui_artifact_io.py tests/python/test_gui_catalog.py -q --maxfail=1 --tb=short --basetemp=build/test-w2a-contract-final-20261024
# 37 passed in 0.52s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build/local-dev: no work; 15/15 passed in 0.06s
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-w2a-full-20261024
# 1675 passed, 3 native-Windows skipped in 118.06s
git diff --check
# passed
```

The sandboxed protection run (`--basetemp=build/test-w2a-protection-20261024`)
stalled at the existing threaded API tests and was interrupted; the scoped local
rerun above passed. An intermediate contract run (`--basetemp=build/test-w2a-contract-20261024`)
had 18 passes plus a test-guard failure: Python's import `fromlist` can be None.
The fixture now normalizes that value and restores import/filesystem patches
before pytest reporting. Root also separated total-byte and entry-count limits
so the latter cannot mask the former. No assertion or production safeguard was
removed to obtain a pass. All Python used the repository `.venv`.

Next: W2b native inventory/read implementation and acceptance, then W2c
protection/SQLite persistence and W2d integration. Keep Windows artifacts/jobs
disabled until their respective boundaries are verified. W1 native acceptance
is independent and still requires the user's Windows checkout.

### October 22–23 W1 Windows Library/Connections increment

Removed unconditional Linux job/storage imports from GUI startup. Host-selected
capabilities retain the complete Linux GUI and expose only authenticated Library
and offline Connections elsewhere. Unsupported routes fail closed with 503 after
authentication; browser links/deep links cannot expose job controls. Downloads and
broker operations remain disabled. Added exclusive Windows loopback socket binding.

Added a bounded Win32 document reader with pinned non-reparse ancestors, local
volume-GUID child paths, denied write/delete sharing, size/identity checks and
handle cleanup. Astra review caught ancestor write sharing and empty logical-path
handling; both corrected with regression coverage. This code is implementation-
reviewed, not native-Windows accepted. No Windows runtime/PowerShell is available
on this host; no tools or dependencies were installed.

Changed W1 files: GUI app/server/safeio/documents/static app; new platform-support,
Windows-reader and JS capability modules; new Windows Python/JS tests and server
regressions; `scripts/start-gui.ps1`; README, GUI usage, roadmap, Windows guide and
this log. All preceding R2a/R2b and user changes remain intact. No Git mutations.

Exact final verification commands from the repository root:

```sh
.venv/bin/python -m pytest tests/python/test_gui_windows.py tests/python/test_gui_app.py tests/python/test_gui_server.py tests/python/test_gui_documents.py tests/python/test_gui_static.py tests/python/test_gui_connections.py -q --maxfail=1 --tb=short --basetemp=build/test-w1-final-focused-20261022
# 93 passed, 3 native-only skipped in 1.15s
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/*.test.mjs
# 53 passed
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build/local-dev: no build work; 15/15 passed
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-w1-full-20261023
# 1655 passed, 3 native-only skipped in 113.89s
.venv/bin/python -m pytest tests/python/test_gui_documents.py -q --basetemp=build/test-w1-document-final-20261023
# 9 passed in 0.11s after documentation formatting/acceptance-command correction
git diff --check
# passed
```

Existing loopback/subprocess tests needed scoped local execution. One sandboxed
focused attempt stalled and was interrupted; October 22's full-suite approval
attempt hit reviewer usage exhaustion. A fresh approved October 23 run completed
above; neither interruption was counted as a pass or worked around with weakened
tests. Python remained in the repository `.venv`.

Browser smoke used a separate port-8767 preview with Linux capabilities explicitly
limited for testing. Verified login, hidden Research/Data links, System routing to
offline Connections, Windows-guide rendering, blocked research deep link without
launch controls, and logout. This exercises UI gating, not Win32 handles. The
guide's table was changed to a list for the Library's CommonMark renderer. The
preview/tab were stopped/closed; the user's port-8765 GUI was left untouched.

Remaining: run the native acceptance subset in [windows-gui.md](windows-gui.md)
on the user's Windows checkout, including actual handle/junction/sharing tests,
PowerShell/native extension and browser smoke. The subset excludes existing POSIX
FIFO/process fixtures; the full Linux suite remains unchanged. W2 artifact/Data
storage and W3 process supervision are queued and must not be advertised as
Windows-ready. Fresh Windows dependency bootstrap is also unvalidated; installing
missing tools/packages or enabling any online access still needs approval.

### October 22 R2b session-aware replay bridge

Implemented opt-in `explicit_availability_v1` datasets and `BarObservation`;
canonical Bar v1 and fixed-interval hash bytes remain unchanged. Explicit
publication clocks allow variable daily/short-tail durations, reject unordered
per-symbol releases, and never backfill an already-processed opening. Strategy
`now_ns` is callback time; candidate/router macro gates use it for point-in-time
availability and staleness. Manifests distinguish observation and replay ends.

The engine preserves newer economic marks while applying all ledger events at
current replay time. Astra caught cross-symbol backwards-ledger timestamps and
pre-epoch manifest initialization; both fixed and regression-tested. Final review
also confirmed potential signed-duration overflow in existing analytics; analysis
now rejects nonmonotonic events or elapsed spans above INT64_MAX before subtraction.
This keeps normal-run arithmetic unchanged. Sol/Terra built scoped components;
root completed integration/verification after their usage interruptions. Astra
review found no remaining issues in the integrated changes or duration guard.

Added `build_session_dataset` for already-canonical bars plus a caller-declared
schedule/source/action-free assertion. No aggregation, repair, source verification
or network. Missing buckets remain gaps; gap count still includes overnight
closures. Direct Python replay/analytics work; existing GUI/CLI formats remain
fixed-interval, and comparison/experiment APIs reject explicit timing pending R2c.
Added ADR 0012, Library registration, data-flow/strategy guide and executable
synthetic example. Foreground GUI was not restarted automatically.

Files changed for R2b: dataset/schedule, engine and strategy headers/sources;
analytics source; bindings; package/adapter exports; new session adapter;
research/experiment guards; candidate/router clocks; GUI document registry;
C++ dataset/schedule/strategy/backtest/analytics tests; new Python availability/
session-adapter tests and candidate/router/document regressions; README, ADRs
0002/0011/0012, architecture/adapters/roadmap/macro-router/test-build docs and log.
Prior R2a/user work preserved. No Git history or remote changes.

Commands run from the repository root (offline):

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python/test_explicit_availability.py tests/python/test_bindings.py tests/python/test_candidates.py tests/python/test_router.py tests/python/test_experiments.py tests/python/test_research_baseline.py -q --maxfail=1 --tb=short --basetemp=build/test-r2b-bindings-20261022
.venv/bin/python -m pytest tests/python/test_session_bars.py tests/python/test_session_calendar.py tests/python/test_sampling_integrity.py -q --maxfail=1 --tb=short --basetemp=build/test-r2b-session-20261022
.venv/bin/python -m pytest tests/python/test_explicit_availability.py tests/python/test_session_bars.py tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-r2b-final-focused-20261022
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-r2b-full-20261022
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/*.test.mjs
git diff --check
```

Focused stages: 188, 56 and 46 passed. Native build and **15/15 CTest** passed.
Offline editable reinstall rebuilt only this repository's extension inside its
`.venv`; no dependencies downloaded/installed. JS first encountered sandbox
`spawnSync EPERM`; the same command with scoped local subprocess permission
passed **51/51**, without code changes. Full Python **1613 passed in 112.08s**.
The guide's Python example was executed directly from its Markdown code block:
one next-session fill, final equity 1001, explicit timing manifest.

During this work the user reported native Windows/PowerShell startup failure
and confirmed a module error. Read-only audit found unconditional `fcntl` import
in GUI jobs, Linux subreaper/process-group supervision, Unix venv paths and
directory-relative file operations. Changing shell commands is insufficient;
native Windows needs a separate portability milestone. No Windows fix or install
was performed at that diagnostic point, and no Windows/WSL validation is claimed.
The user subsequently explicitly requested the Windows GUI port (W1–W3 above).

### October 21–22 R2a offline session contract

Added `python/qte/session_calendar.py`: immutable explicit inclusive schedules,
closed-date records, UTC-nanosecond daily/hourly windows, truncated tails,
declared publication delays, close grids and revision-sensitive snapshot hashes.
It rejects unknown/missing/out-of-order dates, invalid types and overflow.
Schedules are caller-declared, not exchange-verified; no holiday/timezone rules
are inferred. No Bar, Dataset, replay, adapter or broker behavior changed.

Sol implemented the module; Terra added independent fixtures; root completed
final tests/docs after Terra's usage interruption. Astra reviewed and cleared
the implementation. Review found list copying before the coverage-size check;
fixed and regression-tested. Two new test-fixture errors were diagnosed and
corrected: Fill exposes `effective_ns`, and the frozen-window test needed valid
date-matched timestamps before attempting mutation. Frozen tests now assert
`FrozenInstanceError` specifically; no test was deleted or weakened.

New `tests/python/test_session_calendar.py` covers 34 cases, including declared
DST/holiday/weekend/early-close/exceptional closures, bounds, ownership and hash
sensitivity. An actual-engine sampling regression preserves fixed half-hour bars,
holiday gaps, next-available-open execution and exact equity, using an explicit
run-start anchor plus declared session closes. It is not daily replay support.
The new ADR is registered and link-tested in the existing GUI Library; the
standalone example executes successfully. Existing foreground GUI was not
restarted; its new registry entry requires a normal server restart.

Changed files: new module, calendar tests and `docs/adr/0011-session-calendar.md`;
sampling/document tests, GUI document registry, adapter/architecture/roadmap/
test-build guidance and this checkpoint. Existing user work was preserved.

Commands from the repository root (all offline, existing `.venv`/native tools):

```sh
.venv/bin/python -m pytest tests/python/test_session_calendar.py -q --basetemp=build/test-r2a-agent-20261021
.venv/bin/python -m pytest tests/python/test_session_calendar.py tests/python/test_sampling_integrity.py tests/python/test_adapters.py tests/python/test_research_adapters.py tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-r2a-focused-20261021
.venv/bin/python -m pytest tests/python/test_session_calendar.py tests/python/test_sampling_integrity.py tests/python/test_adapters.py tests/python/test_research_adapters.py tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-r2a-focused-20261022
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-r2a-full-20261022
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/*.test.mjs
git diff --check
```

Final focused **62 passed**; full Python **1571 passed in 111.48 seconds**;
native build succeeded (no work required), **15/15 CTest** and **51/51 JS**.
No external data verification or new browser-flow acceptance claimed. No
installs, internet, broker calls or Git history/remote operations. Remaining
gates: R2b timing integration, verified schedule acquisition, corporate-action
accounting and real-data research. No new approval was needed for this increment.

### October 21 local GUI launch

On the user's “Start” request, launched `.venv/bin/python -u -m qte gui`
at `http://127.0.0.1:8765`, completed local one-time sign-in, and left the
Library tab/server open for use. Broker reads/downloads remain disabled;
no G9b implementation, dependency or Git changes. This is a foreground local
process, not an installed persistent service. No access code is retained here.

### October 21 G9a implementation and verification

Added passive **System → Connections** for Schwab and Robinhood Crypto, with
collapsed prerequisites and explicit refresh. The authenticated/no-store status
endpoint exposes only two environment-key presence bits plus fixed metadata;
the bounded POST read boundary always denies execution. No credential values,
broker clients, transport, signer, private-response storage or jobs are involved.
Existing trusted-code broker adapters are unchanged. Deep-link navigation now
also accepts the existing benchmark page without relaxing API authentication.

Sol built the backend/tests; Terra built the view/JS tests; root integrated and
verified; Astra reviewed. Review caught Python 3.14 environment membership
reading values: fixed with names-only iteration and a real `os._Environ`
regression that forbids value decoding. Malformed UI responses now consistently
report unavailable. Re-review found no remaining G9a issues. Documentation audit
also removed stale claims that test/comparison launch controls were unavailable.

Browser acceptance covered login, System navigation, both collapsed/expanded
provider entries, explicit refresh, blocked status and the registered docs link.
Also closed the earlier G8b basic browser-smoke gap: launched a 10-bar no-op job
`f9dd9ded65f74b329962c9a17d804b9f`, refreshed to verified success and inspected
metadata (zero fills, equity 100000, four identity groups unchanged, source-to-
binary correspondence unknown). This is synthetic mechanics/timing evidence,
not a performance claim or exhaustive accessibility/cross-browser acceptance.
The agent-created tab is closed; no temporary server on 8767 remained on resume.

The earlier full-suite terminal result was not retained across interruption;
the complete suite was rerun: **1535 passed in 109.92 seconds**, no skips/failures.
Native build succeeded (no work needed), **15/15 CTest**, **51/51 JavaScript**.
Commands from the repository root (offline; scoped local test permissions):

```sh
.venv/bin/python -m pytest tests/python/test_gui_connections.py tests/python/test_gui_research_browser_routes.py tests/python/test_gui_static.py tests/python/test_gui_documents.py tests/python/test_brokers.py -q --maxfail=1 --tb=short --basetemp=build/test-g9a-focused-20261021
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g9a-full-20261021
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g9a-full-resume-20261021
.venv/bin/python -m pytest tests/python/test_gui_connections.py tests/python/test_gui_research_browser_routes.py tests/python/test_gui_static.py tests/python/test_gui_documents.py tests/python/test_brokers.py -q --maxfail=1 --tb=short --basetemp=build/test-g9a-final-20261021
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/*.test.mjs
.venv/bin/python -u -m qte gui --port 8767
git diff --check
```

New files: GUI `connections.py`, `connection_routes.py`,
`static/system_connections.js`, Python connection tests and JavaScript view tests.
Updated app/static routing, System links, document registry, browser/static
regressions, broker/usage/architecture/roadmap/test-build docs and this log.
Existing G7b work was preserved. No installs, broker requests, outside-repository
edits or Git history/remote operations. G9b remains deferred as described above;
it is not needed to develop and backtest local strategies.
Final post-documentation integration checks: **143 passed**; `git diff --check`
clean. The full data-flow visualization and strategy authoring instructions are
maintained in [the test-build guide](test-build-guide.md).

### October 20–21 G7b implementation and verification

Added typed macro-router profiles to shared research configuration and CLI runs;
the GUI accepts bounded exact JSON text, compiles quoted integers server-side,
and uses existing authorized capture, preview fingerprints and immutable jobs.
Optional v2 `route_events.json` preserves old export maps when absent. Its strict
reader checks shape, causal inputs, policy/priority consistency and provenance;
catalog-protected tables expose four columns plus complete text-only records.
Financial-output protection flows to diagnostics, not in the reverse direction.
Comparison metadata remains restricted to its existing SMA/six-family contract.
No core execution, portfolio or strategy behavior changed.

Sol built config/form/protection and real-worker API regressions; Terra built
exports and validation. Independent Astra review found four defects (stale
proxy, huge-integer overflow, invalid wrapper containers, forged routing target),
all fixed with regressions and reviewed again. Root fixed a missed navigation
allowlist entry, stale table handlers, and comparison metadata contamination.
The first full run stopped at the unchanged comparison registry assertion:
480 passed / 1 failed. Fixed implementation, not the assertion. Final full run:
**1528 passed in 118.37 seconds**, no skips/failures.

Authenticated ASGI acceptance launches the actual worker, rejects a changed
profile's stale preview, explicitly registers output and checks exact timestamps,
as-of inputs, paging and older exports' `not_recorded` response. Browser smoke
acceptance additionally verified duplicate-key error focus, real preview/launch,
successful registration, four-column route rendering, retirement provenance and
back navigation. Basic narrow-window layout was inspected; this is not an
exhaustive accessibility/cross-browser gate. The prior browser-client localhost
block did not recur; no security setting was changed. The temporary server on
8767 and agent-created tab were closed after acceptance.

The [CLI example](../examples/macro-router-run.json) produces two fills and three
transitions with verified checksums in `build/macro-router-demo-20261021`.
[Profile usage](strategies/macro-router.md#gui-and-cli-profile) and the
[data-flow/strategy guide](test-build-guide.md) explain both GUI string and CLI
native-integer encodings. Synthetic inputs establish mechanics only. Their
declared evidence is in the profile; single-run catalog evidence remains unknown,
not independently authenticated historical/synthetic source classification.

Commands from repository root (offline; scoped ASGI/subprocess/loopback access):

```sh
.venv/bin/python -m pytest tests/python/test_gui_router_forms.py tests/python/test_router_config.py tests/python/test_gui_research_forms.py tests/python/test_gui_form_api.py -q --maxfail=1 --tb=short --basetemp=build/test-g7b-forms-20261020
.venv/bin/python -m qte run --config examples/macro-router-run.json --output build/macro-router-demo-20261021 --export-version 2
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7b-full-20261021
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7b-full-final-20261021
.venv/bin/python -m pytest tests/python/test_gui_router_api.py tests/python/test_gui_router_export.py tests/python/test_router_export.py tests/python/test_gui_comparison_forms.py tests/python/test_gui_documents.py tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-g7b-final-check-20261021
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/*.test.mjs
.venv/bin/python -u -m qte gui --port 8767
git diff --check
```

Changed components: `router_config.py`, `router_export.py`, shared research
configuration/export/CLI; GUI profile compiler/forms/comparison registry,
v2 validation/catalog protection/routes/views and JavaScript; router/config/form/
API/protection/export and UI regressions; example and usage/architecture/roadmap/
export/strategy/task-log documentation. Some October 20 changes were already in
the user's clean Git baseline on resumption. No Git history or remote operations
were performed, and no dependencies installed.
Final post-documentation/export/API/comparison checks: **54 passed**; repeated
complete JavaScript suite **48 passed**; diff check clean.

### October 19–20 G8b implementation and verification

Added shared `qte.benchmark`, private benchmark request/evidence/worker/routes,
small fixed-job scheduler hooks, and the System benchmark UI. The existing CLI
retains its arguments/default/1,000,000 cap; GUI caps at 100,000. No engine changes.
Sol built workload/tests and manager/evidence regressions; Terra built UI/tests
and controlled subprocess tests. Root integrated, added API/race/spec regressions;
independent Astra review found no remaining blockers in code/tests.

Checks cover no-op accounting, exact synthetic workload, malformed/nonfinite
measurements, pinned snapshots, queue/idempotency, forged success and runtime
changes, changed/rehashed/extra/symlink/partial files, strict HTTP auth/inputs,
passive views, raw registration denial, active cancellation, timeout/owner loss,
bounded draining, fixed argv/environment and stale UI handlers. Fixed a detached
launch-handler race, preserved edited form values across submission, and made
malformed worker output an execution failure instead of a provenance failure.

The ignored `build/g8b-acceptance-20261020.py` drives the actual authenticated
loopback API and supervisor for both 10,000-bar workloads, retries identical
requests, verifies evidence and checks raw catalog denial. Both succeeded with
all four explicit identity groups and tool versions unchanged. Artifacts remain
under `build/gui/jobs/<id>/artifacts`; no-op has zero fills/unchanged equity.
These are single-sample smoke measurements, not calibrated performance claims.
Browser creation was blocked by the browser client, not a QTE test failure.
No security setting was bypassed; temporary tab/server were closed.

Commands from the repository root (scoped offline subprocess/loopback permission):

```sh
.venv/bin/python -m pytest tests/python/test_benchmark.py tests/python/test_gui_test_jobs.py tests/python/test_gui_test_snapshots.py -q --maxfail=1 --tb=short --basetemp=build/test-g8b-initial-20261019
.venv/bin/python -m pytest tests/python/test_gui_benchmark_api.py tests/python/test_gui_system_tests_api.py tests/python/test_gui_research_browser_routes.py -q --maxfail=1 --tb=short --basetemp=build/test-g8b-api-20261019
.venv/bin/python -m pytest tests/python/test_benchmark.py tests/python/test_gui_benchmarks.py tests/python/test_gui_benchmark_worker.py tests/python/test_gui_benchmark_api.py tests/python/test_gui_test_jobs.py tests/python/test_gui_test_worker.py -q --maxfail=1 --tb=short --basetemp=build/test-g8b-workers-20261020
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/system_benchmarks_ui.test.mjs tests/javascript/system_tests_ui.test.mjs tests/javascript/comparison_form_ui.test.mjs tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
.venv/bin/python -u -m qte gui --port 8767
.venv/bin/python build/g8b-acceptance-20261020.py --code <ephemeral-terminal-code>
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g8b-full-20261020
.venv/bin/python -m pytest tests/python/test_gui_documents.py tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-g8b-docs-20261020
git diff --check
```

Initial 55, API16, final focused81, JS45 and CTest15 checks pass. Full Python:
**1485 passed in 115.53 seconds**, no failures/skips. No internet, installs,
Git history/remote operations, stale build/dev
use or outside-repository edits. Existing documentation edits were preserved.
Post-update documentation/static checks: **24 passed**; diff check clean.
See [benchmark contract](gui-benchmarks.md) and [strategy/data-flow guide](test-build-guide.md).

### October 18–19 G8a.3 acceptance

New production files: `test_routes.py`, `test_views.py`, `static/system_tests.js`.
New tests: `test_gui_system_tests_api.py`, `test_gui_test_views.py`, and
`tests/javascript/system_tests_ui.test.mjs`. Updated `app.py`/`static/app.js`,
static/browser-route regressions and evidence/usage/architecture/roadmap docs.
Sol built the browser module/tests; Terra began API tests; root finished API,
projection and integration work. Independent Astra reviews passed after fixes.

Evidence is protected even when jobs succeed. Reveal is durably bound to the
accepted request and all four exact evidence files; a valid replacement requires
new disclosure. Tests cover changed requests, rehashed replacement, journal
write/readback failure, mutation during reveal/read, failed/skipped history,
bounded text/events/durations and unknown current scope on preflight failure.
GET never prepares or launches a test. Current checks are explicit observations,
not continuous freshness or source-to-binary proof. Browser retries retain frozen
keys; stale handlers/results cannot launch or mutate another selected job.

Review caught an unbounded numeric duration and selection/retry races; fixed with
regressions. Initial API fixtures created output before exclusive snapshot setup;
corrected setup order, preserving validation. The first full GUI Python job
(`55c2d3569c644767b3f7608d5716fe44`) had 1429 passes/one failure: a static test
expected the old cleanup statement literally. Its replacement checks BOTH module
disposals before view advancement, plus System shell/asset protection. The failed
record remains readable only after reveal. No test was removed or weakened.
Final full GUI Python job passed **1430 in 106.65 seconds**, zero failures/skips.
Final focused **39**, JavaScript **37**, CTest **15/15**; independent review clear.

Actual in-app browser acceptance on loopback 8767 covered sign-in/idle expiration,
passive System loading, real CTest/Python launches, protected defaults, explicit
reveals of passed AND failed records, collapsed secondary fields and explicit
current identity/tool-version observation (all same before final doc edits).
The narrow ~664px view kept neutral square controls and table-only horizontal
scrolling. Extended cross-browser/zoom checks remain separate. Raw logs never
became browser downloads. Final documentation edits may change current Python
test-scope hashes without invalidating the recorded run. The server was stopped.

Commands run from the repository root (scoped offline permission for AnyIO,
Node subprocesses and loopback bind after sandbox denials):

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python/test_gui_test_views.py tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a3-views-20261018
.venv/bin/python -m pytest tests/python/test_gui_system_tests_api.py tests/python/test_gui_test_views.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a3-protection-20261018
.venv/bin/python -m pytest tests/python/test_gui_research_browser_routes.py tests/python/test_gui_system_tests_api.py tests/python/test_gui_test_views.py tests/python/test_gui_static.py tests/python/test_gui_documents.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a3-reviewed-20261018
.venv/bin/python -m pytest tests/python/test_gui_documents.py tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a3-docs-20261019
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/system_tests_ui.test.mjs tests/javascript/comparison_form_ui.test.mjs tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
.venv/bin/python -u -m qte gui --port 8767
.venv/bin/ctest --preset dev --output-on-failure --output-junit /home/glsu6/Documents/qte/build/gui/jobs/13497d151c8741499c74d3a278b4f2c3/artifacts/junit.xml
.venv/bin/python -m pytest tests/python --junitxml=/home/glsu6/Documents/qte/build/gui/jobs/b79592fcd350431fb31d54c446a3f56b/artifacts/junit.xml --basetemp=/home/glsu6/Documents/qte/build/gui/jobs/b79592fcd350431fb31d54c446a3f56b/test-tmp -o cache_dir=/home/glsu6/Documents/qte/build/gui/jobs/b79592fcd350431fb31d54c446a3f56b/pytest-cache -o xfail_strict=true
git diff --check
```

Post-update documentation/static checks passed **24**. The final pytest/CTest
commands above were generated by the actual GUI supervisor, not
manually launched. No network, installs, Git history/remote changes, stale build
directory or outside-repository edits. Next offline increment requires no new
approval: G8b bounded benchmark jobs, with timing explicitly not trading merit
and the existing `hold` workload labeled a no-op, not buy-and-hold.

### October 17 G8a.2b acceptance

Added `test_jobs.py`, `test_worker.py` and two matching test modules. Integrated
only the private test kind into `jobs.py`/`job_worker.py`; `catalog.py` blocks its
raw artifacts. Added an HTTP rejection regression in `test_gui_job_api.py`.
Updated evidence, architecture, roadmap and this checkpoint. No engine or GUI
controls changed. Sol built isolated worker regressions; root integrated the
manager/worker and acceptance; Astra independently reviewed the final changes.

Coverage includes queue/idempotency/restart, forged success, request/freshness
changes, failed/skipped/missing/invalid JUnit, exact capped log bytes with continued
draining, cancel/timeout/owner loss, and reaping nested-session descendants even
when an exited leader leaves stdout open. End-preflight failure never reuses the
start identity or emits a misleading completion marker. Review found a fixture
PID readiness race; atomic publication fixed it without weakening assertions.
Final static review passed. Initial focused 61 and final focused **69** passed.

The ignored `build/g8a2b-acceptance-20261017.py` submits real `JobManager` jobs,
waits for completion, independently reads their evidence and closes the manager.
Unlike the previous harness, execution occurs inside the production supervisor.
Both full Python runs passed **1417** tests; the final record is listed above.
CTest passed **15/15** inside the supervisor and separately; build had no work.
All recorded identity groups stayed unchanged during these jobs; subsequent
documentation updates can change current Python test-scope identity. These
records are private and unregistered, not source-to-binary provenance proof.

Commands run from the repository root (worker argv also preserved in manifests):

```sh
.venv/bin/python -m pytest tests/python/test_gui_test_jobs.py tests/python/test_gui_test_worker.py tests/python/test_gui_jobs.py tests/python/test_gui_job_protection.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2b-manager-20261017b
.venv/bin/python -m pytest tests/python/test_gui_test_worker.py -q --basetemp=build/pytest-worker-strengthened-2
.venv/bin/python -m pytest tests/python/test_gui_test_jobs.py tests/python/test_gui_test_worker.py tests/python/test_gui_jobs.py tests/python/test_gui_job_protection.py tests/python/test_gui_job_api.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2b-reviewed-20261017
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python build/g8a2b-acceptance-20261017.py --runner ctest
.venv/bin/python build/g8a2b-acceptance-20261017.py --runner pytest
.venv/bin/python -m pytest tests/python/test_gui_documents.py tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2b-docs-20261017
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/comparison_form_ui.test.mjs tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
git diff --check
```

Final full Python took 101.22 seconds; post-update docs/static checks passed 23.
JavaScript initially hit sandbox `spawnSync .venv/bin/python EPERM`; scoped
offline retry passed **25**. Full Python/API checks used scoped local subprocess
permissions because prior sandbox AnyIO tests stalled. No network, installs,
outside-repository edits, Git history/remote operations or stale build/dev use.
No new approval is needed for the next offline G8a.3 increment. Before exposing
test names, raw logs or JUnit, implement its protection/disclosure boundary;
keep launch explicit and status honest about skips, freshness and partial output.

### October 16 G8a.2a acceptance

Added `test_preflight.py`, `test_snapshots.py`, `test_evidence_writer.py` and three
matching test modules. Updated evidence/architecture/roadmap/checkpoint docs.
Sol built preflight and Terra built the writer before reaching usage limits;
root integrated snapshots and completed real-runtime and race regressions.
Final Astra review passed after frozen-manifest ownership and pre-execution
no-follow launcher checks were fixed. No engine, scheduler or HTTP/UI changes.

Initial 58 tests passed, but actual preflight found the editable wheel mapping
uses `qte/_core...`, and actual CTest JSON includes `version`. Fixed both and
expanded coverage to **83 passing tests**: generated hooks/config rejection,
bounded discovery subprocess cleanup, new/changed files, immutable snapshots,
partial writes, post-marker mutations, path replacement and caller mutation.
An intermediate mock CTest fixture failed after real-runner validation tightened;
updated its package/runner mapping to the actual producer contract. No test was
removed or weakened. Writer checks retain corrupt/partial output for diagnosis.

The ignored one-off `build/g8a2a-acceptance-20261016.py` exercises actual preflight,
snapshot/recheck, the exact fixed command and writer/reader for each runner.
It is **not** the future bounded supervised GUI worker. Its final invocations
passed **1391 Python** and **15/15 CTest**, with all four during-run identity
groups unchanged. Earlier acceptance (1389 Python/15 CTest) also passed before
the last two review regressions. These private records were not registered in
the catalog. Documentation changes after capture may change current test-scope
matching; neither version nor hashes prove source-to-binary correspondence.

Commands run from the repository root (final harness-generated commands shown):

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/ctest --preset dev --show-only=json-v1
.venv/bin/python -m pytest tests/python/test_gui_test_snapshots.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2a-snapshots-20261016
.venv/bin/python -m pytest tests/python/test_gui_test_preflight.py tests/python/test_gui_test_evidence_writer.py tests/python/test_gui_test_snapshots.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2a-initial-20261016
.venv/bin/python -m pytest tests/python/test_gui_test_preflight.py tests/python/test_gui_test_evidence_writer.py tests/python/test_gui_test_snapshots.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2a-rootreview-20261016
.venv/bin/python -m pytest tests/python/test_gui_test_preflight.py tests/python/test_gui_test_evidence_writer.py tests/python/test_gui_test_snapshots.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2a-rootfixes-20261016
.venv/bin/python -m pytest tests/python/test_gui_test_preflight.py tests/python/test_gui_test_evidence_writer.py tests/python/test_gui_test_snapshots.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2a-review-regressions-20261016
.venv/bin/python build/g8a2a-acceptance-20261016.py --runner ctest
.venv/bin/python build/g8a2a-acceptance-20261016.py --runner pytest
.venv/bin/python -m pytest tests/python/test_gui_documents.py tests/python/test_gui_static.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a2a-docs-20261016
.venv/bin/ctest --preset dev --output-on-failure --output-junit /home/glsu6/Documents/qte/build/gui/jobs/35ec0a97d88f4f138450f9e4c8307270/artifacts/junit.xml
.venv/bin/python -m pytest tests/python --junitxml=/home/glsu6/Documents/qte/build/gui/jobs/2d3835ea3956410cbc2cfcd3bff9b984/artifacts/junit.xml --basetemp=/home/glsu6/Documents/qte/build/gui/jobs/2d3835ea3956410cbc2cfcd3bff9b984/test-tmp -o cache_dir=/home/glsu6/Documents/qte/build/gui/jobs/2d3835ea3956410cbc2cfcd3bff9b984/pytest-cache -o xfail_strict=true
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/comparison_form_ui.test.mjs tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
git diff --check
```

Full Python used scoped offline permission because prior sandbox AnyIO runs
stalled. JavaScript initially hit sandbox `spawnSync .venv/bin/python EPERM`;
scoped offline retry passed all **25**. Post-update documentation/static
regressions passed **23**. No network, installs, Git history/remote operations,
outside-repository edits or stale build/dev use. No approval is
needed to continue the next offline increment; retain the existing supervisor's
single-job/liveness/cancel/timeout cleanup, add bounded continuously drained logs,
and keep process results separate from test outcomes and provenance changes.
Do not expose raw test output via generic downloads without its protection gate.

### October 16 G8a.1 foundation checkpoint

New `junit.py`, `test_identity.py`, `test_evidence.py` and three matching Python
test modules implement strict producer parsing, bounded explicit-file capture,
separate source/test/binary/config matches, exact four-file verification and
fixed command descriptions. They do not execute jobs, write artifacts, register
catalog formats or add HTTP/browser capabilities. The
[contract](gui-test-evidence.md) records the next small increments.

Astra provided architecture and partial implementation review; Terra delivered
identity capture (17 tests); Sol delivered the parser before reaching usage
limits. Root integrated the verifier and completed real-producer regressions.
Review caught duplicate manifest paths, unavailable groups comparing equal,
empty pytest collection classnames and outcome/provenance conflation; all fixed.
Actual installed-tool tests then exposed multi-error event counting and
module-level skip/exit-5 behavior, now covered. Root also replaced a broad XML
namespace regex with declaration events so harmless logged text is not rejected.
Final independent review of these last fixes subsequently passed on resumption.

Focused iterations initially failed on root's synthetic CTest fixtures (an
unsupported errors attribute, then run status on a failed case). Corrected the
fixtures to actual producer output, without weakening parser checks. Installed
pytest/CTest regressions generate intentional failures/skips in disposable
repository-local test fixtures; those expected child exits are not QTE suite
failures. Baseline CTest's relative `--output-junit build/...` landed under
`build/local-dev/build/`; fixed command descriptions therefore use absolute
job-local destinations. No stale build/dev directory was used.

Commands executed from the repository root:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure --output-junit build/g8a-ctest-baseline-20261016.xml
.venv/bin/python -m pytest tests/python/test_gui_test_evidence.py tests/python/test_gui_test_identity.py tests/python/test_gui_junit.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a1-focused-20261016
.venv/bin/python -m pytest tests/python/test_gui_test_evidence.py tests/python/test_gui_test_identity.py tests/python/test_gui_junit.py -q --maxfail=1 --tb=short --basetemp=build/test-g8a1-reviewed-20261016
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g8a1-full-20261016 --junitxml=build/gui-g8a1-full-20261016.xml
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/comparison_form_ui.test.mjs tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
git diff --check
```

All writes are repository-local; existing dirty work is preserved. No installs,
external requests, Git history/remote operations, browser or engine changes.
Foundation results: **1308 Python passed in 92.40s**, **111 focused passed in 1.27s**,
**25 JS**, **15/15 CTest**, build no work and diff check clean. The new parser
also accepted actual earlier full-suite XML (1197 cases) and the native baseline
(15 executable cases). All owned test commands have exited; nothing is running.
The final Astra review passed before G8a.2a began, including producer-count,
namespace-text and module-skip fixes. Existing test evidence is not yet served.

### October 15 G7a.3c acceptance

Research now offers New comparison for both fixed workflows. Metadata reuses the
canonical registry/defaults, including every candidate/experiment field and exact
integer strings. The paged chooser displays validation names/IDs only; evidence
declarations are explicit. Preview, fixed launch, edit invalidation, detached-view
guards, single-flight submission and frozen same-key retry use existing jobs.
Base/stressed costs are visibly separated; advanced controls are collapsed.

Sol implemented the form, Terra added seven DOM state-machine tests, root added
metadata/API tests plus two review regressions and integrated routes/navigation.
Astra reviewed read-only and found two issues, both fixed: native constraints
could strand invalid collapsed controls, and detached handlers needed entry
guards. Root verified error opening/focus in an actual browser. Both build
agents reached usage limits after delivering their files/test reports; no edits
were lost. Existing user changes, including untracked `.codex/`, were preserved.

Commands from repository root:

```sh
.venv/bin/python -m pytest tests/python/test_gui_comparison_forms.py tests/python/test_gui_comparison_jobs.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a3c-metadata-20261015
.venv/bin/python -m pytest tests/python/test_gui_comparison_forms.py::test_form_metadata_is_authenticated_no_store_and_creates_no_job_or_catalog -vv -o faulthandler_timeout=10 --basetemp=build/test-g7a3c-diagnose-20261015
.venv/bin/python -m pytest tests/python/test_gui_comparison_forms.py tests/python/test_gui_comparison_jobs.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a3c-metadata-approved-20261015
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a3c-full-20261015 --junitxml=build/gui-g7a3c-full-20261015.xml
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/comparison_form_ui.test.mjs tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
.venv/bin/python build/g7a3c-browser-fixture.py
.venv/bin/python -m qte gui --repo-root /home/glsu6/Documents/qte --port 8766
git diff --check
```

First sandbox HTTP run stalled after seven passes; diagnostic stack showed an
idle AnyIO worker/event loop. Both were interrupted, not counted as passes.
Scoped offline retries passed: focused **16**, full **1197 in 92.64s** (no
failures/errors/skips), **25 JS**, **15/15 CTest**, build no work. No weakened
tests, installs, external requests or Git history/remote changes.

Browser acceptance: ID/name-only chooser; SMA preview/launch/success/register;
three-window SMA+trend experiment with uint64-max seed; edit invalidates preview;
cleared/collapsed seed opens with focus/error; explicit safe-window selection;
base/stress results and final-test withholding. Both jobs succeeded. At 390×844,
form controls had no horizontal overflow; square neutral styling checked.
Extended zoom, other browsers and exhaustive keyboard audit are not claimed.
Browser empty-string fill initially left the default intact; actual keyboard
clear verified the validation behavior. No reveal or raw download occurred.
Synthetic fixture exports, three registered validations and two successful jobs
remain in ignored `build/` and the local catalog for reproducible inspection.

Changed this slice: comparison_forms.py, comparison_routes.py, app.py,
static/app.js, static/comparison_forms.js; new Python/JS form tests; comparison,
architecture, usage, roadmap, test-build and checkpoint docs. Earlier G7a changes
remain dirty and untouched except the scoped integrations described above.

Resume G8a with a small evidence schema/parser slice: inspect existing System,
job runner, JUnit handling and benchmark script before adding classes. Separate
source freshness from binary freshness, failures from skips, and narrative notes
from machine records. Only fixed approved commands, unique repo-local output,
no implicit build/install or browser-supplied test code. G8b follows the G8a gate.

### October 15 G7a.3b acceptance

Added authenticated comparison preview and explicit submission, fixed `qte
compare` worker dispatch, snapshot-rehydrated authorization at acceptance,
dispatch and finalization, exact output correspondence, and role-aware
publication. Both workflows match direct CLI exports byte-for-byte; registration
stays explicit. Public jobs withhold input hashes, role identities and child
exit codes; failures/logs are generic. Safe result views remain available while
test/mixed content stays guarded. Source changes or late protection/disclosure
block execution/publication, including when valid output already exists.

Root implemented HTTP/manager/verifier/integration; Sol supplied worker and
lifecycle tests; Astra reviewed the completed source/tests. Review found a
completion-inventory hash disclosure: `complete.json` is now derived mixed
content without rewriting legacy exports. The first full suite caught legacy
oversized-request behavior changing (400 instead of 413). Fixed by isolating
comparison submissions at `POST /api/v1/comparisons/jobs`; ordinary `/api/v1/jobs`
keeps its original 4/16 KiB envelopes. No existing test was weakened.

Commands run from the repository root using existing tools and local artifacts:

```sh
.venv/bin/python -m pytest tests/python/test_gui_comparison_worker.py tests/python/test_gui_comparison_snapshots.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a3b-resume-worker-20261015
.venv/bin/python -m pytest tests/python/test_gui_comparison_jobs.py tests/python/test_gui_comparison_worker.py tests/python/test_gui_comparison_catalog.py tests/python/test_gui_comparison_artifacts.py tests/python/test_gui_jobs.py tests/python/test_gui_job_worker.py tests/python/test_gui_job_api.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a3b-integration-20261015
.venv/bin/python -m pytest tests/python/test_gui_comparison_lifecycle.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a3b-lifecycle
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a3b-full-20261015 --junitxml=build/gui-g7a3b-full-20261015.xml
.venv/bin/python -m pytest tests/python/test_gui_comparison_jobs.py tests/python/test_gui_comparison_lifecycle.py tests/python/test_gui_inline_job_api.py tests/python/test_gui_job_api.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a3b-envelope-fix-20261015
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a3b-final-full-20261015 --junitxml=build/gui-g7a3b-final-full-20261015.xml
git diff --check
```

Results: worker/snapshot 22 pass; integration 98 pass; lifecycle 8 pass;
initial full 659 pass/1 failure, diagnosed above; post-fix focused 31 pass;
final full **1188 pass in 95.12s**, no failures/errors/skips. Build no work,
15/15 CTest and 16 JS pass; diff clean. Sol's initial sandbox test attempt could
not obtain writable temp storage; repository-local approved retry passed.
No browser changes/smoke claim, installs, network or Git history/remote actions.

Changed implementation: comparison_jobs.py, comparison_routes.py,
comparison_snapshots.py, comparison_artifacts.py, app.py, job_routes.py, jobs.py,
job_worker.py. Added job/worker/lifecycle tests; strengthened reader/catalog
protection tests and updated comparison/job/architecture/usage/checkpoint docs.
Earlier dirty work was preserved. No blocker or new approval is needed for
G7a.3c: fixed registry-backed forms, validation-ID windows/evidence, explicit
preview then launch, stale-response/double-submit handling and real browser
acceptance. Use the dedicated comparison endpoints; existing job views handle
status/cancel/logs. Do not add network access or arbitrary strategy imports.

### October 14 G7a.3a acceptance

Private validation-ID capture now freezes ordered source bytes/configuration,
checks validation-job correspondence and enforces separate safe/final-test
authorization. Final-test reservation is append-only protection, never reveal;
missing history remains unknown. V3 snapshots have exact inventories, pinned
hashes, canonical reconstruction and retained/non-reusable partial writes.
V1/v2 behavior is unchanged. Both workflows run successfully through the existing
CLI exporter using their captured effective configs. No new HTTP/job kind,
worker, form or browser capability is enabled by this foundation slice.

Astra review found case-sensitive extension validation; corrected to match
existing CSV/JSON capture. Shared validation configuration no longer permanently
propagates holdout protection into unrelated raw data; an availability-only
catalog guard preserves generic withholding until explicit config disclosure.
Existing protection/reveal records remain untouched. Regressions include fresh
catalogs, derived-summary protection, damaged history, re-exported GUI sources,
uppercase filenames, duplicates, bounds, tampering and failed writes. Root wrote
tests after Terra's usage limit; Astra's final read-only review found no blocker.

Exact commands (repository root, existing venv, offline, scoped local approval):

```sh
.venv/bin/python -m pytest tests/python/test_gui_comparison_inputs.py tests/python/test_gui_comparison_snapshots.py tests/python/test_gui_inline_inputs.py tests/python/test_gui_snapshot_compatibility.py tests/python/test_gui_datasets.py tests/python/test_gui_job_protection.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a3-focused-20261014
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a3-full-20261014 --junitxml=build/gui-g7a3-full-20261014.xml
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
git diff --check
```

Results: 96 focused passed (8.21s); build no work; 1169 Python passed (84.34s),
zero failures/errors/skips; 15/15 CTest and 16 JavaScript passed; diff clean.
No browser code changed or new browser smoke claimed. No network, installs,
Git history operations or remote access. Changes: comparison_inputs.py,
comparison_snapshots.py, job_inputs.py, catalog.py, two new matching Python test
modules, and comparison/architecture/roadmap/checkpoint docs; earlier dirty work
was preserved.

Next G7a.3b: fixed comparison_run preview/acceptance/worker publication. Recover
private authorization records from the verified snapshot, recheck roles at
acceptance/dispatch/finalization, pin current source identity and prove output
correspondence before publication. Do not serialize raw inputs, test hashes,
roles/outcomes or captured dataclass reprs through generic job DTOs/logs. Intentional
final-test use needs role-aware publication, not ordinary whole-output taint.
Browser launch and its acceptance tests follow. G8 is still pending; no additional
dependency or external approval is needed for the next offline integration gate.

### October 14 G7a.2b acceptance

Added fixed comparison detail/candidate/window/scenario projections and routes.
They never execute strategies or expose final-test rows, global promotion
eligibility, aggregate provenance, or arbitrary exception strings. Known
protected training inputs and malformed disclosure history fail closed. Legacy
fields absent from its report remain explicitly not recorded.

Astra reviewed three defects (test-dependent eligibility, damaged disclosure
history, fabricated legacy universe); fixes and regressions are in place. Its
final detail-status recommendation is also applied. Root integrated the sparse
browser module; recursive record details use the actual app formatter. Tests run:

```sh
.venv/bin/python -m pytest tests/python/test_gui_comparison_views.py tests/python/test_gui_comparison_catalog.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a2b-reviewed-20261014
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a2b-final-full-20261014 --junitxml=build/gui-g7a2b-final-full-20261014.xml
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/comparison_ui.test.mjs tests/javascript/research_ui.test.mjs
git diff --check
```

Results: 33 focused passed; build no work; 15/15 CTest passed; final 1117 Python
passed in 79.90s, zero failures/errors/skips; 16 JavaScript passed; diff clean.
Initial Node invocation was blocked spawning Python by the read-only sandbox;
the scoped retry passed without installs. Earlier backend-only suite: 1116 pass.

Local browser smoke used `.venv/bin/python -m qte gui --repo-root
/home/glsu6/Documents/qte --port 8766`: login, explicit registration of existing
`build/g7a-demo-experiment-20261013`, candidates/complete parameters, safe-window
selection before metrics, validation base/stress results and square-cornered
neutral layout passed. Raw report/test input remained unavailable; no reveal,
download or external request occurred. Browser startup initially stalled; later
checks completed normally. Logged out, closed test tab and stopped owned server.
Registration remains in the local disposable catalog; original export unchanged.
Extended accessibility/zoom coverage is not claimed. Astra's final review found
no remaining blocker. This accepts views only, not all of G7a or G8.

Launch boundary reviewed by Astra: fixed comparison_run kind; validation-ID
inputs privately resolved to checksum-matching allowed sources; explicit
caller historical/synthetic evidence (validation is not authenticity proof).
Use a separate v3 ordered multi-input snapshot; preserve v1/v2. Final-test
computation must not require disclosure or leak test facts through preview,
job DTOs, errors or logs. Reject known revealed final-test inputs; protect test
identities before dispatch and recheck at dispatch/finalization. Verify output
configuration, bytes, roles and source against the snapshot before publication.
Ordinary whole-output taint cannot be reused for intentional final-test inputs.

G8 boundary reviewed: separate completed evidence from test outcome; fixed
CTest/pytest/bounded benchmark commands, no arbitrary selectors or builds.
Failed test evidence needs an explicit verified publication gate, not blanket
permission for failed research outputs. Record source/test/binary inventories;
source-to-binary correspondence is unknown without a build receipt. Zero/all
skipped tests are not pass; malformed/missing JUnit stays incomplete. Benchmark
hold means no-op, default 10,000 bars and GUI maximum 100,000; no network.

### October 14 G7a.2a acceptance

Saved Sol/root implementation was present in a clean worktree on resume. Root
finished verifier corrections/tests; Sol `g7a2_sol_resume_tests` supplied a
deterministic round-trip fixture and Astra `g7a2_astra_resume_review` approved
the final read-only review. No engine, form, route, job or browser code changed.
Older exports remain read-only, including their advisory unsupported-reader
marker. Current source mismatch is not an integrity failure.

Verify all role/file mappings before classifying bytes. Test inputs/scenarios
and mixed metadata require explicit disclosure; invalid/incomplete exports
remain entirely guarded. If a nominally safe input is already protected by the
disclosure journal, protect all derivatives too. Generic catalog status keeps
selection, holdout evaluation and scenario outcome unknown until role-filtered
views exist; no report metrics or failure reasons are public metadata.

Checks cover exact inventory/hashes, configuration and input/window contracts,
stored engine settings, protocol/scenario mappings, CSV types/timestamps/order
links and final-equity consistency. Registration never replays strategies.
Review found and fixed malformed engine snapshots and inconsistent final equity.
An initial size cap was corrected to preserve supported 32-candidate/24-window
exports: the actual maximum-slate regression has 4,448 files and a 1,385,520-byte
report. Configuration/marker inputs retain 1 MiB limits; report artifacts use
the existing 16 MiB file/256 MiB aggregate limits.

Final commands (existing venv, repository-local outputs, scoped approval for the
read-only workspace; no installs, network or Git history changes):

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python/test_gui_comparison_artifacts.py tests/python/test_gui_comparison_catalog.py tests/python/test_comparison_cli.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a2-final-focused-20261014
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a2-final-full-20261014 --junitxml=build/gui-g7a2-final-full-20261014.xml
git diff --check
```

Results: build no work; **15/15 CTest passed**; **50 focused passed, 8.61s**;
**1095 Python passed, 77.41s**, zero failures/errors/skips. Both October 13 demo
exports were independently read successfully without rewriting them. The
selected fixture initially failed its required-regime gate as designed; tests
now explicitly cover both matching-regime pass and mismatched-regime failure,
with test files guarded in both cases. No research rule was relaxed.
No browser code changed; the prior 11-JS-test/local-browser gate remains history,
not a fresh browser acceptance claim. Next: role-filtered comparison projections,
then the separately gated multi-input launch contract.

### October 13 G6b acceptance

- Fixed `download_alpaca` form/preview/snapshot/worker; explicit startup and
  credential gates before submission/dispatch; no credentials in persisted
  artifacts/logs/argv. Pure GETs and preview never download.
- Bounded acquisition, exact request/cache/page/merged-bar verification,
  inherited disclosure protections and authenticated lineage view.
- Full command `.venv/bin/python -m pytest tests/python -q --maxfail=1
  --tb=short --basetemp=build/test-gui-g6b-confirm-20261013
  --junitxml=build/gui-g6b-confirm-20261013.xml`: **1009 passed, 68.29s**.
  Initial full run reached 761 passes then failed an outdated mocked launcher
  signature; updated it to assert both default-disabled and explicit-enabled
  forwarding. The artifact-kind test also caught a misplaced branch, now fixed.
- `PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev`: no work;
  `PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure`:
  **15/15 passed**. `git diff --check`: clean.
- `/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
  --test tests/javascript/research_ui.test.mjs`: **11 passed**.
- Astra reviewed acquisition bounds, strict JSON, FIFO/symlink safety,
  credential gates/env isolation and provenance. Seven adversarial safety
  regressions are included in the full suite. Raw and canonical snapshot hashes
  are both checked; a fixture was corrected without removing provenance checks.
- Actual local browser: authenticated, opened Alpaca form, entered SPY/1Hour/
  explicit iex/UTC window/action declaration, preview succeeded and download
  stayed disabled. No provider/broker requests, downloads, installs or Git writes.
  Isolated state: `build/gui-browser-g6b-20261013`, port 8770. Temporary tab
  closed and server stopped; no credentials/access codes recorded here.
- Astra final review closed with no blocking findings after confirming both
  provenance hashes. The full suite includes the seven safety regressions.
- Final closure command `.venv/bin/python -m pytest
  tests/python/test_download_capture_safety.py tests/python/test_gui_download_jobs.py
  tests/python/test_gui_download_api.py tests/python/test_gui_server.py -q
  --basetemp=build/test-gui-g6b-closure-20261013`: **30 passed, 1.04s**.
- Next: G7a declarative comparison bridge, then G7b/G8/G9. Real acquisition
  needs separate authorization, credentials and entitlement/action-free checks.

### October 13 G7a.1 acceptance

Root owns CLI integration, the unsupported comparison artifact guard,
documentation registry, examples, integration/export tests and docs. Sol
`g7a_sol_config` finished `comparison_config.py` and its tests; Terra
`g7a_terra_export` saved `comparison_export.py`/initial tests before reaching
usage limit. Root completed failure/provenance/export regressions. Astra
`g6b_astra_review_oct12` approved the independent review without blocking
findings and passed 37 targeted tests before the four final regressions. No GUI job,
account or engine change is included.

CLI evidence is caller-declared and tied to raw SHA-256, not authenticated
historical status. Both existing APIs are preserved: the exact four-scenario
SMA comparison and frozen-slate `run_experiment`. Synthetic examples cannot
promote candidates. Registry identities include family, symbol and parameters.
Outputs use indexed paths and explicit scenario mapping; failures are retained.
The catalog refuses to interpret this new format as a lab until holdout-aware
readers exist. Original user work remains untouched.

Commands run from the repository root, using the existing `.venv`:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python/test_comparison_config.py tests/python/test_comparison_export.py tests/python/test_comparison_cli.py -q --maxfail=1 --tb=short --basetemp=build/test-g7a-reviewed-20261013
.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short --basetemp=build/test-g7a-full-20261013 --junitxml=build/gui-g7a-full-20261013.xml
git diff --check
.venv/bin/python -m qte compare --config examples/comparisons/sma.json --output build/g7a-demo-sma-20261013
.venv/bin/python -m qte compare --config examples/comparisons/experiment.json --output build/g7a-demo-experiment-20261013
```

Results: native build no work; **15/15 CTest passed**; final comparison gate
**41 passed, 1.08s**; full Python **1050 passed, 68.322s**, zero failures/errors/
skips in the saved JUnit report. Full Python ran with scoped local execution
after earlier sandbox async-test stalls; no network/dependencies were used.
The focused CLI tests invoke both new example commands and verify recursive
checksums, retained failed trials, exact API parity, repeatability, source-change
failure and fail-closed catalog protection. No browser code changed in G7a.1;
the last browser gate remains G6b's 11 JS tests/local browser check.
Both explicit CLI demos succeeded in ignored `build/` directories: four SMA
scenarios and eight experiment train/validation scenarios. Synthetic evidence
selects nobody and leaves the final test unevaluated. `git diff --check` is clean.
Do not mark all of G7a complete: comparison GUI launching/views are still pending.

### Remaining G7a implementation seam

Sol inspected `experiments.py` (`Candidate`, `Evidence`, `ResearchWindow`,
`ExperimentConfig`, `run_experiment`) and `research.py`
(`compare_moving_average`). Reuse their chronological/universe/currency/interval
checks, frozen slate and validation-only selection; never import browser-named
factories. The shared preparation/CLI slice now exists with exact two-split SMA
parity; do not mislabel historical exports as synthetic labs. The dedicated
`gui/comparison_artifacts.py` verifier/catalog gate is now accepted. Next add
multi-source snapshots and GUI jobs/forms; fixed train/validation views are now
accepted. Keep malformed/incomplete
inputs fully guarded and holdout outcomes/reasons out of generic metadata.
Current bounds: 1–32 allowlisted candidates, 3–24 ordered experiment windows,
one final test window, existing engine/cost/selection defaults.
Before implementation settle historical evidence declaration vs validation-ID
selection. Validation-ID-only GUI is the safer recommendation, but existing
G6a reuses original paths rather than immutable registry input bytes. Resolve
that real boundary instead of bypassing reserved paths. Holdout execution must
not implicitly disclose test values or require a reveal merely to compute;
do not reuse ordinary input authorization without a protocol-aware contract.

Repository `/home/glsu6/Documents/qte`; native compiler `/usr/bin/g++`, CMake
`build/local-dev`, Python only `.venv`. SSL and scoped RPM verification passed
after reboot; original failure retained in `docs/gui-g0-preflight.md`.
Baseline: 15/15 CTest executables, 419 Python tests. Approved hash-checked wheel
installation completed for all 12 packages in requirements-gui-test.lock using
the existing venv, no cache and repository-local TMPDIR. Sandbox index access
failed; scoped network approval allowed the install. No system package changed.

## File ownership during active build

- October 12 G6b: Sol `g6b_sol_plan_oct12` owns bounded download wrapper/verifier,
  adapter hardening and form compiler; Terra `g6a_terra_diagnose_oct12` owns
  capture/snapshot/runner gates; root owns catalog/API/browser/CLI integration.
  Astra `g6b_astra_review_oct12` reviews independently. No real network tests.
  G6a command `.venv/bin/python -m pytest tests/python -q --maxfail=1 --tb=short
  --basetemp=build/test-gui-g6a-resume-confirm-20261012
  --junitxml=build/gui-g6a-resume-confirm-20261012.xml`: **979 passed in 70.24s**.
  Sandbox run stalled and was interrupted; scoped execution completed normally.
- October 9 G6a: Sol `g6_sol_validation` owns shared `data_validation.py`,
  `research_config.py`, CLI/forms and corresponding new unit tests; Terra
  `g6_terra_runner` finished capture/snapshot/manager/worker changes and tests.
  Root owns dataset catalog/routes/views/browser, API tests and documentation.
  Astra `g6_astra_review` reviews independently. Previous assignments are history.
- October 8 browser gate: Sol owns `static/research_forms.js` and its new static
  test; Terra owns `static/research_jobs.js`, its new static test and `app.css`.
  Root owns `app.js`/`app.py` integration, API tests, docs and real-control QA.
  Astra reviews independently. No validator, runner or financial changes.
- October 7 post-snapshot gate: Terra owns `jobs.py`, `job_worker.py` and
  manager/lifecycle tests. Sol owns new `job_outputs.py` and its strict fixed-lab
  verifier tests. Root owns `job_routes.py`/request shape parsing, new inline API
  tests and docs/full gates. Astra independently reviews. No browser/core changes.
- October 7 post-preview gate: Terra owns new `inline_inputs.py`, minimal
  `job_inputs.py` dispatch and `test_gui_inline_inputs.py`. Sol owns independent
  `test_gui_snapshot_compatibility.py` legacy golden tests. Root owns docs,
  snapshot-to-CLI integration tests and full gates. Astra reviews independently.
  No HTTP/manager/worker/core changes in this snapshot-only slice.
- October 7: G5b.2a source is saved. Root owns preview routes/app wiring,
  Catalog authorization, integration/protection tests and docs. Sol owns
  `research_forms.py`/unit tests; Terra owns `form_inputs.py`/unit tests (both
  source-quiet). Astra resumes independent review. Sol is doing read-only G5b.2b
  seam planning; no new launch/snapshot/worker changes before the preview gate.
  All older assignments below are historical.
- October 6: G5a is complete. Sol `g5b_sol_design_oct4` now owns
  `research_config.py`, `cli.py` and single-run preparation tests. Terra
  `g5a_terra_finish_oct4` owns CSV/Alpaca byte-loader seams, `strategy_lab.py`
  pure preparation and their new tests. Astra `g5a_astra_review_oct4` independently
  reviews G5b.1. Root owns docs/integration/full gates. No HTTP/runner/form changes
  are authorized in G5b.1; the older assignments below are historical.
- October 4 resume: earlier agents are no longer live. Terra task
  `g5a_terra_finish_oct4` owns the runner/input files and their three test files;
  Astra task `g5a_astra_review_oct4` is the independent read-only reviewer.
  Sol task `g5b_sol_design_oct4` is doing read-only G5b design, not implementation.
  The historical assignments below explain the saved work, not concurrent owners.
- Sol task `gui_sol_g1` (`gpt-5.6-sol`) saved the initial G5a runner; its later
  pending-initialization turn was interrupted. No current file ownership.
- Terra task `gui_terra_g1` (`gpt-5.6-terra`), now G5a:
  input helper/tests, `jobs.py`, `job_worker.py`, `test_gui_jobs.py` and
  `test_gui_job_worker.py`. Root explicitly transferred the remaining runner
  fixes after interrupting Sol; do not allow concurrent edits in these files.
- Root: `app.py`, request/DTO helpers, artifact API routes/tests, CLI/server,
  docs/checkpoints, integration and real-browser validation.
- Astra task `gui_astra_g1_review`: completed G1 through G4 implementation
  review and G5a design review. G5a implementation still requires a gate.
  Do not assume an errored/interrupted review passed.

## Agreed G1 interface

`qte.gui.app.create_app(repository: Path, *, port=8765, code_sink=print,
clock=time.monotonic) -> Starlette`. Packaged fixed assets: `index.html`,
`app.css`, `app.js`. Header `X-QTE-Token`, HttpOnly cookie `qte_session`.
Auth routes `/api/v1/session` POST/GET/DELETE; library/capability DTOs in
`docs/gui-contracts/v1/`. Zero-radius neutral four-section UI; research/data/
system workflows unavailable until their milestone. No arbitrary static mounts.

## Next actions

Current resume target (October 14): implement G7a immutable multi-input launch
using the reviewed boundary above, then G8a test evidence and G8b benchmarks,
OFFLINE. G6a/G6b and G7a CLI/catalog/views gates are accepted. G7b macro/router
and G9 connection inspection remain queued. Extended accessibility/200% zoom
and exhaustive browser lifecycle acceptance remain pending. Use
`docs/test-build-guide.md` for data flow and strategy development.
No external calls or dependency installs without the applicable approval. The numbered list below
is milestone history, not a request to restart completed work.

### G6a checkpoint — October 9

- Added data-only `qte validate-data` and GUI Data → Validate local dataset;
  CSV mappings/saved Alpaca use existing adapter preflight. Validation never
  constructs a strategy or runs the engine. Fixed job, snapshots, explicit
  registration and inherited disclosure protection reuse the current framework.
- Export `dataset_validation_v1`: unique wrapper, configuration, summary,
  captured input and completion hashes. Exact canonical replay, strict JSON,
  historical source identity, full input/config/source binding at worker exit.
- Dataset view reuses the matching original source with `expected_sha256` pinned
  in run preview/snapshot. No reserved-path bypass. Moved/deleted/changed sources
  must be validated again; immutable raw-byte replay by registry ID is deferred.
- New files: `python/qte/data_validation.py`, GUI `dataset_routes.py`,
  `dataset_views.py`, `static/datasets.js`, four `test_*dataset*.py` modules.
  Updated CLI/config, forms/capture/snapshots/worker/catalog and browser routing.
  Existing G5b.3 untracked files and user changes remain preserved.
- Full command `.venv/bin/python -m pytest tests/python -q
  --basetemp=build/test-gui-g6a-final-20261009`: **970 passed, 73.78s**.
  Earlier full run: 969 passed/1 static navigation assertion failed; preserved
  explicit research navigation and added dataset branch/assertion, then reran.
  Additional selection regression command `.venv/bin/python -m pytest
  tests/python/test_gui_datasets.py -q
  --basetemp=build/test-gui-g6a-selection-final-20261009`: **6 passed, 1.76s**.
- `/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
  --test tests/javascript/research_ui.test.mjs`: **9 passed**. Existing Node only;
  sandbox subprocess EPERM required scoped execution. No runtime installed.
- `PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev`: passed, no work.
  `PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure`: exit 135
  inside and outside sandbox; direct packaged `ctest --version` also exit 135.
  Ran each `build/local-dev/tests/cpp/qte_<name>_tests` directly for names
  bar, domain_types, price_instrument, order, fill, open_only_execution, position,
  portfolio, trade, risk, strategy, dataset, schedule, backtest, analytics:
  **all 15 passed**. `/usr/bin/g++ --version` reports GCC 16.2.1.
- Read-only CTest diagnosis: installed CMake 4.4.3 binary size 22614984 matches
  RECORD but reading fails EIO at byte 6946816. Expected SHA256
  `10e5d9bad3af7c3cfb59a4e906847a719e4d550299c4608e26e0d32e3cea915b`.
  Sibling cmake/cpack hashes match RECORD and run. Unreadable extent on Btrfs
  explains SIGBUS; underlying storage cause is not established. User subsequently
  approved same-version repair: downloaded and verified the exact CMake 4.4.3
  wheel (4167 RECORD entries, CTest hash above), then installed with
  `TMPDIR="$PWD/build" PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
  .venv/bin/python -m pip install --force-reinstall --no-deps --no-index
  --no-cache-dir build/cmake-repair-20261009/cmake-4.4.3-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`.
  Wheel SHA256 `bae3c4954623ec4d62e62c70443f0da7988b733111c2871fcc6a31ead5137e20`.
  CTest checksum/version and pip check passed; build/local-dev regenerated and
  built successfully; `ctest --preset dev --output-on-failure` passed **15/15**.
  Repair predates October 12's no-internet instruction. No system packages changed.
  Recommend user/admin filesystem health inspection if EIO recurs.
- A one-time task heartbeat `resume-qte-milestones` was scheduled and fired
  October 9 at 03:00 America/Los_Angeles (PDT, 10:00 UTC). It retained all approval
  boundaries. This is not an ongoing nightly schedule.
- Browser QA used isolated `build/gui-browser-g6a-20261008`, loopback port 8769.
  Validation preview→launch→success→registration→pinned-source SMA run→success→
  registration→result chart verified. Server session 26871 stopped cleanly and
  temporary tab closed. No real data, provider calls or persistent QA server.
- Astra final review passed after malformed/deep JSON and regular-file capture
  race fixes. Sol's final focused unit gate passed 14 tests; final JS gate 9 passed.
- The later Oct9 full run `--basetemp=build/test-gui-g6a-reviewed-20261009`
  showed one failure then stalled; this was NOT a passing run. A subsequent
  repair-era Python invocation was aborted. Reproduce rather than dismiss it.
  October 12 fresh command `.venv/bin/python -m pytest tests/python -q
  --maxfail=1 --tb=short --basetemp=build/test-gui-g6a-resume-20261012
  --junitxml=build/gui-g6a-resume-20261012.xml` is in progress.
  Extended zoom/narrow-screen acceptance remains separate.

1. DONE: approved locked packages installed; imports and `pip check` passed.
   Optional extras, CLI integration and loopback launcher are saved. No reinstall
   needed. All earlier agents stopped; new Sol/Terra G1 fix agents and independent
   Astra review started on resume. Their names have `_g1`/`_g1_review` suffixes.
2. G1 fixes complete: original 21-pass/1-fail unsafe-Markdown test now passes;
   FileResponse/ASGITransport hang resolved by fixed static-byte responses.
   Unicode credentials, unavailable document rows/DTO, body bounds, framing,
   anchor/skip links, file mutation/FIFO and logout feedback have regressions.
3. G1 independent Astra review has no remaining blockers. Full Python suite:
   `.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g1-gate-20260926`
   => 449 passed in 10.38s; scoped suite 30 passed. `git diff --check` passed.
   Browser sign-in, literal search, docs, metadata, skip link, restart invalidation
   and 390px layout checked using existing in-app browser. Explicit 200% browser
   zoom could not be established with the available shortcuts; remains manual QA.
4. DONE: G2 explicit SQLite artifact registry and strict
   legacy readers with durable holdout disclosure outside the rebuildable index.
   Code and UI are present; 60 GUI tests passed before the latest added hardening.
   Sol's latest core suite: 27 passed. Root API tests cover explicit registration,
   auth, decimal-string fields, downloads, invalid/disappeared roots and disclosure.
   Full Python command `.venv/bin/python -m pytest tests/python -q
   --basetemp=build/test-gui-g2-full-20260926` passed 490 tests in 10.22s with
   scoped execution approval. This predates the final three review fixes below.
   Real old/new labs and single export verify read-only (3093/3309/6 files); no
   actual research root has been registered or revealed.
   Final Astra findings fixed/re-reviewed: unsafe new entries no longer crash the
   catalog; partial disclosure has a separate remaining-disclosure flag;
   refreshed protected hashes inherit protection when copied/repacked.
   Protective classification can be persisted by verification; this never records
   a reveal, launches work, or modifies the research export.
   Final command `.venv/bin/python -m pytest tests/python -q
   --basetemp=build/test-gui-g2-final-20260927` => 494 passed in 10.72s (approved
   scoped execution); native build and 15/15 CTest still pass. Real browser tests
   verified typed disclosure and old-file available/new-file blocked after a
   later addition, using isolated synthetic fixtures only. Astra passes G2.
5. G1 through G4 review completed; NOW G5a bounded local research job runner.
   Never mutate legacy artifacts or change engine accounting/execution. Old
   missing fields remain explicitly not recorded. G5 implementation is next.
6. Update this log with exact commands/results, outstanding review findings,
   running process IDs/ports (no access codes), and next file/action at each gate
   and before stopping. Log checkpoints are not a scheduled automatic restart.
7. After G3, continue G4 lab comparison and G5a/G5b bounded local research jobs
   and forms, with the same tests/review gates. No network/account jobs.

Resume validation: native build succeeded (`PATH="$PWD/.venv/bin:$PATH" cmake
--build --preset dev`); `ctest --preset dev --output-on-failure` with that PATH
passed 15/15. `test_gui_server.py` passes 11 tests. Root extracted shared bounded
JSON/pagination and exact integer DTO helpers for G2, with additional regressions.

The isolated QA server used `build/gui-browser-20260926/repository` (not the
user's main catalog), on 127.0.0.1:8765 with explicit local-bind approval. Session
70852 was stopped; replacement session 17614 was stopped at this checkpoint.
No GUI or compute process is intentionally left running. Restart for further
browser QA; its fresh code must be read from the terminal, never this file.
User's saved lab/reference outputs remain unregistered and untouched.

Sandbox diagnostic: standalone asyncio thread wakeup hangs in the restricted
sandbox but returns normally with scoped escalation. G2 API/full Python suites
therefore run with user-approved scoped pytest execution, not changed dependencies
or weakened tests. Native build and 15/15 CTest tests passed again on G2 review.

## G3a resume details — 2026-09-27

Implemented files: `python/qte/gui/run_views.py`, `charts.py`, run routes in
`artifact_routes.py`, static `app.js`/`app.css`, and tests
`test_gui_run_views.py`, `test_gui_charts.py`, `test_gui_run_api.py`,
`test_gui_static.py`. Documentation changed in `gui-usage.md`,
`gui-architecture.md`, `roadmap.md` and this log. Existing dirty core/binding/
strategy files were not edited in this continuation. No packages installed,
Git history/remote operations performed or broker/provider calls made.

- API: `/api/v1/runs/{id}` returns `{schema_version:1,run:...}`; `/series` returns
  `{schema_version:1,series:...}`; `/tables/{name}` uses a flat envelope with
  `columns,rows,total,offset,limit,warnings`. Paging is numeric bounded metadata;
  artifact integers (ns/IDs/counts/ordinals/config values) are decimal strings.
- Series supports event/sampled, max 2000 points and inclusive signed-int64
  `start_ns`/`end_ns`; duplicate times/ordinals preserved, sequence null for v1.
  Fixed intervals alone determine gaps; no invented exchange sessions.
- Every read uses Catalog authorization. Final snapshot/generation consistency
  rejects replacement of valid files/marker between component reads. Reversed
  source timestamps (even outside the selected range) and malformed data/
  sampling metadata fail explicitly. No metric/accounting reconstruction.
- Astra's navigation finding fixed locally: only verified/unblocked single runs
  auto-open. Errors retain catalog/reveal access. Full record detail is reachable
  from the first table column. Recorded sampling now renders instead of being
  mislabeled missing. SVG coordinates use BigInt time differences and scaled
  finite equity values; inclusive chart-range controls never resample metrics.
- Browser verified: reference default chart, exact fill record, sampled run
  event 9 points versus sampled 8, annualization policy and undefined metrics,
  range narrowed to 2 points without changing headline metrics, corrupt catalog
  diagnostics, inherited protected-manifest catalog fallback, and 390px viewport
  without page overflow. Viewport reset. Download bytes/header/auth are HTTP
  tested; native save-dialog flow and explicit 200% zoom remain manual QA gaps.
- Isolated fixtures: `build/reference`, `build/corrupt`, `build/unknown`, and new
  `build/sampled-reference` under the QA repository. The sampled manifest was
  copied to unknown/copied-manifest.json to test inherited protection. It is now
  intentionally blocked in that QA catalog; do not clear its disclosure history.
  No new reveal was made during this continuation. QA tab closed after checks.

Commands/results (repository root):

```sh
.venv/bin/python -m pytest tests/python/test_gui_run_api.py tests/python/test_gui_run_views.py tests/python/test_gui_charts.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-g3a-integration-20260927
# 29 passed before the additional generation/ordering regressions
.venv/bin/python -m pytest tests/python/test_gui_run_views.py tests/python/test_gui_static.py tests/python/test_gui_charts.py -q --basetemp=build/test-gui-g3a-read-stability-20260927
# 26 passed
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g3a-full-20260927
# 517 passed in 11.57s; scoped async-test execution approval
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
# native build succeeded, no work required
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# 15/15 passed, build/local-dev only
git diff --check
# passed
.venv/bin/python -m qte run --config examples/research-run.json --output build/gui-browser-20260926/repository/build/sampled-reference
.venv/bin/python -u -m qte gui --repo-root build/gui-browser-20260926/repository --port 8765
# isolated QA only; foreground server subsequently stopped
```

Immediate next action: resume Astra G3a review, not a dependency install or a
fresh rebuild of the project. Check chart reduction/ordering, generation guards,
DTO precision, catalog fallback and protected-content routes. Do not count source
substring tests as behavioral browser coverage. Then run the gate again if fixes
are needed and proceed to G3b using Sol for bindings plus separate Terra work.

G3b inspection already established existing owned C++ fields: equity sequence,
fill sequence/reference open, full order request/snapshot, order events,
trade episode IDs/times/sequences/quantities/direction/PnL, position mark sequence.
PositionSnapshot does **not** own average-entry/PnL/final cash; do not invent them.
Expose only read-only owned copies, with exact int64/uint64 and retained-lifetime
tests. Rebuild editable QTE locally without dependencies (`--no-index
--no-build-isolation --no-deps -e .`, cache disabled, TMPDIR inside build), then
test before G3c. Proposed G3c default: preserve v1 CLI/export bytes and make the
tagged richer export opt-in; explain/confirm the concrete additive interface in
the next implementation step. G4 lab views must filter mixed-role reports without
exposing test values; do not bypass current Catalog.read_file protection broadly.

## G3a gate cleared / G3b active — 2026-09-27

Resumed Astra review found one additional malformed-schema defect: a metric
missing `value` was accepted as verified but crashed the view. `artifacts.py`
now requires that key (explicit null remains valid); actual-engine HTTP regression
in `test_gui_run_api.py` verifies invalid catalog status and sanitized 409.
Astra re-reviewed and reports no remaining G3a blockers.

`.venv/bin/python -m pytest tests/python -q
--basetemp=build/test-gui-g3a-review-final-20260927` => **518 passed in 12.44s**
with scoped async-test execution. Native build, **15/15 CTest**, and
`git diff --check` passed using the same commands as above. No server running.

Current assignments (resumed 2026-09-28): Sol owns `bindings/module.cpp` and new
`tests/python/test_gui_result_bindings.py`; Terra owns new
`docs/gui-result-bindings.md`; root owns public enum imports, integration/docs;
Astra reviews after implementation. Keep G3b minimal: existing result fields
only; no extra RunManifest/RiskLimits surface without a concrete consumer.
No core behavior or constructors added merely for tests. Existing dirty binding
work is preserved. Local editable rebuild allowed without dependency/network
installation; exact command/results will be logged at the next gate.

G3b source/docs now present, still testing: readonly fields/enums in
`bindings/module.cpp`; seven enums added to public `qte/__init__.py`;
`test_gui_result_bindings.py` is being strengthened for exact +1 ns, high
uint64 cancellation IDs and risk-rejected large quantities. Terra added
`docs/gui-result-bindings.md` with owned-list and sampling-sequence semantics.
The extension has now been rebuilt successfully using the existing native
`/usr/bin/g++` and venv:

```sh
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --no-index --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest tests/python/test_gui_result_bindings.py tests/python/test_bindings.py tests/python/test_lifecycle.py tests/python/test_research_baseline.py -q
# 20 focused tests passed
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g3b-full-20260928
# 523 passed in 11.04s; scoped async-test execution
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build succeeded and 15/15 CTest passed
git diff --check
# passed
```

Sol initially looked for system CTest and reported it absent; root verified the
existing `.venv/bin/ctest` and ran it successfully with the normal PATH above.
No new dependency was required. Astra G3b review subsequently passed, including
an independent 5-test binding run. The concrete additive G3c design in
`docs/gui-export-v2.md` also passed design review (not implementation review).

## G3c active — 2026-09-29

Usage interrupted Sol/Terra again after partial G3c work. On resuming, verified
`python/qte/research_export.py` and CLI opt-in changes exist; v2 frontend edits
are partial; exporter tests and GUI v2 reader/integration remain unfinished.
Sol owns exporter/CLI plus `test_research_export.py`; Terra owns static assets
and tests; root owns GUI validation/readers/API/tests and checkpoints. No source
or binary upgrade needed for G3c. Astra reviews implementation at its gate.

User requested a 3:24 AM Pacific continuation after the usage reset. No scheduled
automation was successfully created before that interruption. The user manually
resumed at 7:46 AM Pacific on September 29, so work continues now; no duplicate
future schedule was created. This log is a checkpoint, not an automatic timer.

V2 reader guardrails from Astra: wrapper presence cannot downgrade to v1 on
malformed/unknown schema; validate common projections/counts and lineage on one
stable generation without recomputing economics. Positions are signed int64
excluding INT64_MIN; fill/request quantities positive int64; episode cumulative
quantities uint64. Reversal fills can both close/open episodes; scale-in means
episode quantity need not equal its opening-fill quantity. Rejected invalid
requests must remain inspectable. Sequences repeat across collections and may
repeat within sampled carried values; no global uniqueness/contiguity claim.

### G3c integration and independent review — 2026-09-29

Exporter, strict v2 reader, owned-table API and frontend integration are saved.
Default v1 bytes remain unchanged; v2 is opt-in. Initial focused validation:

```sh
.venv/bin/python -m pytest tests/python/test_gui_export_v2.py tests/python/test_gui_run_views.py tests/python/test_research_export.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-g3c-views-20260929
# 51 passed
.venv/bin/python -m pytest tests/python/test_gui_run_api.py tests/python/test_gui_artifact_api.py -q --basetemp=build/test-gui-g3c-http-20260929
# 11 passed; scoped async thread-wakeup approval
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# build succeeded (no work required); 15/15 CTest passed
.venv/bin/python -m pytest tests/python/test_gui_export_v2.py tests/python/test_research_export.py tests/python/test_gui_run_views.py -q --basetemp=build/test-gui-g3c-hardening-20260929
# 36 passed before additional review regressions landed
```

Astra found missing structural checks for lifecycle completeness, sampling grid
completeness/staleness, status/reason compatibility and position mark bounds.
Root implemented these, plus final-position/open-episode quantity consistency.
Terra is adding rehashed corruption regressions and a full real-engine reversal /
carry reader fixture. Sol added four real-engine v2 HTTP parity/protection tests.

Gate remains **pending**: Astra also found a duplicated-fact protection bypass
between v1/v2 projections. Sol owns central conservative protection propagation
and persistence for equity, sampled equity, fills and orders, with both-direction
raw-download / interpreted-view regressions. Do not clear G3c until reviewed.

Browser QA uses only the isolated repository from G3a. New `build/owned-reference`
was generated with `--export-version 2` and explicitly registered there. Confirmed
overview sequence labeling, positions and full mark sequence, all 16 closed-trade
fields, empty open-trade headers, and lifecycle table/full record navigation.
No user research root registered or holdout revealed. Temporary QA server/tab
will be stopped/closed at the end of verification. G4 preflight is read-only;
no G4/G5 implementation has started yet.

### G3c gate passed / G4 active — 2026-09-29

The G3c implementation and independent Astra review are complete. Astra's final
independent suites passed 26 reader tests and 21 HTTP tests. Sol fixed and tested
durable bidirectional protection for all four proven legacy/owned projections;
raw downloads, interpreted views, later copies and explicit disclosure share the
classification. Terra's final real-engine reversal/carry fixture passes the full
reader, and all structural findings have regressions.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g3c-full-20260929
# 582 passed in 15.12s; scoped async thread-wakeup approval
.venv/bin/python -m pytest tests/python/test_gui_documents.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-g3c-docs-20260929
# 22 passed after documentation/registry update
git diff --check
# passed
```

Native build and 15/15 CTest passed earlier in this checkpoint; no C++ source
changed. One Sol broader test invocation overlapped Terra's staleness-fixture
edit and reported a failure; the final fixture and fresh full suite pass without
weakening tests. A transient Astra capacity error was retried successfully; final
review was actually completed, not inferred from a failed agent turn.

Browser additionally verified empty open trades and sampled source-event labels.
New isolated v2 artifact ID: `f9d1403bc8404a6fba4d9ec3a247b0f8`. QA server session
93562 stopped with Ctrl-C (130), browser tab closed. No active server remains.
Explicit 200% zoom and native download-save flow remain the earlier manual gaps.

G4 decisions are in `docs/gui-lab-views.md`. Train/validation structured views
only, even after raw disclosure; fixed allowlisted server projections of mixed
files, no arbitrary read/protection override, whole-generation guard. Candidate
and comparison order remains slate order; one selected window required for
comparison/macro/decisions, no stitched returns. Macro reference and availability
must both join the same safe window; decisions use close-time `(start,end]`.
Mixed-summary outcome is now `unknown` in generic catalog metadata so it cannot
leak held-out failure results. Raw file inventory names/counts remain metadata.

Root has saved Catalog.experiment_view, authenticated experiment routes, static
asset allowlisting and `test_gui_lab_api.py`. Sol is building the projector and
reader tests; Terra saved the modular frontend and 20 passing static tests.
Next: finish and integrate projector, run actual-engine API/security regressions,
measure cold/warm paging on the large saved lab read-only, browser-test isolated
lab controls, full suites, then Astra G4 review. Only then begin G5a/G5b.

### G4 gate passed — 2026-09-30

The user resumed manually. Git was clean before the current test-portability
and Unicode hardening changes; the user owns that history, and no Git operation
other than status/diff inspection was performed. Sol added portable 14/15-slate
fixtures; saved ignored artifacts are optional supplemental checks, not required
for a fresh checkout. Their original assertions remain when the artifacts exist.

Astra found a lone-surrogate JSON encoding crash. The shared artifact JSON
boundary now rejects invalid Unicode scalars in keys and nested strings while
preserving valid surrogate pairs. HTTP regressions cover source identity, nested
config and candidate parameters, including JSON-within-JSON. Initial new tests
expected projection 422 but central catalog validation correctly rejects earlier
with the existing unavailable-view 409 contract; separate new assertions cover
both boundaries and verify raw bytes stay blocked. No previous test was weakened.
Astra independently re-reviewed and cleared G4 (18 artifact + 16 HTTP tests,
plus its earlier 19 reader tests).

```sh
.venv/bin/python -m pytest tests/python/test_gui_lab_views.py tests/python/test_gui_lab_api.py tests/python/test_gui_lab_privacy.py tests/python/test_gui_lab_static.py -q --basetemp=build/test-gui-g4-integration-20260929
# recovered previous result: 38 passed in 20.86s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g4-full-20260930
# before Unicode fix: 622 passed in 38.85s
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_lab_api.py -q --basetemp=build/test-gui-g4-unicode-20260930
# initial new-test contract mismatch: 28 passed, 3 failed (explained above)
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_lab_api.py -q --basetemp=build/test-gui-g4-unicode-final-20260930
# 34 passed in 9.87s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g4-gate-20260930
# 632 passed in 42.31s; scoped async thread-wakeup access
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build succeeded (no work), 15/15 CTest passed, build/local-dev only
git diff --check
# passed
```

Browser QA: generated a two-clock, 15-entry isolated lab under
`build/gui-browser-20260930/repository`, deliberately injecting trend-candidate
failures through the QA-only helper while other candidates used the real engine.
Verified slate order, failure/no-trade/benchmark rows, full base/stress errors and
fields, actual nested cost context, explicit safe-window choice, macro paging and
source/vintage details, recorded regime decisions, daily-24h label and window
reset, catalog fallback and raw mixed files still unavailable. At 390px the page
width and scroll width both measured 375px; viewport override reset. No user
artifact was registered or revealed. QA server session 13560 stopped (130), tab
4 closed. Explicit 200% zoom and native save-file flow remain manual gaps.

Terra's read-only measurement of saved `build/strategy-lab-20260922`: 3,309 files,
uncached verifier 1.907s, warm detail 0.013s; first pages 0.013s candidates,
0.012s windows, 0.017s comparison, 0.032s macro, 0.062s decisions. All 6 macro
pages and 21 decision pages remained bounded; later-page totals 0.175s/1.185s.
Legacy 3,093-file lab verification: 1.821s. These are direct Python reader timings,
excluding HTTP/catalog journal overhead; OS disk-cache state is uncontrolled.

Next: write/review concrete G5a contract, then assign disjoint runner/input/API
work. No broker/provider jobs, no new dependencies, no automatic launches on read.

### G5a active — 2026-10-01

User resumed after usage interruption. Sol's first build turn errored before any
job implementation files existed; it was explicitly resumed, not counted as
completed. `docs/gui-jobs.md` records the concrete contract and Astra's design
clarifications: recheck protection at launch, inherit protected input lineage,
fresh CLI process, four source-identity checks, leave output nonexistent, retain
owner lock through supervisor cleanup, no inherited liveness write FD, no
pipe-backpressure deadlock, and no persisted-PID signaling on recovery.

Root added authenticated job routes, app lifespan hooks, catalog input/output
protection helpers and regression files. Sol owns durable scheduling/supervision;
Terra owns stable bounded input snapshots. **Integration is partial:** imports
may be temporarily unresolved until those assigned modules land. Do not report
G5a as usable or run G5b yet. The G4 gate above remains the last full green suite.

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_protection.py tests/python/test_gui_catalog.py tests/python/test_gui_documents.py -q --basetemp=build/test-gui-g5a-protection-20261001
# 33 passed in 0.67s before app job imports were connected
.venv/bin/python -m pytest tests/python/test_gui_requests.py -q --basetemp=build/test-gui-requests-20261001
# 7 passed in 0.15s before app job imports were connected
```

No GUI server/browser remains running. No dependencies, broker requests, Git
history/remote operations, or automatic artifact registrations were performed.

G5a integration update: all assigned modules now exist and imports resolve.
Real HTTP launch/dedup/invalid-config tests pass, with report/fill/equity/order
event/position byte parity against the normal v2 CLI. An initial API run had two
failures: job-first database initialization omitted the unrelated artifacts table,
so catalog input authorization could not query it. Catalog now treats a missing
artifact table as an empty lazy registry; the fresh run passes unchanged tests.

Astra's root-hook review found a publication race after CLI completion but before
derived-output protection, plus corrupt output-protection records. Catalog now
requires durable `succeeded` job state for all owned job artifact reads/registers;
the internal protection hook verifies records before and after writing. Missing,
failed, running or interrupted jobs stay blocked. Noncanonical repeated-separator
and dot-component paths are normalized before the guard and in saved registry
paths; old noncanonical rows are guarded too. Relevant corrupt/symlinked input
disclosure history fails closed. No implicit input or derived-output reveal.

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_api.py -q --basetemp=build/test-gui-g5a-api-first-20261001
# 2 passed / 2 failed, shared-table initialization defect, fixed above
.venv/bin/python -m pytest tests/python/test_gui_job_api.py -q --basetemp=build/test-gui-g5a-api-second-20261001
# 4 passed in 0.90s; scoped async thread-wakeup access
.venv/bin/python -m pytest tests/python/test_gui_job_protection.py tests/python/test_gui_catalog.py tests/python/test_gui_job_inputs.py -q --basetemp=build/test-gui-g5a-lineage-paths-20261001
# 53 passed
.venv/bin/python -m compileall -q python/qte/gui tests/python/test_gui_job_api.py tests/python/test_gui_job_protection.py
# passed at integration checkpoint; not a test-suite substitute
```

Next: Sol finishes scheduler/cancel/restart/storage/provenance regressions;
Terra additionally owns `test_gui_job_worker.py` for real-process watchdog,
timeout/cancellation/forced cleanup and environment/FD checks. Astra reviews
implementation independently. Root owns API/catalog integration and full gate.
Do not advance to forms or claim G5a complete on the four HTTP passes alone.

Further G5a checkpoint: root readers passed 46 tests and native build/15 CTest
passed again; no native source changed. Terra's real subprocess tests now assert
the required timeout state (`interrupted`), exposing the current worker's
`failed` result; this is an implementation fix pending Sol, not a test to weaken.
Owner EOF, cancellation, forced kill of a TERM-ignoring child and exception
cleanup are otherwise covered. Sol also owns Astra's pending fixes for per-call
SQLite/sidecar validation and lock release on initialization failure, plus
post-close submission rejection, signal cleanup and provenance reason accuracy.
No active QTE worker was found by exact executable/module process inspection.

Terra performed read-only G5b preflight (no implementation): keep config-path
jobs, add explicit inline-config origin, exact decimal-string web integers,
shared CLI validation, family-specific fields and a distinct fixed lab handler
with config-only snapshots. Root must settle/review the concrete G5b contract
only after G5a passes its implementation gate.

Coordination update: Sol stopped responding to follow-ups and had not saved the
review fixes. Interrupt returned `pending_init`, not an active editing turn.
Root reassigned the concrete remaining runner fixes/tests to approved Terra and
told Sol not to edit. Saved initial Sol work remains intact. No gate was inferred
from an uninitialized/errored turn.

### Resume — 2026-10-04

Recovered the October 1 files without Git changes. The saved close guards now
reject a closed manager before the opened shortcut and recheck inside submission
locking. All prior agent sessions are gone; fresh Terra/Astra tasks are verifying
the implementation, and Sol is doing read-only G5b preflight. G5a is not cleared.

The preceding turn also added an explicit 64-level JSON nesting limit at the
artifact boundary. A new test initially failed because Python 3.14 accepted the
deep input; the implementation now enforces the bound rather than relying on a
parser recursion exception. The corrected artifact/input run passed 32 tests:

```sh
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_job_inputs.py -q --basetemp=build/test-gui-g5a-json-boundaries-fixed-20261001
```

Current resume work: verify shutdown/protocol/provenance and process cleanup,
add authenticated end-to-end derived-protection coverage, then full Python/native
tests and independent Astra clearance. In particular, Astra reproduced a surviving
TERM-ignoring descendant after the CLI group leader exited; Terra owns that fix.
Do not treat direct-child cleanup alone as complete group cleanup. No G5b forms
are implemented yet, no new packages installed, no broker or remote Git calls.

October 4 verification so far (not the final gate):

```sh
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_job_protection.py tests/python/test_gui_requests.py -q --basetemp=build/test-gui-g5a-root-boundaries-20261004
# interrupted (130) at the known sandbox async wakeup stall; no pass claimed
.venv/bin/python -m pytest tests/python/test_gui_artifacts.py tests/python/test_gui_job_protection.py tests/python/test_gui_requests.py -q --basetemp=build/test-gui-g5a-root-boundaries-scoped-20261004
# 49 passed in 0.61s, scoped async access
.venv/bin/python -m pytest tests/python/test_gui_job_api.py -q --basetemp=build/test-gui-g5a-api-20261004
# 5 passed in 1.36s, including actual protected-input/output lineage
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build: no work; 15/15 CTest, 0.08s, build/local-dev
git diff --check
# passed at this checkpoint
```

Astra additionally reproduced dual-pipe EOF being mislabeled as cancellation and
a second TERM interrupting cleanup. Terra is addressing both with process-level
regressions, owned descendant reaping and signal-safe cleanup. A signal test's
incorrect `None` exit-code expectation was tightened to the actual terminated
child code; no assertion was removed to hide the implementation failure. The
final source-stable runner gate and full-suite result are still pending.

### Resume — 2026-10-05

The user resumed after the usage interruption. All three October 4 agent turns
had errored; none was counted as a completed review. The same assigned tasks
were resumed. Saved worker code now records termination in a pending flag rather
than blocking signals around spawn (the latter was rejected because children
inherited blocked TERM/INT). Standalone supervisor tests are present, but need
fresh verification and review. The draft `docs/gui-research-forms.md` describes
G5b.1 shared validators, G5b.2 inline/fixed-lab backend, and G5b.3 forms. It is a
plan, not implemented functionality. Root also saved an additional actual-job
regression for harmless original-file edits after acceptance, not yet run.

October 5 integration checkpoint:

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_api.py tests/python/test_gui_documents.py -q --basetemp=build/test-gui-g5a-api-docs-20261005
# 13 passed in 1.91s, including the new copied-input regression
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5a-full-20261005
# 711 passed in 55.88s; baseline only, overlapped final runner fixes
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no work; 15/15 CTest in 0.05s
```

Final review additionally reproduced a stale-descriptor race: `RunningJob.wait`
closed control pipes while the manager still exposed the active job, allowing a
late cancel/close to act on a reused descriptor. Terra added per-job descriptor
locking, atomic invalidation and one-shot cancellation plus a regression. This
post-baseline fix requires the source-stable full rerun. Independent review also
checks actual standalone orphan adoption, inherited-lock exclusion, repeated
signals, and worker completion-versus-cancel, not only fake scheduler objects.
The G5b draft now states authorization-before-validation, exact captured bytes,
honest inline origin and the compatibility tradeoff of earlier sampling errors.

### G5a gate passed; G5b.1 active — 2026-10-06

Recovered the completed final suite after the user resumed. Astra explicitly
passed G5a implementation review: 72 focused tests passed in 13.24s, then four
final hardened regressions passed in 5.16s. Actual standalone-process tests
cover descendant adoption/reaping, repeated TERM, unblocked child TERM/INT and
inherited owner-lock retention; descriptor reuse is asserted, not assumed.
Terra's last final message errored on usage after its saved 49-pass checkpoint;
that error was not substituted for review. Astra's explicit PASS and root's
complete suite provide the gate evidence.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5a-gate-20261005
# 711 passed in 52.97s; source-stable final gate, recovered October 6
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# final native checkpoint already passed: no work, 15/15 CTest, 0.05s
git diff --check
# passed
```

G5a provides the fixed authenticated offline API, not launch forms. No GUI server
was started for this API-only milestone; HTTP integration exercised actual v2 CLI
jobs and byte parity, failure visibility, copied inputs and protected lineage.
No user research root was registered/revealed, no new dependency installed, no
Git history/remote operation or broker request performed.

G5b.1 now starts under the reviewed `gui-research-forms.md` contract. Sol owns
single-run preparation and CLI extraction; Terra owns internal byte-loading
adapter seams and pure lab preparation. The shared preparation does not execute
an engine. Moving invalid sampling checks earlier is intentional; valid economics
and exports must retain parity. Result-dependent C++ checks remain authoritative.
Do not add inline endpoints, lab runner changes or forms before this small gate.

G5b.1 implementation checkpoint: the adapter byte seams and deep-copied pure
lab preparation are saved. Review preserved CSV invalid-option validation before
source-file opening, including through the CLI capture path. Single-run
preparation owns copied configuration, canonical data, a fresh strategy/config,
engine config and optional sampling settings; it does not execute a backtest.
Shared configuration checks do not pretend to validate future equity-event
staleness or undefined metrics; those remain in real-result C++ analytics.

Terra reported 36 focused passes before the concurrent CLI extraction. A later
35-pass/1-failure run hit the existing source-mutation guard during concurrent
Sol edits; this is not waived or weakened. Rerun after all source files settle.
Full G5b.1 tests and Astra review are pending. G5a's 711-pass gate above remains
the last completed full gate, not a claim that this extraction is already done.

After the next usage interruption the same agents resumed. Terra's source-stable
rerun cleared the earlier concurrent-edit failure:

```sh
.venv/bin/python -m pytest tests/python/test_research_adapters.py tests/python/test_lab_config.py tests/python/test_adapters.py tests/python/test_strategy_lab.py -q --basetemp=build/terra-g5b-sourcequiet-20261006
# 36 passed in 6.56s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# native build no work; 15/15 CTest in 0.08s
git diff --check
# passed at checkpoint
```

G5b.1 independent pre-gate evidence: Astra compared the pre-extraction HEAD CLI
with the refactor in isolated namespaces. All seven strategy types traded in
v1/v2 (14 pairs), saved Alpaca SMA ran in both versions (2 pairs), and a small
fixed lab ran (1 pair). All 373 exported file pairs were byte-identical, including
metadata/completion, without normalization. Evidence stays ignored under
`build/astra-g5b1-parity-8h8ZrP/`. Fifty focused tests passed independently.

The broader regression exposed an existing reversal-test patch target at the old
CLI import location. An initial private-constructor injection compatibility fix
was rejected as unnecessary production surface. The approved correction moves
only that test's patch target to `qte.research_config`; its real reversal and
accounting assertions remain unchanged.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5b1-gate-20261006
# 754 passed, 1 failed in 56.15s; NOT a passing G5b.1 gate
```

Failure: `test_standalone_subreaper_adopts_descendant_before_releasing_lock`
did not see its fixture's final readiness file. The child waits for the
descendant JSON path to exist, then immediately parses it; existence alone does
not guarantee the writer has finished. Terra owns diagnosis and an atomic
readiness/failure-cleanup fix in `test_gui_job_worker.py`, with Astra review.
Do not conflate this with a passing production cleanup test. Full stable rerun
and final review are required before starting G5b.2.

### G5b.1 gate passed — 2026-10-06

The user resumed and root recovered the final full-suite result. Atomic readiness
publication fixed the fixture race without extending its 3-second readiness
deadline or weakening any process-group/lock assertion. Setup-failure cleanup
allows 8 seconds (beyond the worker's 2-second TERM grace) and closes owned lock
FDs. No production supervisor change was needed. The reversal test now patches
the extracted module only; no constructor-injection parameters remain.

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5b1-final-20261006
# 755 passed in 53.32s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# October 6 native checkpoint: no work, 15/15 in 0.08s
git diff --check
# passed
```

Astra explicitly reviewed the latest code and fixture fixes: 76 focused
preparation/export tests and 18 worker lifecycle tests passed. It repeated the
independent HEAD comparison on final source: 17 paired runs / 373 byte-identical
files, no normalization, stable identity. Final evidence directory:
`build/astra-g5b1-final-parity-QNQuYN`. G5b.1 is complete, not the whole G5b.

Next: G5b.2a preview boundary (no new launches). Sol owns form conversion,
metadata/assumptions and unit tests; Terra owns bounded inline capture and tests;
root owns HTTP wiring, authorization and integration. Astra reviews independently.

### G5b.2a resume checkpoint — 2026-10-07

Saved strict form conversion/metadata/assumptions, bounded wire/data capture,
explicit nullable lab identity validation, and authenticated metadata/preview
routes. Preview orders capture → protection authorization → shared preparation;
it does not execute research or create snapshots/jobs. Exact captured wire config
hash and typed config fingerprint remain distinct. Invalid protected data does
not disclose financial/adapter diagnostics before an explicit existing reveal.

Before the interruption Terra reported 35 focused capture/legacy-input passes;
Sol reported 83 shared preparation/form passes. Astra reviewed capture (35
passes) and found CSV field-limit exception sanitization, which Sol fixed.
Agents then hit usage limits before the final independent preview decision.
Root's integration tool cell was lost across the interruption: its result is
unknown, not a pass. Rerun under a new unique build test directory. Root also
fixed the lab effective-configuration schema version DTO exception on resume.
Full stable gate and final Astra review are still required. No new launches yet.

October 7 root integration first returned 103 passed / 2 failed in 1.21s.
Both were newly written lab-success fixtures using 96 bars, below the existing
128-bar minimum. Corrected fixture to 128; validation rules and all assertions
remain intact. Added exact ordered registry-key and CandidateConfig field-inventory
drift assertions. Final source-stable root checks:

```sh
.venv/bin/python -m pytest tests/python/test_gui_form_api.py tests/python/test_gui_form_protection.py tests/python/test_gui_form_inputs.py tests/python/test_gui_research_forms.py tests/python/test_gui_job_protection.py -q --basetemp=build/test-gui-g5b2a-focused-final-20261007
# 105 passed in 1.13s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-g5b2a-full-20261007
# 837 passed in 53.16s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no work; 15/15 passed in 0.07s
git diff --check
# passed
```

API/full pytest used scoped execution approval for the documented sandbox async
wakeup failure. No packages, broker calls, user exports or Git history changed.
Final independent Astra review is pending at this checkpoint; this is not yet
permission to add a launch path. The next backend slice should first add immutable
inline snapshots and compatibility tests without exposing a new launch route.

### G5b.2a independent review passed — 2026-10-07

Astra explicitly passed current code review with no blockers, verified the final
DTO/fixture/drift corrections, and retained its independent 122-unit pass.
Its additional API rerun waited on a separate `timeout`-wrapper approval and was
aborted without a result; it is not counted. Root's 105 focused / 837 full and
15 native results above are the completed integration gate. Reviewer stopped
its owned stalled session (exit 130); bounded diagnostic ended at timeout (124).

Now G5b.2b snapshot-only slice under the documented small gate: new explicit
inline origin/versioned immutable inventory, existing legacy snapshot bytes and
fingerprints unchanged, no new job submission or worker surface. Gate before
manager/worker/lab verification integration. No additional dependency approval
or installation is needed.

Snapshot-slice interruption checkpoint: Sol saved the pre-change legacy-byte,
manifest/inventory and `_fingerprint` golden test (1 pass before interruption).
Root saved two real CLI parity tests in `test_gui_inline_snapshot_cli.py`; they
await the new snapshot module and have not run yet. At the next user resume,
`inline_inputs.py` had not been saved, so the current working test tree was not
collectable as a full suite. Terra/Sol/Astra resumed their same owned slice.
837 Python / 15 native remains the last completed full gate, not a claim about
the unfinished snapshot implementation. Finish the module before a new full run.

Snapshot source was subsequently saved and stabilized. `inline_inputs.py` adds
the explicit v2 envelope/manifest, one canonical wire identity, recompiled typed
identity, copied-data path only for research, honest null lab data fields, and
pre-write/verify bounds. Only late dispatch was added to legacy `job_inputs.py`;
no launch route, manager or worker change. Reviewer/root findings on path
association, malformed types, fake source filename and symmetric size limits
were fixed, with regressions. Root's separate invalid suite now has 42 cases for
forged prepared values and rehashed semantic corruption. Partial snapshots fail
verification and cannot be overwritten/reused.

Terra reported 92 combined focused passes before the latest usage interruption.
The root session result was unavailable after resume, so it was rerun:

```sh
.venv/bin/python -m pytest tests/python/test_gui_inline_inputs.py tests/python/test_gui_inline_snapshot_invalid.py tests/python/test_gui_inline_snapshot_cli.py tests/python/test_gui_snapshot_compatibility.py tests/python/test_gui_job_inputs.py -q --basetemp=build/test-gui-inline-snapshot-resume-20261007
# 74 passed in 3.06s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no work; 15/15 passed in 0.04s
git diff --check
# passed
```

Astra independently passed the latest snapshot-only code review: 74 focused
tests passed in 3.03s under `build/test-g5b2b-astra-snapshots-reviewed-20261007`,
with no remaining blockers. Approval covers snapshots only, not future launch
or worker integration. Full Python gate has been launched under
`build/test-gui-inline-snapshot-full-20261007`; record its result before moving
to the next backend slice.

### Inline snapshot gate passed — 2026-10-07

Full result recovered before further edits:

```sh
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-inline-snapshot-full-20261007
# 894 passed in 55.70s
```

Together with 74 focused, 15 native and Astra's explicit latest-source PASS,
this completes the snapshot-only slice. Existing CSV/Alpaca v1 inventory and
research retry hashes remain frozen by golden tests. Research economic artifacts
match direct CLI output after original data changes; every small synthetic-lab
artifact matches direct CLI bytes after original config changes.

Next active backend work: exact alternative inline request shape, fresh
capture→authorize→shared preview→fingerprint comparison→snapshot submission;
only fixed research/lab worker dispatch; new-lab-only completeness/provenance
verification. No browser controls, external provider calls or broker actions.

### Backend recovery — 2026-10-08

The preceding resume was blocked by an unresponsive command runner, including
read-only `pwd`. Its attempted checkpoint write and pending test result could
not be confirmed and were not counted. The next user continuation restored
terminal access. Read-only inspection found a clean checkout at `abc7586 oct7`
with the saved backend implementation; Codex performed no Git history or remote
operation. Last completed full gate remains the snapshot-only 894 Python / 15
native above, not a pass for the newer launch changes.

Root's saved work includes exact path/inline envelopes and individual size
limits, compile-only fingerprint comparison after protection authorization but
before financial/adapter diagnostics, and same-captured-bytes submission. Added
retry regressions now require stale409 after source edits and same-key conflict
after obtaining a fresh changed-input preview; no second job may be created.

Sol's saved strict fixed-lab verifier matches accepted configuration/current
fixed factories, exact candidate parameters/history, actual macro identities,
stored generator/data/window declarations, integer summary counts, comparison
columns and full attempt/failure/log inventories. Its recovered focused run
passed 14 tests, including hourly+daily_24h gap-shock/multiple-regime coverage.
Terra/Astra identified one remaining worker issue: generic snapshot attribute
fallback could accept False as legacy research. Terra is replacing it with real
v1/v2 snapshot type checks and adapting fixtures without weakening assertions.
Wait for source quiet, then focused API/lifecycle, full Python/native, and final
independent review. No G5b.3 browser work before the backend gate passes.

### Backend integration gate in progress — 2026-10-08

The lost API session could not be recovered, so it was rerun rather than counted.
The real-snapshot worker guard is saved and independently Astra-reviewed PASS.
Review also passed strict fixed-lab verification and stale/rekeyed API retries;
approved process cleanup/signal/descriptor ownership remains unchanged.

```sh
.venv/bin/python -m pytest tests/python/test_gui_inline_job_api.py tests/python/test_gui_job_api.py tests/python/test_gui_form_api.py tests/python/test_gui_requests.py -q --basetemp=build/test-gui-inline-api-recovered-20261008
# 41 passed in 6.63s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no build work; 15/15 passed in 0.08s
git diff --check
# passed
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-inline-backend-full-20261008
# 934 passed, 1 failed in 67.94s
```

The failure is `test_owner_eof_terminates_owned_child_and_uses_fixed_environment`:
its helper waits only for marker existence, then signals owner loss while the
child can still be between opening and writing the readiness file. The FD-probe
assertion sees empty text rather than `closed`. Terra is fixing the readiness
handshake without weakening FD/environment/cleanup assertions; Astra reviews.
No browser edits yet. Sol's bounded forms/jobs ownership plan is prepared;
implementation must wait for this gate. Tests use scoped local execution for
the previously documented async wakeup issue, not Full Access or installation.

### Backend gate passed; browser implementation started — 2026-10-08

Terra atomically publishes both parent FD-probe readiness and descendant PID
markers after complete writes; no assertions, deadlines or production behavior
were weakened. Astra explicitly passed the final fixture review before its turn
hit usage limits. That review message and the full result were recovered on the
next user continuation, rather than assuming the errored final message passed.

```sh
.venv/bin/python -m pytest tests/python/test_gui_job_worker.py -q --basetemp=build/terra-worker-markers-atomic-20261008
# Terra: 22 passed in 11.99s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-inline-backend-final-20261008
# root: 935 passed in 67.55s
```

Together with the 41 API / 15 CTest results and independent backend reviews,
G5b.2b is complete. Browser changes now begin under the ownership above. Planned
routes are `/research/new/run`, `/research/new/lab`, `/research/jobs/{id}`.
No new dependencies, external data calls, broker actions or Git operations.

### G5b.3 local test build — 2026-10-08

User narrowed this turn to finishing the existing test build efficiently and
requested a full data-flow/strategy guide. Sol built the metadata-driven forms;
Terra built paged job views and bounded polling. Root integrated routes/assets,
added module state-machine tests, completed sparse form styles, and kept optional
CSV mapping collapsed. Astra reviewed fixes for exact wire values, stale preview,
uncertain retry identity, cancel DTOs, view disposal, polling/focus and error
recovery; latest module review has no code-level blockers.

```sh
.venv/bin/python -m pytest tests/python/test_gui_research_browser_routes.py tests/python/test_gui_research_forms_static.py tests/python/test_gui_research_jobs_static.py tests/python/test_gui_static.py tests/python/test_gui_app.py -q --basetemp=build/test-gui-browser-focused-recovered-20261008
# 40 passed in 0.42s
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-test-build-final-20261008
# 954 passed in 71.13s
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
# no build work; 15/15 passed in 0.05s
/home/glsu6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/javascript/research_ui.test.mjs
# 6 passed; no npm packages or runtime installed
sed -n '/^```python$/,/^```$/p' docs/test-build-guide.md | sed '1d;$d' | .venv/bin/python -
# runnable guide example: 2 fills, total return 8.991000000002636e-05
git diff --check
# passed
```

The new route test initially assumed an incorrect `default-src 'none'` policy;
it was corrected to assert the existing reviewed same-origin CSP rather than
changing production security. A minimal DOM test-double needed action dataset
support; six executable module tests now pass. They are not layout/browser tests.
Scoped test execution addresses sandbox thread/spawn restrictions. Existing
bundled Node was discovered read-only; Python remained in `.venv` throughout.

Browser smoke used only `build/gui-browser-20261008/repository` and the local
loopback server. Actual controls validated the example SMA run, enabled launch
only after assumptions, observed success, explicitly registered it and rendered
event equity. A synthetic hourly lab with 128 bars/window and exact maximum
62-bit seed also succeeded and registered; its view retained all 15 candidates,
synthetic exclusions and withheld test-role values. No user catalog/export was
registered or revealed. This does not claim exhaustive browser cancellation,
restart, narrow-screen or 200% zoom acceptance; those remain manual follow-up.
Automated API/process tests still cover these backend safety boundaries.

Astra's final bounded review of root navigation, collapsed CSV mapping, form
styles and the guide is clear for the stated test-build scope, without claiming
the outstanding extended browser acceptance is complete.
Final style/static check: 31 passed in 0.06s using
`.venv/bin/python -m pytest tests/python/test_gui_research_forms_static.py tests/python/test_gui_research_jobs_static.py tests/python/test_gui_static.py -q --basetemp=build/test-gui-test-build-layout-final-20261008`.
`git diff --check` passed. Temporary browser tab closed and owned QA server
stopped with Ctrl-C (exit 130). No background GUI server remains from this task.

## November 23, 2026 — completed cash-session regime evidence

The frozen pre-open regime moved to `stress_selloff` on Friday’s data. Monday opened +4.59% and finished +4.72% against Friday’s SPX daily bar, a +2.09-sigma rebound at the last regular-session observation. Robinhood supplied complete fifteen-second SPX/SPY/QQQ and minute IWM coverage, 389 observed VIX minutes, and a post-close SPXW contract pair. All five current daily bars were interpolated at capture time. Official daily-bar backfill remains pending. No ES/NQ coverage or label revision. [Session report](../build/daily-regimes/2026-11-23/hood-20261123213353/report.md) · [Chart](../build/daily-regimes/2026-11-23/hood-20261123213353/session.svg)

## November 23, 2026 — daily agent publication

The existing daily heartbeat now follows the repository-owned [daily brief workflow](daily-brief-agent.md) and publishes to Research → Daily brief. The November 20 and 23 captures are available. The XNYS backlog check returned no missing completed sessions. The authenticated read path, session/holiday handling and publication checks passed along with the app integration tests (21 Python tests total), JavaScript syntax checks and 20 native tests. First unattended publication with the updated prompt is pending the next scheduled run.
