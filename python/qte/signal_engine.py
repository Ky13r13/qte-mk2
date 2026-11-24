"""Causal, incremental intraday signal engine for SPX trend days and sigma reversals.

The engine consumes completed one-minute batches in strictly increasing time
order and emits immutable signal records. It never selects contracts, sizes
positions, submits orders or reads credentials. The same code path serves
historical replay, recorder-journal replay and live shadow mode, so a signal
reproduced offline is byte-identical to the one emitted live from the same
inputs.

Information availability:
- A minute bar is usable only at its right edge (``end_ns``).
- Session context (prior close, prior VIX, prior regime) comes from earlier
  sessions only.
- Observations (ATM straddle, VIX/VIX3M/gamma) are usable only once
  ``available_ns`` has passed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import fmean, pstdev
import sys
import time
from types import SimpleNamespace

from qte.regime_study import REGIMES
from qte.reversal_tail import reversal_signal
from qte.s7_tape import regime_at

MINUTE_NS = 60_000_000_000
FIVE_NS = 5 * MINUTE_NS
QUARTER_NS = 15 * MINUTE_NS
SCHEMA = "qte-signal-v1"
STRESS_REGIMES = ("stress_selloff", "high_vol_down")


def _finite(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be a finite{' positive' if positive else ''} number")
    return float(value)


def _ns(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer nanosecond timestamp")
    return value


@dataclass(frozen=True)
class MinuteBar:
    """One completed minute. ``volume`` is None for index feeds without volume."""
    start_ns: int
    end_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def __post_init__(self):
        _ns(self.start_ns, "start_ns")
        if self.end_ns - self.start_ns != MINUTE_NS or self.start_ns % MINUTE_NS:
            raise ValueError("Minute bars must be aligned and exactly one minute long")
        o, h, l, c = (_finite(getattr(self, k), k, positive=True) for k in ("open", "high", "low", "close"))
        if not l <= min(o, c) or max(o, c) > h:
            raise ValueError("Inconsistent OHLC")
        if self.volume is not None and _finite(self.volume, "volume") < 0:
            raise ValueError("volume must be nonnegative")

    @classmethod
    def from_bar(cls, bar):
        """Adapt the canonical ``qte.Bar``."""
        return cls(bar.start_ns, bar.end_ns, bar.open, bar.high, bar.low, bar.close, bar.volume)


@dataclass(frozen=True)
class SessionContext:
    """Facts known before the open. ``anchor`` is the prior regular-session close."""
    trading_date: str
    open_ns: int
    close_ns: int
    anchor: float
    expected_move: float
    vix: float
    regime: str

    def __post_init__(self):
        if _ns(self.open_ns, "open_ns") >= _ns(self.close_ns, "close_ns"):
            raise ValueError("Session must open before it closes")
        if (self.close_ns - self.open_ns) % MINUTE_NS or self.open_ns % MINUTE_NS:
            raise ValueError("Session bounds must be minute aligned")
        for name in ("anchor", "expected_move", "vix"):
            _finite(getattr(self, name), name, positive=True)
        if self.regime not in REGIMES:
            raise ValueError(f"Unknown regime {self.regime!r}")

    @classmethod
    def from_prior(cls, trading_date, open_ns, close_ns, prior_close, prior_vix, regime):
        """Daily expected move proxy: prior close x prior VIX / 100 / sqrt(252)."""
        prior_close = _finite(prior_close, "prior_close", positive=True)
        prior_vix = _finite(prior_vix, "prior_vix", positive=True)
        return cls(trading_date, open_ns, close_ns, prior_close,
                   prior_close * prior_vix / 100 / math.sqrt(252), prior_vix, regime)


@dataclass(frozen=True)
class RegimeObservation:
    """Quarter-hour VIX/VIX3M/dealer-gamma observation, usable from ``available_ns``."""
    scheduled_ns: int
    available_ns: int
    vix: float | None
    vix3m: float | None
    gamma: str | None


@dataclass(frozen=True)
class Signal:
    """A causal decision. ``status`` is ``signal`` (actionable) or ``filtered`` (diagnostic)."""
    strategy: str
    variant: str
    trading_date: str
    status: str
    decided_ns: int
    direction: int
    reference_price: float
    reasons: tuple = ()
    details: dict = field(default_factory=dict)

    @property
    def signal_id(self):
        key = f"{self.strategy}|{self.variant}|{self.trading_date}|{self.decided_ns}|{self.direction}"
        return sha256(key.encode()).hexdigest()[:20]

    @property
    def option_right(self):
        return "C" if self.direction > 0 else "P"

    @property
    def futures_side(self):
        return "buy" if self.direction > 0 else "sell"

    def record(self):
        out = asdict(self)
        out.update(schema=SCHEMA, signal_id=self.signal_id, option_right=self.option_right,
                   futures_side=self.futures_side, reasons=list(self.reasons))
        return out


class MinuteBarBuilder:
    """Aggregate last-price ticks into completed minutes keyed by provider source time.

    A minute is completed when a tick for a later minute arrives, or when the
    receipt clock passes its right edge plus ``grace_ns``. Minutes without
    ticks produce no bar. Late ticks for an already-completed minute are
    counted and dropped; emitted bars are never revised. ``reset`` discards
    the in-progress minute (for example on a disconnect).
    """

    def __init__(self, open_ns, close_ns, grace_ns=2_000_000_000):
        self.open_ns, self.close_ns, self.grace_ns = open_ns, close_ns, grace_ns
        self.current = None
        self.completed_through = open_ns
        self.late = 0
        self.outside = 0
        self.discarded = 0

    def add_tick(self, source_ns, price, received_ns=None):
        out = []
        if received_ns is not None:
            out += self.advance(received_ns)
        _finite(price, "price", positive=True)
        if not self.open_ns <= source_ns < self.close_ns:
            self.outside += 1
            return out
        start = source_ns - (source_ns - self.open_ns) % MINUTE_NS
        if start < self.completed_through:
            self.late += 1
            return out
        if self.current is not None and start > self.current[0]:
            out.append(self._finish())
        if self.current is None:
            self.current = [start, price, price, price, price]
        else:
            c = self.current
            c[2], c[3], c[4] = max(c[2], price), min(c[3], price), price
        return out

    def advance(self, now_ns):
        if self.current is not None and now_ns >= self.current[0] + MINUTE_NS + self.grace_ns:
            return [self._finish()]
        return []

    def reset(self):
        if self.current is not None:
            self.discarded += 1
            self.completed_through = self.current[0] + MINUTE_NS
            self.current = None

    def _finish(self):
        start, o, h, l, c = self.current
        self.current = None
        self.completed_through = start + MINUTE_NS
        return MinuteBar(start, start + MINUTE_NS, o, h, l, c)


class SigmaReversal:
    """Expected-move reversal from ``qte.reversal_tail`` evaluated incrementally.

    Long: prior close below BB20 lower, current positive close back inside,
    a recent low touched ``anchor - bound*move`` and close within 0.25 move.
    Short (``sides`` includes "short"): the untested mirror at the upper bound.
    Matches the research replay: first 20 bars skipped, at most
    ``max_signals`` non-overlapping signals per variant, hold must finish
    by the session close, prior-stress and intraday stress latch veto longs.
    """

    name = "sigma_reversal"

    def __init__(self, bounds=(1, 2), sides=("long",), veto=True, max_signals=5, hold_minutes=5):
        if not bounds or any(b not in (1, 2, 3) for b in bounds):
            raise ValueError("bounds must be chosen from 1, 2, 3")
        if not sides or set(sides) - {"long", "short"}:
            raise ValueError("sides must be long and/or short")
        self.bounds, self.sides, self.veto = tuple(bounds), tuple(sides), veto
        self.max_signals, self.hold_ns = max_signals, hold_minutes * MINUTE_NS

    def start(self, context):
        self.ctx = context
        self.bars = []
        self.stress = False
        self.count = {(b, s): 0 for b in self.bounds for s in self.sides}
        self.blocked = {(b, s): 0 for b in self.bounds for s in self.sides}

    def _with_bands(self, bar):
        row = dict(start_ns=bar.start_ns, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                   bb_lower=None, bb_upper=None)
        recent = self.bars[-19:] + [row]
        if len(recent) == 20 and recent[-1]["start_ns"] - recent[0]["start_ns"] == 19 * MINUTE_NS:
            mean, sd = fmean(b["close"] for b in recent), pstdev(b["close"] for b in recent)
            row.update(bb_lower=mean - 2 * sd, bb_upper=mean + 2 * sd)
        return row

    def on_minute(self, bar, volume=None):
        if bar is None:
            return []
        ctx = self.ctx
        row = self._with_bands(bar)
        self.stress |= bar.close <= ctx.anchor - 2.5 * ctx.expected_move or (
            bar.close <= ctx.anchor - 2 * ctx.expected_move and ctx.vix >= 25)
        self.bars.append(row)
        i = len(self.bars) - 1
        if ctx.regime == "unclassified" or i < 20:
            return []
        prev, recent = self.bars[i - 1], self.bars[i - 4:i + 1]
        daily = dict(anchor=ctx.anchor, expected_move=ctx.expected_move)
        out = []
        for bound in self.bounds:
            for side in self.sides:
                key = (bound, side)
                if bar.start_ns < self.blocked[key] or self.count[key] >= self.max_signals:
                    continue
                if side == "long":
                    hit = reversal_signal(prev, row, recent, daily, bound)
                    level = ctx.anchor - bound * ctx.expected_move
                else:
                    hit = _upper_reversal(prev, row, recent, ctx, bound)
                    level = ctx.anchor + bound * ctx.expected_move
                if not hit:
                    continue
                variant = f"{bound}sigma_{side}"
                details = dict(level=level, bb_lower=row["bb_lower"], bb_upper=row["bb_upper"],
                               entry_after_ns=bar.end_ns, exit_by_ns=bar.end_ns + self.hold_ns + MINUTE_NS,
                               stress_latched=self.stress, prior_regime=ctx.regime)
                reasons = []
                if self.veto and side == "long" and (self.stress or ctx.regime in STRESS_REGIMES):
                    reasons.append("stress_veto")
                if bar.end_ns + self.hold_ns + MINUTE_NS > ctx.close_ns:
                    reasons.append("hold_past_close")
                status = "filtered" if reasons else "signal"
                if status == "signal":
                    self.count[key] += 1
                    self.blocked[key] = bar.start_ns + self.hold_ns + MINUTE_NS
                out.append(Signal(self.name, variant, ctx.trading_date, status, bar.end_ns,
                                  1 if side == "long" else -1, bar.close, tuple(reasons), details))
        return out


def _upper_reversal(previous, current, recent, ctx, bound):
    if previous["bb_upper"] is None or current["bb_upper"] is None:
        return False
    level = ctx.anchor + bound * ctx.expected_move
    return (previous["close"] > previous["bb_upper"]
            and current["close"] <= current["bb_upper"]
            and current["close"] < current["open"]
            and max(b["high"] for b in recent) >= level
            and abs(current["close"] - level) <= .25 * ctx.expected_move)


class TrendDay:
    """S7 opening-range breakout from ``qte.strategies.trend_day_convexity`` evaluated incrementally.

    Opening range: first 30 minutes as six complete five-minute slots. From
    the 35-minute slot through min(240, session-15), a completed five-minute
    close outside the range is a candidate. It is actionable only when proxy
    volume for the slot exceeds ``volume_ratio`` x the mean of the previous 20
    sessions, the range is narrower than ``max_range_fraction`` of the ATM
    straddle frozen at range formation, and the quarter-hour regime
    observation is eligible (negative gamma or VIX/VIX3M > 1.02). The first
    actionable candidate ends the session; a missing slot halts it.
    """

    name = "trend_day"

    def __init__(self, volume_ratio=1.5, max_range_fraction=0.6):
        self.volume_ratio, self.max_range_fraction = volume_ratio, max_range_fraction

    def start(self, context, volume_baseline=None):
        self.ctx = context
        self.baseline = dict(volume_baseline or {})
        self.minutes, self.volumes = {}, {}
        self.slots, self.slot_volume = {}, {}
        self.next_slot = 5
        self.done = False
        self.straddles = []
        self.regimes = {}
        session_minutes = (context.close_ns - context.open_ns) // MINUTE_NS
        self.cutoff = min(240, session_minutes - 15)
        self.status = "pending"

    def observe_straddle(self, available_ns, points):
        self.straddles.append((available_ns, _finite(points, "straddle", positive=True)))

    def observe_regime(self, obs):
        self.regimes[obs.scheduled_ns] = dict(available_ns=obs.available_ns, vix=obs.vix,
                                              vix3m=obs.vix3m, gamma=obs.gamma)

    def _halt(self, status):
        self.done, self.status = True, status
        return []

    def on_minute(self, index_bar, volume_bar=None):
        if self.done:
            return []
        ctx = self.ctx
        if index_bar is not None:
            self.minutes[index_bar.start_ns] = index_bar
        if volume_bar is not None and volume_bar.volume is not None:
            self.volumes[volume_bar.start_ns] = volume_bar.volume
        end = (index_bar or volume_bar).end_ns
        out = []
        while not self.done and ctx.open_ns + self.next_slot * MINUTE_NS <= end:
            slot = self.next_slot
            # Batches are strictly ordered, so every minute of this slot has
            # either arrived or never will: completeness is final here.
            slot_end = ctx.open_ns + slot * MINUTE_NS
            keys = range(slot_end - FIVE_NS, slot_end, MINUTE_NS)
            if all(k in self.minutes for k in keys):
                rows = [self.minutes[k] for k in keys]
                self.slots[slot] = (max(r.high for r in rows), min(r.low for r in rows), rows[-1].close)
            if all(k in self.volumes for k in keys):
                self.slot_volume[slot] = sum(self.volumes[k] for k in keys)
            out += self._evaluate(slot)
            self.next_slot += 5
        return out

    def _evaluate(self, slot):
        ctx = self.ctx
        if slot <= 30:
            if slot not in self.slots:
                return self._halt("incomplete_opening_range")
            if slot == 30:
                opening = [self.slots[s] for s in range(5, 31, 5)]
                self.high = max(s[0] for s in opening)
                self.low = min(s[1] for s in opening)
                formed = ctx.open_ns + 30 * MINUTE_NS
                usable = [p for t, p in self.straddles if t <= formed]
                self.move = usable[-1] if usable else None
            return []
        if slot > self.cutoff:
            return self._halt("no_breakout" if self.status == "pending" else self.status)
        if slot not in self.slots:
            return self._halt("incomplete_index_prefix")
        close = self.slots[slot][2]
        side = 1 if close > self.high else -1 if close < self.low else 0
        if not side:
            return []
        at = ctx.open_ns + slot * MINUTE_NS
        baseline = self.baseline.get(slot)
        relative = self.slot_volume[slot] / baseline if slot in self.slot_volume and baseline else None
        regime = regime_at(SimpleNamespace(regimes=self.regimes), ctx.open_ns, at)
        reasons = []
        if relative is None:
            reasons.append("missing_proxy_volume_baseline")
        elif relative <= self.volume_ratio:
            reasons.append("weak_volume")
        if self.move is None:
            reasons.append("missing_atm_straddle")
        elif self.high - self.low >= self.max_range_fraction * self.move:
            reasons.append("wide_range")
        if regime is not True:
            reasons.append("missing_regime" if regime is None else "regime_ineligible")
        details = dict(slot=slot, range_high=self.high, range_low=self.low,
                       midpoint=(self.high + self.low) / 2, implied_points=self.move,
                       relative_volume=relative, proxy_volume=self.slot_volume.get(slot),
                       mean_prior_proxy_volume=baseline, regime_eligible=regime,
                       target_abs_delta=0.12, delta_band=(0.10, 0.15), stop_reference="range_midpoint")
        status = "filtered" if reasons else "signal"
        if status == "signal":
            self.done, self.status = True, "signal"
        else:
            self.status = "filtered"
        return [Signal(self.name, "opening_range_30m", ctx.trading_date, status, at, side, close,
                       tuple(reasons), details)]


def volume_baseline(prior_sessions, lookback=20):
    """Mean proxy volume per five-minute slot over exactly ``lookback`` prior sessions."""
    history = list(prior_sessions)[-lookback:]
    if len(history) < lookback:
        return {}
    slots = set.intersection(*(set(h) for h in history)) if history else set()
    return {s: fmean(h[s] for h in history) for s in sorted(slots)}


def slot_volumes(bars, open_ns, close_ns):
    """Sum complete five-minute proxy volume slots keyed by elapsed minutes at the right edge."""
    by_start = {b.start_ns: b.volume for b in bars}
    out = {}
    for start in range(open_ns, close_ns, FIVE_NS):
        keys = range(start, start + FIVE_NS, MINUTE_NS)
        if all(by_start.get(k) is not None for k in keys):
            out[(start + FIVE_NS - open_ns) // MINUTE_NS] = sum(by_start[k] for k in keys)
    return out


class SignalEngine:
    """Run strategies over strictly ordered minute batches for one session."""

    def __init__(self, context, *, reversal=None, trend=None, volume_baseline=None):
        self.ctx = context
        self.reversal, self.trend = reversal, trend
        self.last_end = context.open_ns
        if reversal:
            reversal.start(context)
        if trend:
            trend.start(context, volume_baseline)

    def observe_straddle(self, available_ns, points):
        if self.trend:
            self.trend.observe_straddle(available_ns, points)

    def observe_regime(self, obs):
        if self.trend:
            self.trend.observe_regime(obs)

    def on_minute(self, end_ns, index=None, volume=None):
        """Process every bar ending at ``end_ns`` together, after all have arrived."""
        if end_ns <= self.last_end or end_ns > self.ctx.close_ns or (end_ns - self.ctx.open_ns) % MINUTE_NS:
            raise ValueError("Minute batches must be aligned, in session and strictly increasing")
        for bar in (index, volume):
            if bar is not None and bar.end_ns != end_ns:
                raise ValueError("Batch bars must end at the batch timestamp")
        self.last_end = end_ns
        out = []
        if self.reversal and index is not None:
            out += self.reversal.on_minute(index, volume)
        if self.trend and (index is not None or volume is not None):
            out += self.trend.on_minute(index, volume)
        return out


# ----------------------------------------------------------------- journal / CLI

def _encode(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def load_contexts(path):
    """Read ``qte-signal-context-v1``: per-session prior facts and optional observations."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    if doc.get("schema") != "qte-signal-context-v1" or type(doc.get("sessions")) is not list:
        raise ValueError("Expected a qte-signal-context-v1 document with a sessions list")
    sessions = {}
    for item in doc["sessions"]:
        allowed = {"trading_date", "open_ns", "close_ns", "prior_close", "prior_vix", "regime",
                   "straddles", "regimes"}
        if set(item) - allowed:
            raise ValueError(f"Unknown context fields: {sorted(set(item) - allowed)}")
        ctx = SessionContext.from_prior(item["trading_date"], item["open_ns"], item["close_ns"],
                                        item["prior_close"], item["prior_vix"], item["regime"])
        if ctx.trading_date in sessions:
            raise ValueError("Duplicate session in context")
        straddles = [(_ns(s["available_ns"], "available_ns"), s["points"]) for s in item.get("straddles", [])]
        regimes = [RegimeObservation(r["scheduled_ns"], r["available_ns"], r.get("vix"), r.get("vix3m"),
                                     r.get("gamma")) for r in item.get("regimes", [])]
        sessions[ctx.trading_date] = (ctx, straddles, regimes)
    return sessions, sha256(raw).hexdigest()


