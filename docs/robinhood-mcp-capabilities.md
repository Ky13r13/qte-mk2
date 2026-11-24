# Robinhood MCP capabilities — November 19, 2026

Inspected 87 advertised tools and exercised 22 distinct read/preview tools.
Inventory and 16 public data samples: `build/robinhood-capabilities-20261119/`.
No orders, approval requests, transfers, upgrades, alerts, saved scanners or
watchlist changes were submitted. Account-specific responses were not saved.
Availability and account permissions must be rechecked before an actual action.

| Area | Available functionality | Evidence / limitations |
|---|---|---|
| Account | Balances/buying power, positions, orders, pending approvals, approval setting | Account discovery, permissions and order-status reads succeeded; assets have distinct account identifiers |
| Stocks/ETFs | Quotes, OHLCV, tradability, Level 2 snapshots, technical indicators | Live SPY/QQQ depth and minute Bollinger calculations succeeded; SPY 15-second sample returned observed bars |
| Indexes | Discovery, current SPX/NDX/VIX levels, OHLC history | Already captured in regime study; indexes have no traded share volume; licensing restrictions may apply |
| Options | Chains, contracts, AM/PM settlement metadata, ticks, multiplier, sellout time; bid/ask, sizes, IV, Greeks, OI and volume | SPX/SPXW discovery and Nov 27 7250 call/put quote reads succeeded; current Greeks are provider models, not signed dealer inventory |
| Option history | OHLC intervals down to 15 seconds | Two sampled SPXW contracts each returned 20 non-interpolated bars over 17:35–17:40 UTC today. Prior all-interpolated samples remain failures; no general historical coverage claim or historical bid/ask replay |
| Scanning | Unsaved previews, saved scanners, expression filters/columns; technical, volume, fundamental and volatility fields | Preview over SPY/QQQ/IWM/RSP/SMH returned five rows with 7-/30-day ATM IV. Separate relative-volume >=1.5 preview returned zero; neither saved |
| Company research | Fundamentals, earnings calendar/results, financials, analyst ratings, SEC filings/facts, politician disclosures | Fundamentals, earnings, quarterly financials and SEC index sampled on NVDA. Sample 10-Q had no readable text. Other endpoints catalog-only; historical publication vintages not established |
| Monitoring | Custom/curated watchlists, option watchlist, equity/crypto alerts and fired-alert logs | Catalog inspected. Writes require specific user instructions. Alert events must be polled; they do not automatically wake this assistant |
| Order workflow | Previews, placement/cancellation, equity OCO, option exercises/cancel queued exercises, approval status | Catalog only: no financial mutations or previews with invented order sizes. Equities: market/limit/stops; options: 1–4 legs depending on permission, multi-leg limit orders; crypto orders supported with linked eligible account |
| Results | Aggregate realized PnL, per-trade PnL, tax lots, order executions | Catalog inspected; useful for later reconciliation against QTE, not a substitute for a durable ledger |
| Other finance | Banking/credit account and transaction reads | Advertised but deliberately not queried: unrelated to the designated trading-account setup |

Important distinctions:
- MCP tools are available to this assistant; QTE's standalone Python/GUI does
  not automatically inherit them. Public responses can be captured for offline
  adapters. No always-running MCP trading daemon was implemented.
- Stock fractional/dollar orders are regular-session market/GFD only through
  these tools, even when instrument flags advertise broader app capabilities.
  Whole-share limits/stops have different constraints. Verify tradability anew.
- Option strategies require appropriate approval, collateral and contract rules.
  An approved account is not proof every proposed order is affordable/eligible.
- Equity OCO supports regular hours and broker-defined minimum price separation.
  It is not an option bracket or a guaranteed stop fill. No futures quote/history
  or futures order tool is advertised, although scanners/watchlists may reference
  futures. No advertised brokerage funding/withdrawal, true tick stream, historical
  NBBO, historical depth replay or generic macro-news feed.
- Level 2 is a momentary resting-liquidity snapshot, not executed aggressive flow
  or proof of dealer hedging. No venue-completeness/latency guarantee established.
- Scanner ATM-IV maturities may use the nearest listed expiry, not an interpolated
  constant-maturity curve. Values are decimal annualized volatility; 0.24 = 24%.
  IV versus historical volatility alone is not a tradable pricing discrepancy.
- Scanner relative volume uses cumulative daily volume / an average full-day
  volume. It is NOT QTE's prior-session same-clock cumulative-volume ratio. Keep
  lookback, session, source and availability semantics explicit.
- Quotes and bars need timestamp/interpolation checks. Observed current candles
  do not establish complete historical option listings, executable spread prices,
  corporate-action adjustments or a profitable strategy.

Priority integration work: public-data capture with source/retrieval timestamps;
regime/IV term-structure features; bounded option-chain surface snapshots;
frozen strategy comparison; then shadow order intent and reconciliation. Any
live test needs an explicit order/risk mandate and fresh permissions/buying power.
No funding increase is required to explore the read-only research tools.
