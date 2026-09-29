# ADR 0008: Explicit observation grid for analytics

Status: M13b implemented in the C++ analytics layer and Python bindings.

Replay retains the initial equity point and every event sample, with ordered
sequences even when timestamps coincide. The raw curve is never rewritten.

`SamplingConfig(timestamps_ns, max_staleness_ns)` declares a strictly increasing
UTC grid with at least two points and the run's exact start/end boundaries.
For each timestamp select the last sequence whose event time is at or before
it. Equal-time initial/event samples therefore collapse to the final committed
value. Future observations are never consulted. Missing observations may carry
forward only within the caller's maximum age; otherwise sampling fails. This
age refers to equity events, not individual held-symbol mark age.

Calendar/session policy belongs to the grid supplier. To exclude overnight or
weekend points, supply explicit session observation times. No exchange holiday
calendar or session inference is built in. Each adjacent selected observation
is one declared return period for volatility, Sharpe and Sortino; the caller
must supply the appropriate periods-per-year. This permits unequal wall-clock
spacing between sessions deliberately. CAGR continues to use actual elapsed
UTC years (365.25 days). Initial/final partial sessions must be accounted for
when choosing the grid and annualization convention.

The `analyze` overload accepts this sampling policy; raw annualization without a
policy continues rejecting irregular/duplicate spacing. Returns and return-based
ratios use sampled equity. Drawdown and time-weighted exposure still use the full
event series so sampling cannot hide an intra-period loss or exposure interval.
Annualized overflow is undefined with a reason, not an exception or infinity.
Run manifests exported by the research command preserve the exact grid, maximum
age, periods-per-year, risk-free rate input, and policy version.
