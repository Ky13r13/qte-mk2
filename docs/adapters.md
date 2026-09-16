# Offline data adapters

Adapters are Python boundary components. They may understand vendor fields;
canonical C++ market-data and engine modules may not. Both initial adapters read
complete local files, copy values into `Dataset`, hash the original bytes, and
return immutable provenance alongside the dataset. They never use credentials or
the network.

## Alpaca historical stock bars

The `alpaca-stock-bars-v1` adapter targets Alpaca Market Data API v2 historical
stock-bar responses. Alpaca documents bar timestamps and OHLCV fields and notes
that multi-symbol historical results are ordered by symbol and timestamp:
<https://docs.alpaca.markets/us/v1.4.2/reference/stockbars>.

Saved fixtures add a `_qte` envelope declaring schema ID, feed, timeframe,
adjustment, and action coverage. The adapter accepts only `adjustment=raw`,
`action_free=true`, and `next_page_token=null` for the initial research profile.
Required vendor fields are `t`, `o`, `h`, `l`, `c`, and `v`; auxiliary fields are
ignored. Timestamps must use RFC3339 UTC `Z` form with at most nine fractional
digits and are converted to integer nanoseconds without Python `datetime`
precision loss. A non-null page token is rejected rather than producing a
silently truncated universe.

## Explicit CSV bars

CSV ingestion requires an explicit unique mapping for symbol, timestamp, OHLC,
and volume columns. Timestamp, interval, currency, tick, volume unit, adjustment,
and action-free assumptions are caller inputs. The adapter does not infer them
from filenames or values. Extra columns are ignored; missing mapped columns,
invalid rows, empty files, non-raw adjustment, and undeclared action coverage
fail before replay. Canonical dataset preflight remains the final validation
boundary for ordering, duration, prices, gaps, and instrument assumptions.
