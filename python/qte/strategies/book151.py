"""Explicit research adaptations of Kakushadze/Serur sections 3, 4 and 10.

ETF wrappers use QTE's existing order lifecycle. Futures policies return weights
only: they do not bypass the derivative execution/accounting gates.
"""
from dataclasses import dataclass
import math
from statistics import fmean, pstdev

from .candidates import _LongOnlyStrategy, _atr
from .nasdaq_gamma import FuturesMark, _timestamp


BOOK_FAMILIES = ("book_single_ma", "book_triple_ma", "book_channel_reversal", "book_ibs_reversal")


@dataclass(frozen=True)
class BookConfig:
    kind: str = "book_single_ma"
    allocation_fraction: float = 0.10
    fast_period: int = 3
    middle_period: int = 10
    slow_period: int = 21
    channel_period: int = 20
    ibs_entry: float = 0.20
    ibs_exit: float = 0.80
    atr_period: int = 14
    stop_atr_multiple: float = 2.5
    max_holding_bars: int = 20

    def __post_init__(self):
        if self.kind not in BOOK_FAMILIES:
            raise ValueError("unknown book strategy")
        for name in ("fast_period", "middle_period", "slow_period", "channel_period", "atr_period", "max_holding_bars"):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= 10000:
                raise ValueError("lookbacks and holding limits must be integers in [1,10000]")
        if self.kind == "book_triple_ma" and not self.fast_period < self.middle_period < self.slow_period:
            raise ValueError("fast < middle < slow required")
        for name in ("allocation_fraction", "ibs_entry", "ibs_exit", "stop_atr_multiple"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("strategy parameters must be finite")
        if not 0 < self.allocation_fraction <= 1 or self.stop_atr_multiple <= 0:
            raise ValueError("invalid sizing or stop")
        if not 0 <= self.ibs_entry < self.ibs_exit <= 1:
            raise ValueError("IBS thresholds must satisfy 0 <= entry < exit <= 1")

    @property
    def history_required(self):
        return max(self.atr_period + 1, {
            "book_single_ma": self.slow_period + 1,
            "book_triple_ma": self.slow_period,
            "book_channel_reversal": self.channel_period + 1,
            "book_ibs_reversal": 1,
        }[self.kind])


def book_signals(history, config):
    """Entry, exit, ATR. Input is completed canonical bars visible to the caller."""
    if len(history) < config.history_required:
        raise ValueError("insufficient book strategy history")
    closes = [b.close for b in history]
    price = closes[-1]
    if config.kind == "book_single_ma":
        mean = fmean(closes[-config.slow_period - 1:-1])
        enter, exit_ = price > mean, price < mean
    elif config.kind == "book_triple_ma":
        fast = fmean(closes[-config.fast_period:])
        middle = fmean(closes[-config.middle_period:])
        slow = fmean(closes[-config.slow_period:])
        enter, exit_ = fast > middle > slow, fast <= middle
    elif config.kind == "book_channel_reversal":
        previous = closes[-config.channel_period - 1:-1]
        floor, ceiling = min(previous), max(previous)
        # Equality in the source is broadened to touching/crossing the boundary.
        enter, exit_ = floor < ceiling and price <= floor, price >= ceiling
    else:
        bar = history[-1]
        ibs = None if bar.high == bar.low else (bar.close - bar.low) / (bar.high - bar.low)
        enter, exit_ = ibs is not None and ibs <= config.ibs_entry, ibs is not None and ibs >= config.ibs_exit
    return enter, exit_, _atr(history, config.atr_period)


class BookStrategy(_LongOnlyStrategy):
    """Cash-equity/ETF implementation only; no SPX-index or futures-as-shares fills."""
    def __init__(self, symbol, config: BookConfig, regime_filter=None):
        if not isinstance(config, BookConfig):
            raise TypeError("config must be BookConfig")
        super().__init__(symbol, config, regime_filter)

    def _signals(self, history):
        return book_signals(history, self.config)


def futures_weights(histories, *, at_ns, interval_ns, lookback=20,
                    volatility_period=20, max_age_ns=0, kind="time_series_momentum",
                    expected_timestamps=None):
    """Sections 10.3/10.4: causal continuous weights, not integer contract orders.

    Require an aligned, complete observed window from one source per instrument.
    Explicit expiry contract identifiers and roll handling belong to the caller.
    A universe with missing/stale observations fails rather than silently shrinking.
    """
    _timestamp(at_ns, "at_ns")
    if type(interval_ns) is not int or interval_ns <= 0 or type(max_age_ns) is not int or max_age_ns < 0:
        raise ValueError("positive interval and nonnegative maximum age required")
    if any(type(x) is not int or not 2 <= x <= 10000 for x in (lookback, volatility_period)):
        raise ValueError("lookbacks must be integers in [2,10000]")
    if kind not in ("time_series_momentum", "cross_sectional_contrarian"):
        raise ValueError("unsupported futures policy")
    if not isinstance(histories, dict) or not histories or any(not isinstance(k, str) or not k for k in histories):
        raise ValueError("nonempty named futures universe required")
    if kind == "cross_sectional_contrarian" and len(histories) < 2:
        raise ValueError("cross-sectional contrarian needs at least two instruments")
    returns, vols, clocks = {}, {}, set()
    required = max(lookback, volatility_period) + 1
    if expected_timestamps is not None:
        expected_timestamps = tuple(expected_timestamps)
        for stamp in expected_timestamps:
            _timestamp(stamp, "expected timestamp")
        if len(expected_timestamps) != required or any(a >= b for a, b in zip(expected_timestamps, expected_timestamps[1:])):
            raise ValueError("explicit session grid must be ordered with exactly the required observations")
    for symbol in sorted(histories):
        rows = list(histories[symbol])
        if any(not isinstance(row, FuturesMark) or row.underlying != symbol for row in rows):
            raise ValueError("invalid mark or instrument mismatch")
        visible = sorted((r for r in rows if r.available_ns <= at_ns), key=lambda r: r.reference_ns)
        if len({r.reference_ns for r in visible}) != len(visible):
            raise ValueError("duplicate/revised mark requires explicit vintage selection")
        visible = visible[-required:]
        if len(visible) != required or len({r.source_id for r in visible}) != 1:
            raise ValueError("insufficient history or mixed sources")
        if at_ns - visible[-1].reference_ns > max_age_ns:
            raise ValueError("stale futures window")
        if expected_timestamps is not None:
            if tuple(r.reference_ns for r in visible) != expected_timestamps:
                raise ValueError("window does not match the explicit session grid")
        elif any(b.reference_ns - a.reference_ns != interval_ns for a, b in zip(visible, visible[1:])):
            raise ValueError("gapped window; use an explicit session/roll adapter")
        clocks.add(tuple(r.reference_ns for r in visible))
        prices = [r.price for r in visible]
        returns[symbol] = prices[-1] / prices[-lookback - 1] - 1
        vols[symbol] = pstdev([b / a - 1 for a, b in zip(prices[-volatility_period - 1:], prices[-volatility_period:])])
        if not math.isfinite(returns[symbol]) or not math.isfinite(vols[symbol]):
            raise ValueError("nonfinite futures feature")
    if len(clocks) != 1:
        raise ValueError("cross-instrument timestamps must align")
    if kind == "time_series_momentum":
        if any(v <= 1e-12 for v in vols.values()):
            raise ValueError("zero/near-zero volatility cannot support inverse-volatility sizing")
        raw = {s: ((r > 0) - (r < 0)) / vols[s] for s, r in returns.items()}
    else:
        mean = fmean(returns.values())
        raw = {s: -(r - mean) for s, r in returns.items()}
    gross = math.fsum(abs(w) for w in raw.values())
    return {"kind": kind, "decision_ns": at_ns,
            "weights": {s: w / gross if gross else 0.0 for s, w in raw.items()},
            "returns": returns, "volatility": vols,
            "units": "gross-normalized notional weights, not contracts",
            "execution_enabled": False}
