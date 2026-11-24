# SPXW outright options and position sizing

QTE buys one near-ATM European SPXW call after SPX reenters its opening
band from below, or a put after reentry from above. The band is set at
1.5 times `opening SPX × prior VIX1D close / sqrt(252)`, using decimal
annualized volatility. Signals use consecutive five-minute closes on the
same side of the open. A known excursion of at least 2.25 band standard
deviations vetoes the signal. The replay has no timestamped news coverage.

Each option is modeled on a five-point strike grid, seven calendar days
to a 16:00 ET expiry, with a $100 multiplier. QTE picks the nearest strike
to SPX at signal time, breaking ties downward. Black-76 reprices the option
on the next completed one-minute SPX bar and five minutes after entry. It
uses the prior VIX9D close held flat across the session and strike grid,
4% financing and 1.3% dividend yield. The modeled strikes and expiries
carry no historical listing verification. Depth, intraday IV, skew and
executable option quotes are absent. [SPXW contract specs](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/)

Base prices add or subtract 0.25 index points from mid per side and fees
are $1.25 a contract per side. The wider-cost case uses 1.0 point and $2.50.
A third case also reduces exit IV by one volatility point. QTE rounds
adversely to 0.10 index points. Each candidate starts with $100,000. Compare
1, 2 and 5 contracts with budgets of 2%, 8% and 16% of available cash.
Budget sizing floors to whole contracts and caps quantity at five. Premium
and both sides of fees count toward cash and budget checks. Hold at most
one position and open at most five trades per session.

## Free inputs and replay

Use FirstRate's public one-minute cash-SPX sample and Cboe's daily volatility
CSV downloads. The local sample used 4,680 regular-session bars across 12
sessions, October 29–November 13, 2026. FirstRate's Eastern minute-start
timestamps become UTC bar ends, with availability assumed at bar close.
Source hashes and VIX observation dates are recorded in the report.
[FirstRate SPX sample](https://firstratedata.com/i/index/SPX) ·
[Cboe VIX histories](https://www.cboe.com/tradable-products/vix/vix-historical-data) ·
[Cboe VIX1D CSV](https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX1D_History.csv)

```bash
.venv/bin/python -m qte.spx_single_study \
  --spx build/spx-public-data/firstratedata-sample-spx.zip \
  --vix1d build/spx-public-data/VIX1D_History.csv \
  --vix9d build/spx-public-data/VIX9D_History.csv \
  --output build/spx-public-data/outright-replay.json
```

Three signals qualified in the sample. The 2% budget passed on all three
because a single modeled premium exceeded the cap. The 8% budget bought
one contract per signal, and 16% bought two. At base costs the one-contract
case lost $197.50 and the five-contract case lost $987.50. The same
one-contract sequence lost $655 under wider costs and $1,925 when exit IV
also fell by one point. The run is a methodology check. A chronological
holdout with more sessions and observed quotes is needed for estimating
execution sensitivity and repeatable performance.
