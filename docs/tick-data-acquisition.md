# Tick-data acquisition checkpoint — November 15, 2026

Objective: measure short-horizon option outcomes using actual bid/ask updates,
sizes and synchronized underlying observations. This is data acquisition, not
approval to trade or a claim that subsecond strategies have an edge.

## Free sample acquired first

ThetaData publishes a [no-account sample bundle](https://www.thetadata.net/docs/Articles/Getting-Started/Sample-Data.html).
The ZIP is 3,771,994,492 bytes. QTE downloaded only six selected members via
bounded HTTP range requests, about 32 MB compressed:

- SPX index price updates and end-of-day context.
- SPXW trades and end-of-day context.
- SPXW tick quotes for August 19 and August 26, 2025 expirations.

The observation day is August 19, 2025: the expiry files represent same-day and
seven-day options. Raw files, SHA-256 provenance manifest, archive inventory,
download scripts and structural audit are in `build/tick-data-evaluation/`.
They are ignored local research artifacts, not portable Git-tracked fixtures.
The files are vendor samples, not synthetic or reconstructed premiums.

Integrity checks passed, but both quote CSVs have an incomplete final row.
These source defects are counted and the raw files preserved; the sample is
not certified for complete-session replay.

Audit positive prices/sizes, crossed/locked quotes, condition codes, timestamps,
contract counts and per-contract ordering before admitting any data to replay.
The sample has naive timestamps and no collector receive timestamp; do not
invent nanosecond precision or network latency. Positive non-crossed NBBO is
not proof an order would have filled. No simulated fill results are produced.
Index publication data has no executable book; ES/MES is a separate instrument.

## First authenticated Schwab capture

On November 21, 2026, QTE saved SPY and QQQ one-minute history for October 12
through November 20. Both returned 30 complete regular sessions and 11,700
bars. The audit checked timestamps, OHLC consistency, volume, minute coverage
and hashes. Corporate action and adjustment conventions require separate
verification before engine accounting tests.

The separate SPY snapshot contains 16 American-style contracts expiring
November 27 and November 30. Each has positive bid/ask and sizes. The provider
also returned quote timestamps, contract metadata, IV and Greeks. The request
arrived Saturday morning, and the leg quotes are stamped Friday around 16:15
ET. That is one snapshot. It provides neither a historical quote series nor
an observation of brokerage fill latency.

Raw captures, `audit.json`, and the reviewed request definition are in
`build/schwab-pilot-20261121/`. The two GET paths and request parameter names
were checked against the [schwab-py client source](https://github.com/alexgolec/schwab-py/blob/main/schwab/client/base.py)
and returned valid responses from Schwab. The reviewed definition is a local
allowlist in OpenAPI format. Schwab keeps the [official specification](https://developer.schwab.com/products/trader-api--individual/details/specifications/Market%20Data%20Production)
behind the developer portal login. A forward quote collection pilot during
market hours is still needed for synchronized short-hold options evidence.

### REST quotes and the Schwab Streamer

Two additional authenticated REST requests verified `GET /marketdata/v1/quotes`
with `symbols=SPY,QQQ` and `GET /marketdata/v1/SPY/quotes`. Both return
snapshots. QTE's capture operation IDs are local names and can differ from
documentation labels. The provider responses and the separate quote route file
are in the same pilot directory.

The Schwab Streamer document supplied by the user describes a separate
WebSocket protocol. It uses the GET User Preference response for connection
details, then `ADMIN LOGIN` and market-data `SUBS` messages. The relevant
feeds are `LEVELONE_EQUITIES` for the underlying and `LEVELONE_OPTIONS` for
the exact contract symbols returned by the chain. Option quote fields include
bid and ask (2 and 3), their sizes (16 and 17), and source quote time (38).
The document says that change updates can be combined and only carry changed
fields. A collector must merge partial updates, timestamp local receipt, and
invalidate state on disconnect until it has fresh quotes. The feed is useful
for observed quote replay. It cannot establish a complete exchange tick
history or actual fill latency. The document specifies a one-connection limit
per user. No streamer session was opened during the REST pilot.

## Optional provider alternative — signup paused

November 17 priority: the user is waiting for Schwab developer approval rather
than purchasing data. Use existing captures for offline work. The comparison
below is the November 15 research snapshot, not a current signup task or spending
authorization; reverify terms before any later acquisition.

[Databento signup](https://databento.com/signup): historical usage-based access
first, **no recurring live subscription yet**. Its [credits policy](https://databento.com/docs/faqs/usage-pricing-and-data-credits)
currently advertises $125 for a new team, expiring after six months. Historical
data is metered by uncompressed binary size; exhausted credits can lead to
charges. Estimate each bounded request before download; no blanket spending
authorization exists. User handles signup/agreements; keep credentials local,
never in chat or tracked files.

Data required:

| Purpose | Dataset / schema | Selection |
|---|---|---|
| Option executable-price studies | OPRA.PILLAR CMBP-1, trades, definitions | Explicit SPXW contracts; bid/ask, sizes, conditions, event/receive timestamps |
| Futures confirmation and execution | GLBX.MDP3 MBP-1, trades, definitions | Explicit ES/MES expiry contracts, no continuous-contract execution prices |
| Underlying index context | Confirm SPX index coverage separately | Do not relabel ES or SPY as SPX |

[OPRA coverage](https://databento.com/docs/datasets/opra-pillar) has an important
boundary: before March 28, 2023, highest BBO resolution is CBBO-1m; CMBP-1,
CBBO-1s and TCBBO are unavailable. Earlier years cannot validate second-level
execution. CMBP-1 is NBBO/top-of-book, not full options order queues.

Pilot: resolve definitions and cost for a few explicit near-ATM/OTM calls and
puts, 0DTE and roughly 7DTE, during one hour on a predefined date. Then obtain
complete fixed calendar weeks, including quiet sessions and event sessions,
before estimating conditional outcomes. Prior dramatic days are retrospective
case studies only. Never download ALL_SYMBOLS or an entire OPRA year by default.

## Alternative providers

[ThetaData pricing](https://www.thetadata.net/pricing) currently lists Options
Standard $80/month and Pro $160/month. Standard exposes historical tick options
back to 2016; stock and index products have separate entitlements and different
granularity. Options Standard is not a complete options-plus-underlying budget.
Its terminal is required for the documented API and has not been installed.
[Subscription matrix](https://www.thetadata.net/docs/Articles/Getting-Started/Subscriptions.html).

Massive's [options flat-file documentation](https://massive.com/docs/flat-files/options/overview)
lists Options Advanced at $199/month with tick quotes. Separate underlying and
futures coverage must be checked. No provider subscription was purchased.

Robinhood MCP currently provides option OHLC history and current quote snapshots,
not a historical NBBO event tape. Polling it is not a substitute for this data.

## Integration boundary

Next implementation belongs in adapters: preserve raw payloads and provenance;
normalize explicit timezone/precision; keep event time distinct from availability;
retain bad/crossed/one-sided flags; resolve option exercise/settlement/multiplier
and futures expiry/tick metadata. Do not squeeze quote updates into OHLC Bars.
Only then add quote replay, conservative size/latency assumptions and forward
executable-bid outcome measurements. Source precision and broker latency remain
separate concerns. This milestone does not change the C++ core or strategy API.

## Replay a recorded Schwab option stream

For an existing local NDJSON journal, run the following command with the exact
padded contract key saved at subscription. The output path must be new.

```sh
.venv/bin/python -m qte.brokers.schwab_stream_replay --capture build/option-journal.ndjson --contract 'SPXW  261120P06500000' --max-age-ms 1000 --output build/normalized-quotes.ndjson
```

Each journal row has a collector `received_utc_ns` integer and an `event`.
Record `subscribed` with the acknowledged `contracts` list, `data` with
the provider's original `message` containing LEVELONE_OPTIONS SUBS packets,
and `disconnect` when the connection ends. A new acknowledged subscription
starts with an empty book. Collect only market-data messages in this file.
The adapter merges changed fields for each contract in receipt order. An
eligible quote needs bid/ask and sizes, Normal status, an explicit real-time
indicator and a fresh source time. Gaps in these facts produce an unavailable
record with a reason. The output records separate quote, packet and receipt
clocks and a digest of the input bytes. This path has been exercised with
protocol fixtures.

## Record SPX and SPXW during market hours

Start from the repository root with the optional `live` dependencies
installed in the QTE virtual environment. Authenticate through the existing
QTE Schwab login. The streaming app needs permission to read User Preferences
and both Level One services. Enter the app key and secret at the local hidden
prompts. Select an actual contract key from the current chain. To capture
6.5 hours for the local Live Desk, the command has this form:

```sh
.venv/bin/python -m qte.brokers.live_recorder --contract 'SPXW  261127P06500000' --max-age-ms 1500 --duration-seconds 23400 --output build/forward/spxw-session-1 --desk
```

The symbol above illustrates the fixed-width format. Each run requires a new
output directory. `--desk` publishes the recorder status at the fixed GUI path
`build/live-desk/capture/status.json`. A single recorder may publish there at
a time, while separate sessions keep their own raw data. `--contract` can be repeated. The recorder subscribes to
the cash index `$SPX` and the selected SPXW contracts. New subscriptions
after disconnect clear all books. The reconnect budget defaults to five
across the entire run. A broker authorization or protocol error ends capture.

`raw.ndjson` contains the selected market fields after SDK decoding, with
their provider field IDs restored. `quotes.ndjson` contains normalized events
and explicit unavailability reasons. Each journal packet is flushed and
synced to disk. `status.json` is an atomically replaced snapshot for the GUI.
Interpret connectivity as the last recorded connection event. Recheck source
age against the observation clock when displaying a silent or stopped feed.

An SPX index value uses the last published price (field 3) and its source
time (field 35). Each eligible option quote includes its bid, ask, spread and
sizes in contracts. It carries the most recently available eligible index
value within the same connection epoch, or a reason the join failed. Source
and packet timestamps use provider milliseconds. Local availability is
sampled in UTC nanoseconds at SDK callback handling. Source age also includes
clock skew, provider batching and collector work. Equity/option packets
arriving between replay records remain unobserved.

Rebuild the normalized stream offline into another new directory:

```sh
.venv/bin/python -m qte.brokers.live_recorder --replay build/forward/spxw-session-1/raw.ndjson --contract 'SPXW  261127P06500000' --max-age-ms 1500 --output build/live-desk/replay-1
```

The recorder uses the SDK's subscription and synchronous callback APIs, and
QTE's locked token store. A complete market-hours recording remains a live
validation step. [SDK streaming documentation](https://schwab-py.readthedocs.io/en/latest/streaming.html)
and [SDK authentication documentation](https://schwab-py.readthedocs.io/en/latest/auth.html).
