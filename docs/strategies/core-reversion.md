# S1 VWAP reversion and S2 opening gap fade

These offline studies implement the SPX hypotheses in the Claude trading research specification. Each strategy selects a same-day PM-settled SPXW call debit spread for an upward reversion or a put debit spread for a downward reversion.

## Run a study

From the checkout, use the project venv and a new output directory.

```sh
.venv/bin/qte s1-study --demo --output build/my-s1-demo
.venv/bin/qte s2-study --demo --output build/my-s2-demo
```

Open `report.md` for the session counts, entry filters and simulated spreads. `report.json` records signal levels, trigger times, leg prices, fees, input checksums and the calendar identity. The two demos use deliberately constructed price paths and option quotes.

To screen the local archive:

```sh
.venv/bin/qte s1-study \
  --index-minute build/daily-regimes/year-20261120/raw/spx-minute-*.json \
  --spy-minute build/schwab-pilot-20261121/SPY-minute-20261012-20261120.json \
  --output build/my-s1-observed

.venv/bin/qte s2-study \
  --index-minute build/daily-regimes/year-20261120/raw/spx-minute-*.json \
  --daily build/daily-regimes/year-20261120/raw/index-day.json \
  --output build/my-s2-observed
```

Add `--options path/to/core-tape.json` to enable the regime, catalyst and execution stages. The price screen can run independently. Missing evidence appears in the report.

## S1 · VWAP z-score reversion

SPX has no traded index volume. Use each SPX minute’s typical price, (high + low + close) / 3, weighted by synchronized SPY share volume from the cash open. For each completed minute compute the deviation of the SPX close from this session VWAP. Divide that deviation by the population standard deviation of the latest 30 completed deviations, including the current minute. A zero denominator leaves z undefined.

From 10:00 through 14:30 Eastern, require two successive same-side deviations with |z| ≥ 2. The second close must move toward VWAP, shrink the absolute deviation and have |z| < 3. Require positive gamma, VIX ≤ 22, and clear calendar gates. Buy the eligible leg nearest 0.40 absolute delta. Sell the strike nearest the signal-time VWAP. Maximum profit divided by maximum loss must be at least 1.5 after budgeted round-trip fees at the entry limit.

Exit on a return to the last completed minute’s VWAP, |z| ≥ 3, loss of half the debit, capture of 70% of maximum profit, or the earlier of 45 minutes after fill and 15 minutes before cash close. The source prose and JSON differ on the profit target. This implementation follows the JSON, so a 5-point spread bought for 1 point triggers at a 3.80-point closing credit. Change `--entry-z`, `--stop-z` and `--lookback` to test another declared parameter set.

## S2 · Opening gap fade

At 09:45 Eastern compare the actual 09:30 SPX opening price with the immediately preceding scheduled session’s close. Require an absolute gap of 0.3–1.0%, inclusive. The first minute defines the initial extreme. For a gap up, the next 14 minutes must stay at or below its high and the 09:45 close must be below the open. Mirror both rules for a gap down. Skip if the first 15 minutes already touched halfway back to the prior close. This fixes the ambiguous “fails to make a new extreme” comparison and uses the prose specification’s single decision at 09:45.

Require positive gamma, VIX ≤ 30, a clear event calendar and a contemporaneous declaration covering earnings and macro catalysts affecting the index’s ten largest weights. Buy the eligible leg nearest 0.45 absolute delta. Sell the nearest strike to the 50% gap-fill level. Exit on touching that target, a new adverse high or low strictly beyond the full 15-minute opening range, or noon Eastern. On a minute touching both target and stop, the stop takes precedence.

## Quote and risk assumptions

- Both studies start flat with an independent account. Each takes at most one entry attempt per session. Defaults are one vertical, $10,000 starting cash, maximum risk of 2% of current closed equity, and $0.65 per contract per executed leg. Override with `--cash`, `--risk-fraction` (up to 0.02) and `--fee`. There is no estimated Kelly edge. Account-wide live controls and cross-strategy exposure are outside these research runs.
- Select the short leg first from listed strikes bracketing the target. The long strike must form a debit vertical and its absolute delta must be within 0.05 of the strategy target. Require displayed size and spreads ≤10% of midpoint on both entry legs. Use a synchronized later quote at long ask minus short bid, within a midpoint limit for 30 seconds and the original natural debit limit for the rest of the minute. Cancel the attempt if the gates close before a fill.
- An exit trigger submits a simulated paired sell limit. The limit starts at the last known spread midpoint, relaxes to its last natural credit after 30 seconds and to zero credit after 60 seconds. Fills require a quote sourced strictly after the trigger with executable size on both legs. Costs use a 100× multiplier. Queue position and exchange complex-book execution are unmeasured.
- Underlying bars are available at interval end. Use the close alone on the partially owned first minute, then completed high/low ranges. Bar-based stops precede targets when intraminute ordering is unknown. Monitoring gaps over two minutes between synchronized leg quotes or a missing required underlying minute leave the position unresolved and block subsequent trades. An unfilled exit can cash-settle only with coverage through the close and official PM settlement. Settlement incurs entry fees only.

## Supplying evidence

Use a JSON object with `schema` equal to `qte-core-spxw-v1`, `symbol` equal to `SPX`, `evidence` equal to `observed` or `synthetic`, and `sessions` keyed by Eastern trading date. Each session contains `snapshots`, `regimes` and `settlement` following the [S7 quote tape contract](s7-trend-day-convexity.md). `chain` denotes coverage of the candidate strikes and deltas. `update` can contain just the two legs. Only the current quarter-hour regime bucket available at decision time qualifies. A declared positive gamma estimate may confirm the independent price setup.

Each session also requires a `calendar` array. Every record has integer UTC `available_ns`, nullable booleans `event_day` and `top10_catalyst`, and `macro_times_ns` (scheduled UTC nanoseconds or null). An empty release list asserts checked coverage for the session. A null is unknown. Submit updated calendar declarations in increasing receipt order. Both strategies block entries from ten minutes before through fifteen minutes after each known release. S2 additionally requires `top10_catalyst` to be explicitly false. Each dated declaration must reflect what was known at its availability time.

The six archived dates are a retrospective convenience sample. Repeated qualifying minutes can belong to the same move. Historical option P&L requires matched regime, calendar and two-leg quote histories. Neither the screen nor the synthetic fixture measures out-of-sample profitability.