def run_session(ctx, index_bars, *, volume_bars=(), straddles=(), regimes=(), baseline=None,
                reversal=None, trend=None):
    """Replay one session through the same incremental path used live."""
    engine = SignalEngine(ctx, reversal=reversal, trend=trend, volume_baseline=baseline)
    for t, p in straddles:
        engine.observe_straddle(t, p)
    for r in regimes:
        engine.observe_regime(r)
    index = {b.end_ns: b for b in index_bars if ctx.open_ns < b.end_ns <= ctx.close_ns}
    volume = {b.end_ns: b for b in volume_bars if ctx.open_ns < b.end_ns <= ctx.close_ns}
    out = []
    for end in sorted(set(index) | set(volume)):
        out += engine.on_minute(end, index.get(end), volume.get(end))
    return out


def replay(minutes_path, symbol, context_path, output, *, volume_path=None, sides=("long",)):
    from qte.adapters.schwab_research import read_minute_sessions
    output = Path(output)
    contexts, context_digest = load_contexts(context_path)
    index, index_digest = read_minute_sessions(Path(minutes_path), symbol)
    sources = [dict(file=Path(minutes_path).name, sha256=index_digest),
               dict(file=Path(context_path).name, sha256=context_digest)]
    volume = {}
    if volume_path:
        volume, digest = read_minute_sessions(Path(volume_path), "SPY")
        sources.append(dict(file=Path(volume_path).name, sha256=digest))
    history = []
    records, summary = [], {}
    for day in sorted(set(index) | set(volume)):
        vol_bars = [MinuteBar.from_bar(b) for b in volume.get(day, [])]
        if day in contexts and day in index:
            ctx, straddles, regimes = contexts[day]
            signals = run_session(ctx, [MinuteBar.from_bar(b) for b in index[day]], volume_bars=vol_bars,
                                  straddles=straddles, regimes=regimes, baseline=volume_baseline(history),
                                  reversal=SigmaReversal(sides=sides), trend=TrendDay())
            records += [s.record() for s in signals]
            summary[day] = sum(s.status == "signal" for s in signals)
        if day in contexts and vol_bars:
            ctx = contexts[day][0]
            history.append(slot_volumes(vol_bars, ctx.open_ns, ctx.close_ns))
    output.mkdir(parents=True, exist_ok=False)
    (output / "signals.ndjson").write_bytes(b"".join(_encode(r) + b"\n" for r in records))
    manifest = dict(schema="qte-signal-replay-v1", symbol=symbol, sides=list(sides), sources=sources,
                    sessions=len(summary), actionable=sum(summary.values()), per_session=summary,
                    signals_sha256=sha256((output / "signals.ndjson").read_bytes()).hexdigest())
    (output / "manifest.json").write_bytes(_encode(manifest) + b"\n")
    return manifest


