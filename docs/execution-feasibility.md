# Retail execution feasibility — November 19, 2026

Schwab is the preferred integration candidate for a direct QTE order gateway,
not an established winner on execution quality. No live latency experiment or
test trade was performed. Existing QTE Schwab support is OAuth and read-only
requests; a live gateway remains unimplemented.

## Verified evidence

- Schwab accepts payment for order flow (PFOF), including rebates from liquidity
  providers and exchanges. Moving there does not eliminate that conflict.
  [Schwab routing disclosure](https://www.schwab.com/legal/order-routing-1).
- Robinhood's Q3 2026 Rule 606 report, generated October 30, states that it does
  not receive payments for index-option orders. This matters for SPX/SPXW;
  do not generalize equity-option PFOF to these products. It does not prove
  good fills or absence of other execution costs.
  [Robinhood report](https://cdn.robinhood.com/assets/robinhood/legal/RHS%20SEC%20Rule%20606%20and%20607%20Disclosure.pdf).
- Schwab advertises average execution of 0.05 seconds for Q3 2026. Its disclosure
  covers market/marketable limit orders in exchange-listed stocks during normal
  hours; the clock starts at Schwab routing and ends at market-center execution.
  This is neither an options benchmark nor client-to-fill latency, a percentile,
  a guarantee, or sustained order throughput.
  [Headline](https://www.schwab.com/execution-quality),
  [measurement disclosure](https://international.schwab.com/why-charles-schwab).
- Schwab offers an individual Trader API for self-directed applications.
  [Product](https://developer.schwab.com/products/trader-api--individual).
  Detailed current account entitlements, order throttles and streaming contracts
  still need verification against the user's official portal export. No claimed
  orders-per-second limit or end-to-end latency has been established.

## Feasible direction

The assistant/MCP path includes model/tool orchestration and potentially human
approval. It has no established upper latency bound and is unsuitable as a
deterministic subsecond execution loop. Several contracts in one order is not
the same requirement as several complete buy/sell round trips each second.

Build toward streaming quotes -> deterministic strategy -> local risk checks ->
direct broker adapter -> acknowledgements/fills -> authoritative reconciliation.
Keep the LLM outside that loop. A subsecond signal-to-ack target is an engineering
objective to measure, not an observed or promised capability. An acknowledgement
is not a fill, and a fill notification can lag the actual execution. Passive
limits can remain unfilled indefinitely; marketable limits bound price but do
not guarantee a fill. Protect against stale quotes, duplicate submissions,
partial fills, disconnects and uncertain order state before live use.

## Smallest useful measurement milestone

1. Authenticate Schwab locally and verify official streaming/order contracts and
   account permissions. Record quote timestamps, local receipt times and gaps.
2. Run QTE decisions in shadow mode, recording monotonic signal-to-order-intent
   timing. This measures local processing and data delivery, not real fills.
3. Only after separate live-order authorization, compare matched instruments,
   sizes, session conditions and order policies across brokers. Capture submission,
   acknowledgement, first/full fill, and client notification separately. Use
   monotonic clocks locally; document clock uncertainty for provider timestamps.
4. Report p50/p95/p99, rejects, timeouts, partial/unfilled orders, effective spread,
   commissions/fees and adverse price movement after fills. Keep unfilled orders
   in the comparison to avoid selection bias. Paper fills cannot establish real
   queue position or execution quality.

For options with a 100 multiplier, crossing a $0.10 spread costs $10 per contract
on an immediate unchanged-market round trip, before fees. Speed is valuable only
if the strategy's edge survives those costs; faster submission is not evidence
of profitability. Current volume studies do not validate a fast-trading edge.
