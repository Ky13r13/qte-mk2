# Session hours and option entry deadlines

The paper desk evaluates decisions during the underlying US cash session. Export the dates covering the decision and the listed expiry. For AM-settled SPX, include the preceding open session as well.

```sh
.venv/bin/python -m qte.market_sessions export --start 2026-11-20 --end 2026-11-27 --output build/xnys-november.json
```

The schedule includes each calendar date, UTC nanosecond opens and closes, the installed `exchange_calendars` version, and a checksum of the declared hours. Holidays have null hours. Checksum verification and replay run offline. Scheduled future hours can change if the exchange publishes a new notice. A new export can be compared to the previous snapshot. [Exchange Calendars](https://github.com/gerrymanoim/exchange_calendars) · [Cboe holiday hours](https://www.cboe.com/about/hours/us-options/)

To create a complete research dataset, pass `--bars-input captured-bars.json` to the same export command. Supply the existing `qte-session-bundle-v1` fields with `schedule` omitted, including the actual `bars`, their source ID, action-free declaration, interval kind, and publication delay. The importer validates every bar against the declared buckets before publishing. Use a fresh output filename for each snapshot.

| Product | Last date eligible in QTE |
|---|---|
| `SPXW_PM` | Expiry date, through the cash close (normally 16:00 Eastern, 13:00 on a holiday half day) |
| `SPX_AM` | Previous open session, through its cash close |
| `SPY_OPTION` | Expiry date, through the cash close |

QTE's cash-hours policy excludes extended sessions. Supply the actual listed contract expiry and any earlier contract or broker cutoff. Use `--contract-cutoff-ns` and `--broker-cutoff-ns` for absolute UTC deadlines. The effective cutoff is the earliest applicable restriction. [SPX contract specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/) · [ETF expiry hours](https://www.cboe.com/document/tech-spec/document/technical-specifications/equity-options-extended-trading-hours-faq/)

This bundled synthetic example examines a 12:55 Eastern entry on a 13:00 close. A four-minute hold leaves one minute for exit. Moving the decision one nanosecond later rejects it.

```sh
.venv/bin/python -m qte.market_sessions check \
  --schedule examples/session-bars.json --product SPXW_PM --expiry 2025-11-28 \
  --now-ns 1764352500000000000 --hold-seconds 240 --buffer-seconds 60
```

The time check establishes whether the planned hold fits. A fill still requires an eligible quote before the effective cutoff.

To display the countdown in the local GUI, export coverage including today and the intended expiry. Run the check with `--desk` from the repository root. The GUI reads the published policy from `build/live-desk/session.json` and evaluates it against the current clock each time it refreshes.

```sh
.venv/bin/python -m qte.market_sessions check --desk --schedule build/xnys-november.json \
  --product SPXW_PM --expiry 2026-11-27 --hold-seconds 300 --buffer-seconds 30
```