class RecorderFeed:
    """Turn ``live_recorder`` quotes.ndjson underlying rows into engine minutes."""

    def __init__(self, engine):
        self.engine = engine
        ctx = engine.ctx
        self.builder = MinuteBarBuilder(ctx.open_ns, ctx.close_ns)
        self.signals = []

    def _emit(self, bars):
        for bar in bars:
            self.signals += self.engine.on_minute(bar.end_ns, bar)

    def apply(self, row):
        kind = row.get("event")
        received = row.get("received_utc_ns")
        if kind in ("subscribed", "disconnect", "underlying_unavailable"):
            if received is not None:
                self._emit(self.builder.advance(received))
            self.builder.reset()
        elif kind == "underlying":
            self._emit(self.builder.add_tick(row["source_trade_ms"] * 1_000_000, row["price"], received))
        elif received is not None:
            self._emit(self.builder.advance(received))

    def finish(self):
        self._emit(self.builder.advance(self.engine.ctx.close_ns + MINUTE_NS + self.builder.grace_ns))


def follow(quotes_path, ctx, output, *, poll_seconds=1.0, stop_after_close=True, sides=("long",),
           sleep=time.sleep, clock=time.time_ns):
    """Shadow mode: tail a growing recorder journal and append signals as they occur."""
    engine = SignalEngine(ctx, reversal=SigmaReversal(sides=sides), trend=TrendDay())
    feed = RecorderFeed(engine)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    written = 0
    with Path(quotes_path).open("rb") as stream, (output / "signals.ndjson").open("xb") as sink:
        buffer = b""
        while True:
            chunk = stream.read(1 << 20)
            if chunk:
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if line:
                        feed.apply(json.loads(line))
            for s in feed.signals[written:]:
                sink.write(_encode(s.record()) + b"\n")
                sink.flush()
                print(f"{s.status.upper()} {s.strategy}/{s.variant} dir={s.direction:+d} "
                      f"px={s.reference_price:.2f} {','.join(s.reasons)}", flush=True)
            written = len(feed.signals)
            if not chunk:
                if stop_after_close and clock() > ctx.close_ns + 2 * MINUTE_NS:
                    feed.finish()
                    for s in feed.signals[written:]:
                        sink.write(_encode(s.record()) + b"\n")
                    break
                sleep(poll_seconds)
    return feed.signals


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qte signals", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("replay", help="Replay Schwab minute captures through the engine")
    rp.add_argument("--minutes", type=Path, required=True)
    rp.add_argument("--symbol", default="$SPX")
    rp.add_argument("--context", type=Path, required=True)
    rp.add_argument("--volume", type=Path, help="SPY minute capture used as trend-day volume proxy")
    rp.add_argument("--output", type=Path, required=True)
    rp.add_argument("--short", action="store_true", help="Also emit untested upper-bound short reversals")
    fp = sub.add_parser("follow", help="Shadow mode: tail a live_recorder quotes.ndjson")
    fp.add_argument("--quotes", type=Path, required=True)
    fp.add_argument("--context", type=Path, required=True)
    fp.add_argument("--date", required=True)
    fp.add_argument("--output", type=Path, required=True)
    fp.add_argument("--short", action="store_true")
    fp.add_argument("--no-wait", action="store_true", help="Process the file once and stop")
    args = parser.parse_args(argv)
    sides = ("long", "short") if args.short else ("long",)
    try:
        if args.command == "replay":
            manifest = replay(args.minutes, args.symbol, args.context, args.output,
                              volume_path=args.volume, sides=sides)
            print(f"{manifest['sessions']} sessions, {manifest['actionable']} actionable signals -> "
                  f"{args.output / 'signals.ndjson'}")
        else:
            contexts, _ = load_contexts(args.context)
            if args.date not in contexts:
                parser.error("Context file has no entry for --date")
            ctx, straddles, regimes = contexts[args.date]
            if args.no_wait:
                signals = follow(args.quotes, ctx, args.output, sides=sides, sleep=lambda _: None,
                                 clock=lambda: ctx.close_ns + 3 * MINUTE_NS)
            else:
                signals = follow(args.quotes, ctx, args.output, sides=sides)
            print(f"{sum(s.status == 'signal' for s in signals)} actionable signals")
    except (OSError, ValueError, KeyError) as exc:
        print(f"signal engine failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
