# ADR 0009: Causal regime research without engine specialization

Status: implemented research policy and synthetic verification; economic
validation, live feeds and production trading remain unimplemented.

## Decision

Keep indicators, macro classification, candidate selection and routing in Python
research/strategy modules. Reuse the C++ event loop, order/risk/portfolio authority
unchanged. Expose only read-only dataset currency metadata needed to reject
incompatible comparisons. Preserve canonical Bar schema and availability rules.

Macro observations have separate reference and availability timestamps, source
and vintage identities. As-of queries choose only available releases; revisions
to older periods do not replace a newer visible period. Missing/stale inputs
block new risk. Standardization must be causal and provider-independent. The
initial equal-weight score is a configurable hypothesis, not a proven model.

Dealer signed-gamma inputs require external provenance, methodology, units and
underlying identity. Proxies never become observed inventory. Ignoring dealer
inputs is explicit; required mode blocks without a qualified fresh observation.
The overlay only removes permissions and cannot override the macro risk veto.

Each candidate uses one pending order and actual fill feedback. Macro exits and
close stops execute at the next eligible open, not instantly. A multi-family
router must use one engine portfolio and close an active position before another
family can enter. Independently profitable strategy segments cannot be spliced
into a claimed portfolio track record.

Freeze the slate, settings, data/source identity and screening thresholds before
running. Train is diagnostic; validation alone selects; only the locked winner
sees final test data. Retain failures and all candidates. Return no selection
when evidence is synthetic or gates fail. Benchmarks, costs and zero-trade windows
remain visible. Zero-return risk-off windows are legitimate; require aggregate
activity/return and bounded per-window losses rather than profit in every regime.

## Tradeoffs and boundaries

Small Python policies are easy to inspect but slower than C++ indicators. Measure
first; an observed contraction-indicator bottleneck was reduced by skipping its
expensive confirmation when the required breakout is false, with equivalence
tests. Cross-run cached indicator state and core optimizations are not introduced.

The initial lab uses fabricated 1-hour/24-hour clocks, not exchange sessions.
No real macro/dealer data, broker account or economic merit is implied. Session
adapters, corporate actions, walk-forward fitting, uncertainty/multiple-testing
analysis and live/paper reconciliation have separate acceptance gates.

Python factories and closures cannot be sandboxed by this research harness.
Hashes record declared evidence, not its truth or external preregistration time.
Use fresh processes after edits and independently audit real-data provenance.

See [lab](../strategies/strategy-lab.md), [candidate rules](../strategies/candidate-library.md),
[regime policy](../strategies/regime-framework.md), and
[selection protocol](../strategies/selection-protocol.md).
