"""Causal, long-only research candidates; none is a claim of demonstrated alpha."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Sequence

from qte import OrderSide, OrderStatus, Strategy, market_order


FAMILIES = ("trend", "breakout", "contraction", "pullback", "mean_reversion", "rebound")


@dataclass(frozen=True)
class CandidateConfig:
    kind: str = "trend"
    allocation_fraction: float = 0.10
    fast_period: int = 10
    slow_period: int = 50
    breakout_period: int = 20
    exit_period: int = 10
    atr_period: int = 14
    contraction_window: int = 50
    contraction_quantile: float = 0.20
    contraction_memory: int = 5
    entry_z: float = 2.0
    min_atr_fraction: float = 0.02
    max_atr_fraction: float = 0.03
    shock_fraction: float = 0.03
    stop_atr_multiple: float = 2.5
    max_holding_bars: int = 20

    def __post_init__(self) -> None:
        if self.kind not in FAMILIES:
            raise ValueError(f"kind must be one of {FAMILIES}")
        for name in (
            "fast_period", "slow_period", "breakout_period", "exit_period",
            "atr_period", "contraction_window", "contraction_memory", "max_holding_bars",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        for name in (
            "allocation_fraction", "contraction_quantile", "entry_z",
            "min_atr_fraction", "max_atr_fraction", "shock_fraction", "stop_atr_multiple",
        ):
            value = getattr(self, name)
            try:
                finite = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.allocation_fraction > 1 or self.contraction_quantile > 1:
            raise ValueError("allocation_fraction and contraction_quantile cannot exceed one")
        if self.shock_fraction >= 1:
            raise ValueError("shock_fraction must be less than one")
        if self.min_atr_fraction > self.max_atr_fraction:
            raise ValueError("min_atr_fraction cannot exceed max_atr_fraction")
        if self.slow_period < 2:
            raise ValueError("slow_period must be at least two")

    @property
    def history_required(self) -> int:
        signal_requirement = {
            "trend": self.slow_period,
            "breakout": max(self.breakout_period, self.exit_period) + 1,
            "contraction": max(
                self.breakout_period + 1, self.exit_period + 1,
                self.atr_period + self.contraction_window + self.contraction_memory + 1,
            ),
            "pullback": self.slow_period + 1,
            "mean_reversion": self.slow_period + 1,
            "rebound": max(self.fast_period, 3),
        }[self.kind]
        return max(self.atr_period + 1, signal_requirement)


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _atr(history: Sequence, period: int, end: int | None = None) -> float:
    """Simple (not Wilder-smoothed) mean true range ending at exclusive `end`."""
    end = len(history) if end is None else end
    return _mean([
        max(history[i].high - history[i].low,
            abs(history[i].high - history[i - 1].close),
            abs(history[i].low - history[i - 1].close))
        for i in range(end - period, end)
    ])


def _signals(history: Sequence, config: CandidateConfig) -> tuple[bool, bool, float]:
    """Return entry, ordinary exit, ATR from completed bars only."""
    closes = [bar.close for bar in history]
    price = closes[-1]
    atr = _atr(history, config.atr_period)
    kind = config.kind
    if kind == "trend":
        fast, slow = _mean(closes[-config.fast_period:]), _mean(closes[-config.slow_period:])
        return fast > slow, fast < slow, atr
    if kind in ("breakout", "contraction"):
        entry = price > max(bar.high for bar in history[-config.breakout_period - 1:-1])
        exit_ = price < min(bar.low for bar in history[-config.exit_period - 1:-1])
        # Percentile history affects entry only. Avoid repeatedly recomputing
        # historical ATRs when the prerequisite breakout already rules it out.
        if kind == "contraction" and entry:
            contracted = False
            # Candidate contraction bars precede the current breakout bar. Each
            # threshold uses strictly earlier ATR observations, never a future rank.
            for end in range(len(history) - config.contraction_memory, len(history)):
                candidate = _atr(history, config.atr_period, end)
                prior = sorted(_atr(history, config.atr_period, previous_end)
                               for previous_end in range(end - config.contraction_window, end))
                threshold = prior[math.ceil(config.contraction_quantile * len(prior)) - 1]
                if candidate > 0 and candidate <= threshold:
                    contracted = True
                    break
            entry = entry and contracted
        return entry, exit_, atr
    if kind == "pullback":
        fast = _mean(closes[-config.fast_period:])
        slow = _mean(closes[-config.slow_period:])
        previous_fast = _mean(closes[-config.fast_period - 1:-1])
        return (fast > slow and closes[-2] < previous_fast and price > fast,
                fast < slow or price < slow, atr)
    if kind == "mean_reversion":
        baseline = closes[-config.slow_period - 1:-1]
        mean = _mean(baseline)
        deviation = math.sqrt(_mean([(value - mean) ** 2 for value in baseline]))
        entry = (deviation > 0 and price <= mean - config.entry_z * deviation
                 and atr / price <= config.max_atr_fraction)
        return entry, price >= mean, atr
    # A price/volatility rebound hypothesis, NOT evidence of dealer buying.
    shock = closes[-2] / closes[-3] - 1 <= -config.shock_fraction
    return (shock and price > closes[-2] and atr / price >= config.min_atr_fraction,
            price >= _mean(closes[-config.fast_period:]), atr)


class CandidateStrategy(Strategy):
    """One symbol, one pending order, next-open fills; reset on every run."""

    def __init__(self, symbol: str, config: CandidateConfig,
                 regime_filter: Callable[[int, str], bool] | None = None) -> None:
        super().__init__()
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be nonempty")
        if not isinstance(config, CandidateConfig):
            raise TypeError("config must be CandidateConfig")
        if regime_filter is not None and not callable(regime_filter):
            raise TypeError("regime_filter must be callable")
        self.symbol = symbol
        self.config = config
        self.regime_filter = regime_filter
        self._reset()

    def _reset(self) -> None:
        self._pending_order_id: int | None = None
        self._last_submitted_order_id: int | None = None
        self._pending_entry_atr: float | None = None
        self._entry_price: float | None = None
        self._entry_atr: float | None = None
        self._holding_bars = 0
        self._bars_seen = 0

    def on_start(self, context) -> None:
        self._reset()

    def on_bar(self, context, bar) -> None:
        if bar.symbol != self.symbol:
            return
        self._bars_seen += 1
        position = context.position(self.symbol)
        quantity = 0 if position is None else position.quantity
        if quantity < 0:
            raise ValueError("CandidateStrategy is long-only")
        if quantity > 0:
            self._holding_bars += 1
        if self._pending_order_id is not None:
            return
        allowed = True if self.regime_filter is None else self.regime_filter(bar.end_ns, self.config.kind)
        if type(allowed) is not bool:
            raise TypeError("regime_filter must return bool")
        history = context.history(self.symbol)
        if len(history) < self.config.history_required:
            if self._bars_seen >= self.config.history_required:
                raise ValueError("engine history_capacity is smaller than config.history_required")
            return
        enter, exit_, atr = _signals(history, self.config)
        if quantity > 0:
            stopped = (self._entry_price is not None and self._entry_atr is not None
                       and bar.close <= self._entry_price - self.config.stop_atr_multiple * self._entry_atr)
            if not allowed or exit_ or stopped or self._holding_bars >= self.config.max_holding_bars:
                self._submit(context, OrderSide.SELL, quantity)
        elif allowed and enter:
            equity = context.portfolio.equity
            if equity is None or not math.isfinite(equity) or equity <= 0:
                return
            quantity = math.floor(equity * self.config.allocation_fraction / bar.close)
            if quantity > 0:
                self._pending_entry_atr = atr
                self._submit(context, OrderSide.BUY, quantity)

    def _submit(self, context, side, quantity: int) -> None:
        receipt = context.submit_order(market_order(self.symbol, side, quantity))
        self._pending_order_id = receipt.order_id
        self._last_submitted_order_id = receipt.order_id

    def on_order_update(self, context, update) -> None:
        if update.order_id != self._pending_order_id:
            return
        if update.status in (OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.FILLED):
            self._pending_order_id = None
        if update.status in (OrderStatus.REJECTED, OrderStatus.CANCELED):
            self._pending_entry_atr = None

    def on_fill(self, context, fill) -> None:
        if fill.order_id != self._last_submitted_order_id:
            return
        self._pending_order_id = None
        if fill.side == OrderSide.BUY:
            self._entry_price = fill.executed_price
            self._entry_atr = self._pending_entry_atr
            self._holding_bars = 0
        else:
            self._entry_price = self._entry_atr = None
            self._holding_bars = 0
        self._pending_entry_atr = None
