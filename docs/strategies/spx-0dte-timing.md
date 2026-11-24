# SPXW 0DTE timing and risk

QTE found no rule that supports a $10,000-to-$50,000 target in 30 calendar days.
This study tests time of entry, direction, volatility conditions and retail
execution sensitivity. It preserves every trade, skip, missing observation,
choice and account path.

## What the data showed

The corrected run screened 224 rules with five modeled execution scenarios.
It used 172 development sessions and 78 of 79 later sessions on a complete
five-minute SPX grid with prior-known VIX. The later one-minute panel had
21 usable sessions from 30 requested. All 15 later sessions with prior VIX
below 15 were separately requested at one minute. The vendor supplied no
usable minutes on those dates, so their low-volatility result is five-minute
only. Both periods reuse historical index observations and are retrospective.

- **15:50 ET, 0.2%-OTM call.** The five-minute hold with a 50% net target
  lost $6,385 under wide modeled costs across 74 later opportunities.
  On the separate later minute panel, four of 21 trades hit the target
  and aggregate P&L was −$1,720. The low-volatility slice on the five-minute
  panel lost $707.50 over 11 trades and hit neither the 30% nor 50% net target.
- **Power and sour hour.** Across 248 full comparable SPX sessions, from the
  15:00 open to the end of the regular session, SPX rose at least 0.2% on
  27 dates and fell at least 0.2% on 32 dates. From 15:50 to 15:55 it rose
  at least 0.2% on five dates and fell at least 0.2% on five. The time
  window did not supply a directional edge by itself. These are index moves.
- **Development reference.** The 15:30 confirmed call with a 30% target and
  five-minute hold was the least negative eligible rule under the worst
  development scenario. It failed the gate. On later sessions it lost
  $2,270 across 18 trades under wide modeled costs and hit its target zero
  times. The best-looking later downside result, a 10:30 confirmed put,
  made $3,525 on five later wide-cost trades after losing $285 on seven
  development trades. It is a counterexample to selecting by hindsight.
- **Observed quote pilot.** On August 19, 2025 the 10:00 call was 0.170% OTM.
  The same eligible trade returned +31.47% and +54.91% under alternative
  target rules, each with a one-second delay and $1.30 per side. These
  simulations use saved two-sided quotes and display sizes. The call file
  ends at 10:27:53 ET, with no observed 15:50 call. One trade does not
  validate an entry time.

## Account risk

None of the 54 complete overlapping later 30-day windows reached $50,000 for
the frozen reference at any of the tested debit budgets. Budgets ranged from 2%
to 30% of equity. The wide-cost 30% budget produced a worst closed-equity
drawdown of 66%. That is a stress illustration, not a proposed allocation.

Even 21 consecutive wins at exactly +50% per trade compound 2% allocated
debit into only about $12,324 from $10,000, before contract rounding. A -35%
trigger does not limit losses to 35% when the bid gaps, disappears or cannot
fill. The debit and fees are the maximum reserved loss in the long-only
research setup. The account software sizes whole contracts, includes known
no-trade days, and excludes windows with unknown observations from its
summary.

## Where each tool fits

- [Jordi Corbilla’s Quant Forecast Lab](https://github.com/JordiCorbilla/stock-prediction-deep-neural-learning) provides walk-forward and naive-baseline methods. A price forecast must beat a simple direction baseline after quote costs first.
- [Awesome Quant](https://github.com/wilsonfreitas/awesome-quant) is a directory for evaluating libraries and data tools.
- [q-trader](https://github.com/edwardhdlu/q-trader) demonstrates deep Q-learning for stock trading. Its model cannot supply the missing historical option quotes or demonstrate profitable SPXW fills.
- [QF-Lib](https://github.com/quarkfin/qf-lib) offers a separate event-driven framework for cross-checking design and analytics. QTE already owns the core simulator. No replacement or dependency is needed for this study.
- [Cboe SPX/SPXW specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/) define the multiplier and expiration trading cutoff. The study excludes sessions or entry times that cannot admit a full hold before the cutoff.

## QTE research inventory

The year regime archive supplies point-in-time context and observed underlying
paths. The Schwab REST chain supplies contract discovery and a snapshot. The
Schwab Streamer is the preferred next source for collecting a synchronized
forward quote archive. The quote pilot verifies one date's mechanics. The
timing replay separates model prices from archived option prices. The
bankroll module translates a frozen candidate's debit and exit proceeds into
integer-contract account paths.

The Book Strategy Explorer and futures weights remain useful for their own
research. They do not validate 0DTE execution. The previous weekly options
master remains a separate mechanics fixture. No modules, reports or user work
were removed.

For a forward study, predeclare a narrow set of entry windows and capture
the contracts, index, bid/ask, sizes, provider event timestamps and local
receipt timestamps together. Run a measured latency replay and freeze the
call/put and volatility gates before evaluating new dates. Compare per-session
P&L with cash and per-trade P&L with the unchanged no-filter control. A lower
number of trades is insufficient if expectancy stays negative.

## Reproduce

From the repository root:

```sh
.venv/bin/python examples/spx_0dte_timing_study.py \
  --raw build/spx-0dte-timing/raw --minute build/spx-0dte-timing/minute \
  --calendar build/daily-regimes/year-20261120/sessions.json \
  --output build/spx-0dte-timing/new-run
```

The final report, fixed protocol, frozen choice, complete score matrix, and
account paths are under `build/spx-0dte-timing/final/`. The actual quote pilot
is under `build/spx-0dte-quote-pilot/`.

## Post-selection winner to investigate

The 10:30 confirmed put with a 30% net target and a ten-minute maximum hold
earned $3,525 over five later trades in the wide-cost replay, compared with
−$4,320 for the same time and direction without the gate across 78 trades.
All five filtered trades came from November 16–20, one bearish cluster.
November 19 contributed $1,945, about 55% of the net. The one-minute
cross-check on overlapping dates earned $2,655. Earlier wide-cost P&L was
−$285. The strongest explanation to test is bearish continuation in that
episode. Its clock and filter need fresh observed quotes and a new episode.

The modeled single-contract debits ranged from $2,482.50 to $3,962.50.
A 10% debit cap on a $10,000 account would have skipped all five. The
21.3% modeled profit over cumulative debit is not a portfolio return.
The per-trade ledger and time controls are in
`build/spx-0dte-timing/exploratory-attribution/`.

## Replay an observed quote capture

The archived Theta pilot runs offline from the repository root:

```sh
.venv/bin/python -m qte.spx_0dte_quotes --output build/my-quote-replay
```

For another expiration-day SPXW call or put, supply the contract, the verified
cash-session close in New York time (including early closes), the SPX price
CSV and the Theta quote CSV. For example, the archived call can also be run
with explicit file names:

```sh
.venv/bin/python -m qte.spx_0dte_quotes \
  --session 2025-08-19 --strike 6455 --right CALL --close-et 16:00 \
  --spot-csv build/tick-data-evaluation/theta/SPX_price.csv \
  --quotes-csv build/tick-data-evaluation/theta/SPXW_20250819_quotes_tick.csv \
  --output build/my-explicit-quote-replay
```

The Markdown and JSON reports count excluded records and retain every
in-session decision, including missing entries and unresolved exits. The
adapter assumes naive New York timestamps, positive displayed sizes and
condition 50 on both sides. It discards late updates in input order.
A gap exceeding ten seconds while monitoring a position leaves the exit
unresolved and reserves the entire entry debit as the conservative loss.
The original example command remains available. Each run requires a new
output directory so earlier results remain reproducible.
