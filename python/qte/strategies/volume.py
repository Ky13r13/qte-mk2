"""Common bar-volume hypotheses; not order flow or demonstrated alpha."""
from dataclasses import dataclass
import math
from statistics import fmean

from .candidates import _LongOnlyStrategy, _atr

VOLUME_FAMILIES = ("volume_breakout", "vwma_trend", "obv_confirmation", "cmf_trend", "mfi_reversal")


@dataclass(frozen=True)
class VolumeConfig:
    kind: str = "volume_breakout"
    allocation_fraction: float = 0.10
    fast_period: int = 10
    slow_period: int = 50
    breakout_period: int = 20
    exit_period: int = 10
    volume_period: int = 20
    mfi_period: int = 14
    relative_volume: float = 1.5
    cmf_entry: float = 0.10
    mfi_entry: float = 20.0
    mfi_exit: float = 50.0
    atr_period: int = 14
    stop_atr_multiple: float = 2.5
    max_holding_bars: int = 20

    def __post_init__(self):
        if self.kind not in VOLUME_FAMILIES:
            raise ValueError(f"kind must be one of {VOLUME_FAMILIES}")
        for name in ("fast_period", "slow_period", "breakout_period", "exit_period",
                     "volume_period", "mfi_period", "atr_period", "max_holding_bars"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.kind == "vwma_trend" and self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        for name in ("allocation_fraction", "relative_volume", "cmf_entry", "mfi_entry",
                     "mfi_exit", "stop_atr_multiple"):
            value = getattr(self, name)
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value > 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"{name} must be positive and finite")
        if self.allocation_fraction > 1 or self.cmf_entry >= 1:
            raise ValueError("allocation_fraction must be <=1 and cmf_entry <1")
        if not self.mfi_entry < self.mfi_exit < 100:
            raise ValueError("Require 0 < mfi_entry < mfi_exit < 100")

    @property
    def history_required(self):
        required = {
            "volume_breakout": max(self.breakout_period, self.exit_period, self.volume_period)+1,
            "vwma_trend": self.slow_period,
            "obv_confirmation": max(self.slow_period, self.volume_period+1),
            "cmf_trend": max(self.slow_period, self.volume_period),
            "mfi_reversal": self.mfi_period+2,
        }[self.kind]
        return max(required, self.atr_period+1)


def _weighted_price(bars):
    total = math.fsum(b.volume for b in bars)
    if total <= 0:
        return None
    anchor = bars[-1].close
    # Centering avoids a false crossover on constant prices from separately
    # rounded normalized weights in windows with different lengths.
    return anchor+math.fsum((b.close-anchor)*(b.volume/total) for b in bars)


def _cmf(bars):
    total = math.fsum(b.volume for b in bars)
    if total == 0:
        return None
    return math.fsum(((2*b.close-b.high-b.low)/(b.high-b.low) if b.high > b.low else 0)
                     * (b.volume/total) for b in bars)


def _mfi(bars):
    """n flows require n+1 bars; unchanged typical prices contribute neither side."""
    typical = [(b.high+b.low+b.close)/3 for b in bars]
    positive = math.fsum(typical[i]*bars[i].volume for i in range(1, len(bars))
                         if typical[i] > typical[i-1])
    negative = math.fsum(typical[i]*bars[i].volume for i in range(1, len(bars))
                         if typical[i] < typical[i-1])
    # Divide first: 100*p/(p+n) can round above 100 when n == 0 (regression 2026-11-24).
    return 100*(positive/(positive+negative)) if positive+negative > 0 else None


def volume_signals(history, config):
    """Entry, ordinary exit, simple ATR using available completed bars only."""
    if len(history) < config.history_required:
        raise ValueError("Insufficient volume strategy history")
    atr = _atr(history, config.atr_period)
    current = history[-1]
    price = current.close
    enter, exit_ = False, False
    if config.kind == "volume_breakout":
        average = fmean(b.volume for b in history[-config.volume_period-1:-1])
        enter = (average > 0 and current.volume/average >= config.relative_volume
                 and price > max(b.high for b in history[-config.breakout_period-1:-1]))
        exit_ = price < min(b.low for b in history[-config.exit_period-1:-1])
    elif config.kind == "vwma_trend":
        fast = _weighted_price(history[-config.fast_period:])
        slow = _weighted_price(history[-config.slow_period:])
        if fast is not None and slow is not None:
            enter, exit_ = fast > slow, fast < slow
    elif config.kind == "obv_confirmation":
        bars = history[-config.volume_period-1:]
        change = math.fsum((1 if b.close > a.close else -1 if b.close < a.close else 0)*b.volume
                           for a, b in zip(bars, bars[1:]))
        mean = fmean(b.close for b in history[-config.slow_period:])
        enter, exit_ = price > mean and change > 0, price < mean or change < 0
    elif config.kind == "cmf_trend":
        flow = _cmf(history[-config.volume_period:])
        mean = fmean(b.close for b in history[-config.slow_period:])
        enter = flow is not None and flow > config.cmf_entry and price > mean
        exit_ = price < mean or (flow is not None and flow <= 0)
    else:
        previous = _mfi(history[-config.mfi_period-2:-1])
        current_mfi = _mfi(history[-config.mfi_period-1:])
        enter = (previous is not None and current_mfi is not None
                 and previous <= config.mfi_entry < current_mfi < config.mfi_exit
                 and price > history[-2].close)
        exit_ = current_mfi is not None and current_mfi >= config.mfi_exit
    # Zero-volume observations cannot initiate exposure; existing positions still
    # receive price exits, ATR stops, holding limits and external regime vetoes.
    return bool(enter and current.volume > 0), bool(exit_), atr


class VolumeStrategy(_LongOnlyStrategy):
    """Long-only ETF/equity hypotheses using the target instrument's own volume."""

    def __init__(self, symbol, config: VolumeConfig, regime_filter=None):
        if not isinstance(config, VolumeConfig):
            raise TypeError("config must be VolumeConfig")
        super().__init__(symbol, config, regime_filter)

    def _signals(self, history):
        return volume_signals(history, self.config)
