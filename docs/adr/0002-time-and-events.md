# ADR 0002: Deterministic time and visibility

Status: recommended. Preserves Bar schema version 1.

## Data boundary

Bar times are UTC nanosecond instants; interval is `[start_time, end_time)`.
Historical completed bars are available at end_time. This is an explicit
zero-publication-delay assumption. Future delayed/revised observations need an
event envelope with availability time >= end_time, not a reinterpretation of
Bar timestamp fields. Until supported, reject such datasets at preflight.

Dataset preflight validates every Bar plus profile metadata before on_start.
Require one fixed positive interval, duration equality for all bars, unique
`(symbol, start_time, end_time)`, and strictly increasing non-overlapping bars
within each symbol stream. Reject duplicates (even identical) and out-of-order
streams; do not silently sort, merge, fill or drop rows. Input order between
different symbol streams is irrelevant: merge valid streams deterministically.
Allow gaps, record them, and never fabricate bars. No symbol/price forward-fill
is exposed as a new market observation. Universe selection and metadata are
explicit inputs; they do not establish survivorship-free data.

## Event phases at a shared timestamp T

Single-threaded processing uses `(T, phase, symbol-byte-order, local sequence)`;
symbol order is locale-independent normalized byte order, order priority is ID.
Every event/command gets one run-global increasing sequence.

| Phase | Work | Strategy visibility |
|---|---|---|
| 1 | Publish all bars ending at T; update their close marks and histories | No callback until the whole batch is published |
| 2 | Call on_bar for those bars in symbol order | Completed history through T and current committed portfolio |
| 3 | Process buffered commands in callback/submission order: cancellations, submissions as issued | No reentrant callback |
| 4 | Internally introduce opens of bars starting at T; mark all opening symbols, then execute eligible orders in ascending ID | Future Bar OHLCV objects remain private |
| 5 | Commit fills sequentially; then notify on_fill in fill-ID order | All committed phase-4 fills and updated open marks, no future high/low/close/volume |
| 6 | Process on_fill commands in order; sample portfolio equity | Commands cannot execute in the already-processed phase 4 |

Execution gets only the current open in the baseline model, not full future Bar.
Portfolio open marks at phase 4 are legitimately current information; historical
strategy bar history still contains only completed bars. At phase 2, new opens
have not yet been revealed. Orders/cancels queued by earlier callbacks become
effective in phase 3; later on_bar callbacks cannot see pending commands as
accepted state. This batching is specified, not an accidental loop consequence.

An order emitted on close at T may execute at an adjacent bar's open at T in
phase 4: this is the declared idealized close-to-next-open, zero-latency
convention. It never executes against the close or earlier open of its signal
bar. An order emitted by on_fill waits for a strictly later open event of its
symbol. Missing bars delay eligibility until that symbol's next actual open.
Other symbols' events never fill it. No synthetic intrabar timestamps.

Initial lifecycle: validate, initialize, call on_start with configured universe,
cash and empty history, process its commands, then start event replay. Orders
before a visible mark are rejected (`no_reference_price`); on_start is for
initialization, not privileged price access. At end, cancel outstanding orders,
call on_end with read-only context, and finalize results. on_end cannot trade.
Callback exceptions fail the run; there is no successful partial result.

Sample an initial equity point and one after each event timestamp, including
opens without closes. Key points by timestamp and sequence so equal-time initial
and event points are unambiguous. Unobserved held symbols use last known marks
with mark timestamps/staleness exposed; never a future price. No external wall
clock enters simulation decisions.

## Acceptance and consequences

- Changing future highs/lows/closes/volumes cannot affect any earlier callback or
  baseline fill. Reordering the supplied symbol streams leaves traces unchanged.
- Two symbols closing at T see the same published history batch. Callback-issued
  orders follow deterministic priority; strategies that compete for cash are
  deliberately sensitive to that documented priority.
- Test adjacent close/open, gaps, unequal symbol starts, fill-callback orders,
  cancellation before open, terminal cancellation, and repeat-run trace equality.
- Canonical dataset hash, normalized config, schema/execution versions, seed,
  code/build identity and source provenance accompany results. If Git identity
  is unavailable, use a source digest and explicitly record that fact.
- Baseline has no randomness. A future stochastic model owns its seeded RNG and
  records algorithm/version. No unordered-container iteration drives decisions.

An open-only model avoids intrabar knowledge but is coarse. The explicit
phase model must be revised before live latency/revisions or OHLC-touch fills;
neither can be bolted on by handing full future bars to callbacks.
