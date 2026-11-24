# Five-agent options research

Open **Research → Five-agent research** in the Linux QTE GUI. **Run fixed
synthetic backtest** works offline. To allow OpenAI agent requests, start QTE
with:

```bash
.venv/bin/python -m qte gui --port 8771 --allow-research-agents
```

With a local Ollama service running on `127.0.0.1:11434` and an installed
open-weight model, start QTE using:

```bash
.venv/bin/python -m qte gui --port 8771 --ollama-model qwen3:4b
```

This mode uses the local daemon and does not read an OpenAI key. Run Ollama
with `OLLAMA_NO_CLOUD=1` to disable the daemon's cloud features. The 2.5 GB
Qwen3 4B artifact is a CPU-friendly starting point. [Ollama local FAQ](https://docs.ollama.com/faq), [Qwen3 4B package](https://ollama.com/library/qwen3:4b)

The experiment selector includes the new [daily-band weekly reversal](strategies/weekly-reversal-research.md)
suite. The original unhedged straddle remains available through the same
selector.

In OpenAI mode, the server reads the approved key from the Git-ignored
`build/qte-agents/.env.local`. Each role starts unchecked. Select any subset,
then click **Run selected agents and backtest**. One run can make up to seven
OpenAI requests with the fixed synthetic specification, case summaries, papers,
and agent notes. QTE saves the run ID, notes, picks, skips, results, and backtest
hash under `build/qte-agents/runs/`. Agent failures preserve local results.

Premarket bias specifies a cutoff and observable inputs. Two strategy roles
choose independently from frozen executable rules, then challenge each
other using recorded counterexamples. Exposure reviews the debit budget and
conditions for abstaining. Documentation records evidence and falsifiers. QTE
owns fills, P&L, and the risk cap. [Agents API session documentation](https://developers.openai.com/api/docs/guides/agents-api/sessions)

## First replay

The unhedged long straddle uses a single strike and expiry. The control, the
0.15-point confirmation rule, and the 0.15–0.40-point no-chase rule share a
decision clock. The regime-sizing variant requests two pairs only when a
separate available premarket move reaches 0.40 synthetic points. Each case
begins flat. The maximum debit plus round-trip fees is $250 per trade.
Five-minute snapshots arrive one step late. Entry pays both asks from the
subsequent available snapshot and exit receives both bids two steps later.
The fixture assumes paired fills. The hindsight benchmark enters only with a
known profitable payout and uses one pair.

| Rule | After-cost P&L | Cumulative debit plus fees |
| --- | ---: | ---: |
| No confirmation, fixed size | $120.40 | $253.61 |
| Confirmation, fixed size | $120.40 | $253.61 |
| Confirmation, premarket regime sizing | $253.77 | $451.90 |
| Confirmation with no-chase band | $173.97 | $166.08 |
| Infeasible hindsight, fixed size | $186.93 | $110.75 |

Across four synthetic cases, confirmation changed no fills. No-chase avoided
the $53.56 wide-spread loss and still lost $12.97 in chop. Sizing amplified
both the wide-spread loss and the expansion gain. Missing entry quotes produced
zero fills. The fixture leaves open a failure of no-chase where an already
large move continues profitably. The first saved report uses proposals and
rebuttals from Codex collaborators because OpenAI returned
`usage_limit_exceeded` for the API project. Report ID
`b019f1876e5746d19229ceda090abcd4`.

The replay uses Black-76 to price each leg and calculate gamma. The expansion
entry opens Gamma Lab at its recorded price, strike, volatility, and expiry.

- Black's futures-option model supplies scenario prices and sensitivities. [Black (1976)](https://doi.org/10.1016/0304-405X(76)90024-6)
- Leland motivates modeling discrete hedge execution and costs before evaluating a hedged strategy. [Leland (1985)](https://doi.org/10.1111/j.1540-6261.1985.tb02383.x)
- Bollen and Whaley examine option demand and IV shape. This motivates a separate flow hypothesis if suitable timestamped data becomes available. [Bollen and Whaley (2004)](https://doi.org/10.1111/j.1540-6261.2004.00647.x)

## Historical validation

Use timestamped futures observations and paired option quotes with availability
times, depth, spreads, fees, and verified contract and expiry identity. Freeze
the premarket cutoff, entry rules, risk budget, and cost model on an earlier
period. Compare paired net P&L per eligible session on a chronological holdout,
counting skipped opportunities as zero. Report trade count, debit deployed,
uncertainty, and results by observable regime. The current premarket role
produces a research protocol. A dated assessment needs a market feed.

## Short-duration options master

In **Research → Five-agent research**, select **Options master · failure-driven
research**. The offline button runs the fixed candidate slate. With the existing
local Ollama or OpenAI provider enabled, select the strategy roles to nominate
registered rules and state falsifiers. Both nominees get an early comparison
against the incumbent. QTE applies the same gates to each. Premarket, exposure,
and documentation review the development audit. The optional model calls were
verified with a test provider. The completed local run used the offline slate.

The starting policy is cash. QTE tests naive fade, completed reentry at 1, 1.5,
and 2 daily expected moves, news and shock filters, a quote spread filter, and
one continuation control. A revision must strictly improve the worse of base
and stressed development P&L, be positive after removing the best session,
close at least ten trades in each cost scenario, and have zero unresolved exits.
There are at most three accepted revisions. The fixed order breaks ties and
model nominations can change the order within that budget.

QTE freezes the selected rule before a single chronological evaluation of that
candidate. The evaluation gate calls for at least 20 sessions and 30 closed
trades per cost scenario, positive P&L after costs and after removing the best
session, zero unresolved exits, and a positive lower bound on the exploratory
session block interval. These are pragmatic screening thresholds. They do not
establish sufficient statistical power. Failed evaluations leave the operational
policy in cash. Repeatedly tuning on the evaluation set contaminates it.
[Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

One long call or put expiring in 5–9 calendar days, with one contract at a time,
a $650 debit-plus-round-trip-fees cap per trade, and up to five entries per
session. The five-minute hold is measured from reported entry availability.
Missing timely exit quotes can extend holds toward ten minutes or leave them
unresolved. The replay charges the full reserved debit on unresolved exits.
$3,250 is the maximum cumulative daily debit under these research settings.
The existing Python quote replay pays the later ask and sells at the later bid.
Midpoint changes at those same timestamps diagnose execution drag. They assume
no midpoint fills. Stress adds 20 bp on each quote price and raises fees from
$0.65 to $1.30 per contract per side. The experiment uses quoted marks and no
broker order route, option shorts, assignment, or integrated portfolio margin.

On the saved synthetic run, the naive 1-sigma fade lost $1,003.69 at stressed
costs and $392 in midpoint P&L. The confirmed 1-sigma fade moved from $534 at
midpoints to a $25.24 after-cost loss. The selected 1.5-sigma reentry vetoes
observable news and excursions of at least 2.25 expected moves. It earned
$654.73 across 16 closed trades in eight synthetic evaluation sessions. The
exploratory interval for average stressed session P&L was −$4.37 to +$168.05.
Both the sample size and uncertainty gates failed. The local ThetaData sample
contains one vendor-selected session and cannot supply independent development
and evaluation windows. See [tick data coverage](tick-data-acquisition.md).

Run the same experiment from the repository root:

```sh
.venv/bin/python -m qte.options_master --output build/options-master-new
```

For a compatible timestamped capture, add `--input path/to/capture.json
--design-sessions N`, using the [weekly replay capture format](strategies/weekly-reversal-research.md#replay-a-capture).
Choose the split before inspecting outcomes. The CLI preserves the frozen master
and protocol before evaluation, then writes the failure log, selected validation
trades, HTML report, and hashes. Saved GUI runs contain the same replay evidence
and any selected model notes. The capture schema lacks independently verified
contract terms, firm quote sizes and provider provenance, so supplying a capture
can run the research gate but cannot promote a strategy to live trading.

## Locked forward study

The forward study tests the options master on real Schwab market-hours data.
Its split is fixed before any of that data exists. All commands run from the
repository root.

1. **Lock** before the first development session opens. Preview with `plan`,
   then write the lock once:

   ```sh
   .venv/bin/python -m qte.forward_protocol plan --study-id spy-weekly-v1
   .venv/bin/python -m qte.forward_protocol lock --study-id spy-weekly-v1
   ```

   The default is 20 development sessions, 1 buffer session and 30 evaluation
   sessions. `build/forward/studies/<id>/lock.json` records the builder rules,
   the resolved NYSE calendar with holidays and 13:00 early closes, the
   session dates for each phase, and the candidate slate. It also records
   costs, gates, the missing-session limit and the SHA-256 of the scoring
   code. Verify the built-in calendar against the exchange's published
   calendar before locking. It covers 2026–2027.
2. **Collect** every session, starting before 09:30 ET. The collector takes
   the close time from the lock:

   ```sh
   .venv/bin/python -m qte.forward_collector --contract examples/schwab-forward-contract.json \
       --lock build/forward/studies/spy-weekly-v1/lock.json
   ```

   It is read-only and never places orders. It writes an append-only, fsynced
   journal per session under `build/forward/journals/`. Restarting it after
   the close changes nothing.
3. **Freeze** after the development window settles, which is 20 minutes after
   its last close, and before the first evaluation open. Freeze opens only
   development-date journals. It runs the replay and the fixed tournament.
   Agent nominations are optional:

   ```sh
   .venv/bin/python -m qte.forward_protocol freeze --study-id spy-weekly-v1 \
       --agents strategy_a,strategy_b,exposure,documentation --ollama-model qwen3:4b
   ```

   Agents see development evidence only. They can reorder registered
   candidates but cannot bypass the gates. The output, `frozen.json`, is
   written once. A missed deadline voids the study.
4. **Evaluate** once after the evaluation window settles:
   `... forward_protocol evaluate --study-id spy-weekly-v1`. It replays only
   the frozen candidate on the evaluation dates. The candidate passes only if
   it clears the existing validation gates on those unseen sessions:
   - at least 20 sessions and 30 closed trades per cost scenario,
   - positive after costs, including after removing the best session,
   - no unresolved exits,
   - a positive lower bound on the block-resampled uncertainty interval,
   - and no more than 10% of evaluation sessions missing.

   A second evaluation is refused.

`status` reports the current stage, and whether the scoring code has changed
since the lock. Any change to the scoring code blocks freeze, evaluation and
ledger updates. A new lock and fresh sessions are then required.

**Paper ledger.** After each close, run:

```sh
.venv/bin/python -m qte.forward_paper update --study-id spy-weekly-v1
.venv/bin/python -m qte.forward_paper report --study-id spy-weekly-v1
```

This runs every registered candidate forward with its locked parameters over
every settled session, including development and buffer sessions. It appends
one hash-chained record per session with paper fills, write-offs and
cumulative P&L. Sessions without a usable journal are recorded as excluded.
Each update first verifies the chain and recomputes all earlier sessions from
the journals. Any difference halts the update without rewriting the ledger.

Limits:

- Fills follow the quote-replay policy (later ask, first eligible bid after
  five minutes). They are not broker fills.
- REST polling every 5 seconds is not a tick tape.
- No news feed is collected.
- Running nine candidates in parallel is a multiple comparison. Only the
  frozen candidate on evaluation dates is the pre-registered test.
- Protocol files are ordinary local files. Commit or timestamp `lock.json` and
  `frozen.json` externally if others must trust the ordering.
