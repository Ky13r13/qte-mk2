# SPXW daily-band reversal with debit spreads

Replay 5-, 10- and 20-point debit spreads on the same directional 1.5σ
reentry signal. Use a bull-call spread after reentry from below the lower
band or a bear-put spread after reentry from above the upper band. The
long leg is the nearest visible strike for that side, with ties resolved to
the lower strike. The same-weekly-expiry short leg is the indicated width
away, farther out of the money. The replay uses PM-settled European SPXW
contracts with a $100 multiplier and 5–9 calendar days to expiry, choosing
the expiry closest to seven days. [Cboe contract specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/)

QTE freezes the opening same-day ATM IV band and observes completed
five-minute closes. One implied standard deviation in index points is
`opening_anchor × IV × sqrt(seconds_to_PM_expiry / seconds_in_365_days)`.
The optional gate blocks active news and a known
excursion peak of at least 2.25σ. Every width uses one spread, at most five
entries per session, one position at a time, and a $1,500 cap on debit plus
round-trip fees. Spreads must have both legs quoted at the same reference
time, with one lot displayed on each relevant side and publication within
30 seconds. The simulator buys at long ask minus short bid and waits at
least five minutes before executing the reverse against the next eligible
pair. The final minutes before close are ineligible for new entries.
The fixture's one-second publication lag and one-minute snapshots produce
six-minute realized holds for the matched examples.

Base fees are $1.25 per contract per side ($5 round trip). Stress charges
$2.50 plus 20 bp per leg on each fill, applied adversely to each leg price.
An unresolved close is kept in its own ledger, valued at zero in the
conservative summary, and halts further entries for that candidate for the
session. The model assumes atomic paired execution at synchronized leg quotes.
Complex-order book data is needed to validate whether the net order would
fill. Expiry payoff curves are hypothetical and exclude exit fees. The replay
actually exits intraday.

## Run it

```bash
.venv/bin/python -m qte.spx_spread_study --output build/spx-verticals.json
.venv/bin/python -m qte.spx_spread_study --input data/spxw-capture.jsonl --output build/spx-verticals-captured.json
```

The first command uses 16 designed sessions with modeled European option
quotes. The opening IV and underlying are synthetic inputs. The report includes
leg prices, strikes, signal and fill timestamps, entry and exit cash amounts,
resolved and unresolved P&L, and year-by-regime summaries. Its five observable
regimes are range, reentry, continuation, shock and news. Range and continuation
do not trigger a reentry by definition. Design-case names such as
`breakout_after_reentry` are annotations only. The older macro, volatility
and dealer regimes need separately timestamped inputs and defined sampling;
these spreads have no observed historical attribution for those regimes.

In the synthetic run at base costs, gated 5-, 10-, and 20-point spreads
returned −$1,270, −$650, and +$15 respectively after conservative marking.
At stressed costs the same widths returned −$3,888.46, −$3,234.18, and
−$2,314.21. One 20-point exit was unresolved in each cost scenario.
The ungated 20-point spreads lost $285 in two known-news entries under base
costs. The gate blocked both and could not block delayed news that arrived
after fills. The fully scripted fixture is suitable for validating mechanics.

## Decade coverage and input format

Target November 14, 2016 through November 13, 2026. The checkout has no
licensed decade capture. Cboe offers quote updates and trades from its own
recording and separately 1-minute OPRA NBBO snapshots. Verify Cboe MDR's
venue scope and handle the timestamp format change on October 1, 2019.
Its free MDR sample downloaded during this task is a December 24, 2019
OEX file. It does not provide a usable SPXW study session.
[Cboe Legacy MDR](https://datashop.cboe.com/mdr-quotes-trades-data),
[Cboe Option Quote Intervals](https://datashop.cboe.com/option-quote-intervals)

Use one-minute consolidated BBO for the consistent decade baseline and a
separate tick-level sensitivity check for periods with trustworthy receipt
timestamps. Databento documents prior availability of only one-minute BBO
before March 28, 2023 with synthesized receive timestamps. Tuesday and
Thursday SPXW expirations were introduced in 2022. Require an actually
listed same-day PM series and retain date coverage for missing IV days.
[Databento OPRA specification](https://databento.com/docs/datasets/opra-pillar),
[Cboe 2022 expiration announcement](https://ir.cboe.com/news/news-details/2022/Cboe-to-Add-Tuesday-and-Thursday-Expirations-for-SPX-Weeklys-Options-04-13-2022/default.aspx)

The JSON Lines header contains `schema_version: 1`, `product: "SPXW"`,
`source` and `cadence` (nonempty strings). Each following line has `session`
and `news_flags`. Use one chronological exchange session per line with UTC
nanoseconds. Include any news state still active at the session open, explicit
clears, and publication timestamps. Provide the following session fields:

- `id`, `open_ns`, `end_ns` and `opening_daily_iv`. The IV record contains
  `timestamp_ns`, `available_ns`, `forward` (opening SPX anchor), decimal
  annualized `iv`, and same-session PM `expiry_ns`. Use null when missing.
- `bars` with `timestamp_ns`, `available_ns`, `close`. The decision
  grid uses five-minute closes measured from the session open.
- `contracts` with `id`, `right` (call or put), integer `strike`,
  `expiry_ns`, `available_ns`, and the constants `root: "SPXW"`,
  `exercise: "european"`, `settlement: "pm_cash"`.
- `option_quotes` with `contract_id`, `timestamp_ns`, `available_ns`,
  `bid`, `ask`, `bid_size`, and `ask_size`. A synchronized snapshot
  adapter must retain genuine leg availability and exclude stale leg quotes.
- `news_flags` with `timestamp_ns`, `available_ns` and boolean `active`.
  A coverage gap means the gate cannot identify news in that interval.

Keep raw provider schemas in an adapter. Before a historical evaluation
freeze the universe, periods, rules, costs, and news coverage. Compare
capture coverage to the exchange calendar so omitted sessions are visible.
Report missing sessions, fills, observation and opportunity counts, risk
deployed, and net P&L together. Separate confirmed exits from unresolved
stress marks. Keep the first holdout chronologically after development.
