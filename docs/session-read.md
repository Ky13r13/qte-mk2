# Session read

## Pre-registered replay rules · November 24, 2026

Use the first complete all-contract 0DTE chain in the replay directory as the reference clock. Use complete five-minute candles ending by that clock. The previous completed XNYS session supplies the SPX anchor, VIX and VIX3M. Apply the frozen regime-lab rules to prior-session inputs only. Expected daily move is prior SPX close × prior VIX / 100 / √252. Report the 20- and 200-bar SPX averages through the last completed candle.

Use standard PM-settled SPXW contracts. The dealer gamma proxy assumes dealers long calls and short puts, weighted by reported open interest. Report dollar delta change per 1% spot move; search for sign changes over ±5% of reference spot. Future expiries captured after the reference clock are reported separately with their own timestamps. OI signs are assumptions.

For the IV gate use ATM and the actual primary-spec short strikes, with strike placement based on prior VIX and the remaining trading minutes. Convert each observed calendar IV to study-time units with `CALENDAR_TO_TRADING` before dividing by prior VIX. The session score is the lower of the two short-strike multiples. The forward gate requires at least 20 qualifying calm session observations and a median score of at least 0.60. Research settlement uses the final complete SPX five-minute bar ending at cash close, with $1.25 per leg at entry and no fee at expiry.

Position marks use the selected chain midpoint and signed quantity. Scenario pricing uses zero-rate European Black-76 with calendar minutes. The grid uses spot shocks of −2%, −1%, 0, +1%, +2% and time at reference, halfway to expiration and expiration. A recorded option reference must reproduce the observed midpoint within $1 per contract.

## Run

```sh
.venv/bin/qte session-read --replay data/truth/captures/schwab/live-20261124T172044Z \
  --position '2x SPXW 7170P @7.725'
```

Omit `--replay` to capture with the stored Schwab market-data token. Refresh interactively before running if it has expired. `--output` accepts a new JSON path. The default is a timestamped file under `build/session-read/`. In replay, interval-end bar availability is reconstructed from the captured history. Snapshot receipt times are preserved.

Repeat `--position` to enter more contracts. For a later captured expiry include its date, as in `2x SPXW 2026-11-25 7195C @50.1`. Each scenario uses the spot and clock from its own chain.

The November 24 replay classified as R12. ATM measured 0.539× / 0.538× prior VIX for puts / calls. The fixed-spec 7105 put and 7290 call measured 0.661× and 0.600×. This reading uses prior VIX and fixed strategy strikes. The historical `--iv-check` example used a different strike and VIX basis. The recorded 6970 put model is within $1 of the observed midpoint. The 7200 call model missed the midpoint by $137 per contract at zero rates with spot as forward. Position grid prices are model estimates.
