# Daily expected-move reversals with American weekly options

Open **Research → Five-agent research**, select **Daily-band reversal · American
weekly options**, and click **Run fixed synthetic backtest**. The report compares
ten fixed rules under base and stressed costs and shows individual trades,
blocked entries, and American put gamma across spot and expiry scenarios.

## Frozen experiment

The opening anchor is the observed underlying price. Freeze the expected move
in price units as `anchor × same-day ATM IV × sqrt(calendar years to expiry)`
once the opening observation is available. Use a 365-day year and no
rolling intraday re-estimation. This is an implied one-standard-deviation scale
proxy. The 1, 1.5 and 2 bands are distances from the opening anchor. The proxy
does not establish empirical coverage probabilities.

- A downside fade buys a call. An upside fade buys a put. Each uses one
  preselected American contract with 5–9 calendar days to expiry.
- The naive rule enters at the first completed crossing. Confirmed rules wait
  for a completed close back inside the crossed band on the same side of the
  anchor. The 2σ exclusion rejects an excursion whose observed peak exceeded
  2σ. Trend-following and cash are controls.
- The two regime rules veto active news known at the decision or before a
  pending fill. They also veto observed excursion peaks at or above 1.5σ
  for the 1σ entry rule and 2.25σ for the 1.5σ rule. The tight-spread variant
  additionally requires a visible spread no wider than 4% of the ask.
  News input consists of dated state changes, so late news cannot block an
  earlier completed fill. An empty feed provides no news signal.
- Enter at a later eligible ask. Attempt an exit at the first eligible bid
  originating at least five minutes after entry. Quotes published more than
  30 seconds after their reference time are excluded. An unavailable exit
  has a separate unresolved record and a zero-recovery stress mark.
- Maximum five entries per session and one position at a time. Same-session
  replay starts flat. One contract's debit plus round-trip fees must fit the
  $650 experiment cap. Base fees are $0.65 per side. Stress fees are $1.30
  per side plus 20 bps adverse execution on the ask and bid.

## First synthetic run

These are 16 designed sessions. The evaluation slice is the final eight
fixtures. Its scenario families also appear in the first eight, so it tests
replay behavior and reproducibility rather than historical generalization.

| Rule | Mean entries/session (all, base) | Evaluation net P&L (base) | Evaluation net P&L (stress) |
| --- | ---: | ---: | ---: |
| Naive 1σ fade | 3.12 | −$1,022.50 | −$1,094.84 |
| Confirmed 1σ | 2.75 | −$333.60 | −$447.94 |
| Confirmed 1.5σ | 2.12 | +$669.90 | +$654.73 |
| Confirmed 2σ | 0.25 | −$33.60 | $0.00 with zero stress entries |
| Reject excursions above 2σ | 2.75 | −$377.60 | −$447.94 |
| Regime-gated confirmed 1σ | 2.12 | −$62.10 | −$116.40 |
| Regime-gated confirmed 1.5σ | 2.00 | +$704.20 | +$654.73 |
| Tight-spread regime reentry | 1.62 | −$84.90 | −$126.99 |
| 1σ trend follow | 0.94 | −$122.00 | +$18.39 |
| Cash | 0 | $0.00 | $0.00 |

The 1σ news gate blocked both entries in the known-news sell-off cases.
Unfiltered 1σ reentry lost $265.60 there across both slices. With delayed news,
the same gate entered and lost $217.60 across two cases. The 1.5σ gated rule
still lost $331.40 across the breakouts after apparent reentry. Under stress,
its evaluation P&L matched ungated 1.5σ. Treat these as cases to falsify,
not reasons to promote a rule or to lower the band to force five trades.

## Replay a capture

The run has no market-data download step. Provide a UTF-8 JSON capture with
`schema_version: 1`, `sessions` and `news_flags`. Each session contains:

| Field | Contract |
| --- | --- |
| `id`, `open_ns`, `end_ns` | Unique ID and nonoverlapping chronological session bounds |
| `opening_daily_iv` | `timestamp_ns`, `available_ns`, `forward` (opening underlying reference), annual decimal `iv`, same-session `expiry_ns` |
| `weekly` | `call_contract_id`, `put_contract_id`, shared `expiry_ns` 5–9 calendar days from open |
| `bars` | Chronological observations with `timestamp_ns`, `available_ns`, `close`; include the fixed five-minute decision grid |
| `option_quotes` | `contract_id`, `timestamp_ns`, `available_ns`, `bid`, `ask`; a zero bid cannot close a position |
| Top-level `news_flags` | `timestamp_ns`, `available_ns`, boolean `active`; publish a clear state when the event window ends |

All timestamps use UTC nanoseconds. Importers must verify contract mapping,
exercise style, multiplier, and availability lineage. The replay assumes
standard 100-share deliverables. For example:

```bash
.venv/bin/python -m qte.weekly_reversal_suite --input data/weekly-capture.json --output build/weekly-capture-result.json
```

Omit `--input` to reproduce the synthetic slate. Output creation refuses an
existing file. An actionable historical test needs paired source-timestamped
underlying and option NBBO with depth, opening daily IV, as-observed event
timestamps, and a chronological split locked before scoring. Alpaca's
indicative feed uses derived quotes and delayed trades, so it cannot establish
five-minute fill feasibility. [Alpaca historical option data](https://docs.alpaca.markets/us/docs/historical-option-data)

## Next research families

Use matched windows and identical costs to compare time stops, no-chase bands,
entry speed, volatility changes and alternative strikes. Capture spreads and
leg depth before studying debit verticals, calendars, or butterflies.
American short legs require assignment, exercise, dividend, and multi-leg
execution handling. A delta-hedged straddle also needs underlying hedge
orders and combined accounting. This release supports premium-paid long calls
and puts for the reversal suite.

- Cox, Ross and Rubinstein provide the lattice used for the scenario quotes and
  gamma grid. Gamma uses bump-and-reprice at fixed IV and continuous dividend
  yield. [Option pricing: A simplified approach](https://doi.org/10.1016/0304-405X(79)90015-1)
- Nagel reports that short-term equity reversal returns vary with market
  stress. Dyl, Yuksel and Zaynutdinova distinguish news-related continuation
  from non-information price shocks at longer horizons. Neither study tests
  the exact weekly-option rule. [Evaporating Liquidity](https://www.nber.org/system/files/working_papers/w17653/w17653.pdf), [Price reversals and price continuations](https://doi.org/10.1016/j.jbusres.2018.08.036)
- Cboe's VIX1D provides one-day volatility context for SPX. QTE's ATM IV band
  has a separate model and underlying. [Cboe VIX1D methodology](https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_1-Day_Volatility_Index.pdf)
