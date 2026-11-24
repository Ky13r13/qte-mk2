# Market-data truth database

`python/qte/truth_db.py` stores every bar QTE has collected, from every
source, in one SQLite file. Raw captures stay on disk and are tracked by SHA-256
hash. The database is the shared, audited input for backtests, regime studies
and agents.

## Folder layout: `data/truth/` (ignored by git; never commit)

```text
data/truth/
  qte-truth.sqlite3            the database (default for qte truth / qte search / studies)
  captures/schwab/<batch>/     raw Schwab priceHistory captures (collect writes <UTC stamp>/ here)
  captures/schwab/chains-*/    raw Schwab optionChain snapshots (not ingested; read by --iv-check)
  captures/alpaca/<request>/   raw Alpaca downloads (bars.json + download.json; default --cache-dir)
  contracts/schwab-market-data.json   reviewed Schwab Market Data OpenAPI contract
  reports/                     regime outcome reports
  backups/                     earlier database files kept after rebuilds
```

The database is fully rebuildable from `captures/`:

```sh
.venv/bin/qte truth ingest data/truth/captures  # load captures (skips test/fixture/demo folders)
.venv/bin/qte truth derive                      # rebuild derived 1h/4h bars
.venv/bin/qte truth coverage                    # what is held, per instrument/interval/provider
.venv/bin/qte truth collect                     # fetch the standing Schwab set and ingest it
.venv/bin/qte truth regimes --output data/truth/reports/regimes-YYYYMMDD [--start 2020-01-01]
```

## Weekly routine

```sh
.venv/bin/python -m qte.brokers.schwab_auth refresh   # private prompt for app key/secret
.venv/bin/qte truth collect                           # run within ~30 minutes of the refresh
.venv/bin/qte truth derive
```

## Intervals

| Interval | Source | Notes |
|---|---|---|
| 1m, 5m, 10m, 15m, 30m | Schwab native (Alpaca 5m) | Schwab keeps about 48 days of 1m and about 9 months of 5–30m. `collect` requests all five for SPX, SPY and ES (VIX: 5–30m) |
| **1h, 4h** | **Derived** from complete 5m buckets (`qte truth derive`) | Schwab has no hourly bars. Cash-session series are anchored at the XNYS open (09:30, 10:30 … and 09:30, 13:30), with the last bucket ending at the calendar close (early closes respected). ES uses ET clock hours, and 4h buckets start at 18:00 ET, skipping the 17:00 halt. Incomplete buckets are skipped, never filled. They're stored with `quality='derived'` and replaced on each run |
| 1d, 1w, 1mo | Schwab native | Weekly and monthly candles stay provisional until their period ends. `collect` requests all three for SPX, SPY, ES, VIX and QQQ |

## Rules

- **One record per source capture.** Loading the same file again does nothing.
- **Providers are kept separate.** Bars are keyed by
  (instrument, interval, start, provider), so you can query where providers disagree.
- **Unfinished sessions are provisional.** A bar is marked provisional if its
  session hadn't ended when the capture was received. Research reads skip it. A
  later final observation replaces it, and the change is logged in `revisions`
  as `finalized`.
- **Final bars never change.** If a later capture disagrees with a final bar,
  the first value is kept and the disagreement is logged as a `conflict`.
- **Bad rows are rejected with a reason** in the `rejected` table:
  - inconsistent OHLC,
  - non-positive or non-finite prices,
  - a timestamp repeated with different values inside one response
    (`duplicate_timestamp_conflict`). Schwab did this for Nov 23, 2026.
- **Close-only rows are flagged.** Rows where open = high = low = close are kept
  but marked `close_only`, and range or excursion studies exclude them.
  Examples: VIX 1990–91 and a few SPX days in 1971.
- **Test data stays out.** Directory loads skip any folder whose name contains
  test, pytest, fixture, demo or synthetic. A test fixture with fake SPY prices
  once got loaded into the database. That's why this rule exists.

Timestamps: intraday `start_ns` is the bar's start in UTC. Daily bars are keyed
by trading date: `session_date` is the New York date of the provider's stamp, and
`start_ns` is New York midnight of that date. Schwab normally stamps daily candles
at midnight US/Central, but it re-stamped the newest candle at midnight US/Eastern
on 2026-11-23, which used to create a phantom Sunday session. Prices are as the provider sends them,
unadjusted. `/ES` is Schwab's continuous front-month series, not adjusted
across rolls. Each capture records the contract symbol actually returned
(e.g. `/ESZ26`).

## Coverage as of Nov 24, 2026 (Schwab)

| Instrument | Daily | 5-minute | 1-minute |
|---|---|---|---|
| SPX | 1970– | Mar 9, 2026– | Oct 7, 2026– |
| ES (continuous, incl. overnight) | 1983– | May 4, 2026– | Oct 14, 2026– |
| SPY | 1993– | Mar 9, 2026– | Oct 7, 2026– |
| VIX | 1986– | Mar 9, 2026– | n/a |
| VIX3M / VIX9D | 2007– / 2013– | n/a | n/a |
| NDX, QQQ, sector ETFs | daily | 30-minute only for some | QQQ Oct–Nov |

Schwab only keeps a rolling window of intraday history: about 48 days of
1-minute bars, about 9 months of 5- to 30-minute bars, and 40,000 bars per
futures request. **Depth only builds up if `collect` runs regularly**, at
least weekly to keep every 1-minute bar.

Caveat: Schwab's `$VIX` series before 1990 predates CBOE's current VIX method.
Treat regime results from before 1990 with caution.

## Scheduled collection

`collect` uses the stored Schwab token. An access token lasts about 30
minutes, and `refresh` asks for the app key and secret at a hidden prompt.
That means unattended nightly collection isn't possible yet. The options are:

1. You run `schwab_auth refresh` and then `qte truth collect` yourself. Once
   a week is enough to keep all the 1-minute data.
2. Approve a local, permission-restricted store for the app credentials, so a
   scheduled job (or the daily Codex agent) can refresh by itself. This
   is a security decision, and it's not built.

## Regime validation

`qte truth regimes` scores every session's pre-open regime label
(`qte.regime_study.features`, prior-day data only) against what the session
actually did. Everything is measured in expected-move units: prior SPX close ×
prior VIX / 100 / √252. The daily definitions below are first drafts:

- **trend day:** range ≥ 1 expected move, and the close is within 10% of the
  day's high or low.
- **reversal:** the day touched ±1 move from the prior close, then closed at
  least 1 move off that extreme.

Reports: `data/truth/reports/regimes-20261123/` (1986–2026) and
`regimes-2020on-20261123/`.
