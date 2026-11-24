"""S5 point-in-time signal. A partial session can be inspected without committing it."""
from collections import deque
from dataclasses import dataclass
from math import fsum, isfinite


@dataclass(frozen=True)
class S5Config:
    rsi_entry: float = 10
    ibs_entry: float = 0.15
    rsi_exit: float = 65

    def __post_init__(self):
        if any(type(x) not in (int, float) or not isfinite(x) for x in
               (self.rsi_entry, self.ibs_entry, self.rsi_exit)):
            raise ValueError("S5 thresholds must be finite numbers")
        if not 0 <= self.rsi_entry < self.rsi_exit <= 100 or not 0 <= self.ibs_entry <= 1:
            raise ValueError("Invalid S5 RSI or IBS thresholds")


class S5Signal:
    def __init__(self, config=S5Config()):
        self.config = config
        self.closes = deque(maxlen=199)
        self.prior_high = None
        self.deltas = 0
        self.gain = self.loss = 0.0

    def _averages(self, close):
        change = close - self.closes[-1]
        gain, loss = max(change, 0), max(-change, 0)
        if self.deltas == 0:
            return gain, loss
        return (self.gain + gain) / 2, (self.loss + loss) / 2

    def finish_day(self, high, close):
        if not 0 < close <= high or not isfinite(high):
            raise ValueError("Invalid completed daily candle")
        if self.closes:
            self.gain, self.loss = self._averages(close)
            self.deltas += 1
        self.closes.append(close)
        self.prior_high = high

    def snapshot(self, high, low, last):
        if not 0 < low <= last <= high or not isfinite(high):
            raise ValueError("Invalid partial daily candle")
        if len(self.closes) < self.closes.maxlen:
            return None
        gain, loss = self._averages(last)
        rsi = 100 * gain / (gain + loss) if gain + loss else 50.0
        ibs = (last - low) / (high - low) if high > low else None
        sma = (fsum(self.closes) + last) / 200
        uptrend = last > sma
        oversold = rsi < self.config.rsi_entry or (ibs is not None and ibs < self.config.ibs_entry)
        return dict(price=last, partial_high=high, partial_low=low, sma200=sma, rsi2=rsi, ibs=ibs,
                    prior_high=self.prior_high, uptrend=uptrend, signal=uptrend and oversold,
                    exit_signal=rsi > self.config.rsi_exit or last > self.prior_high)
