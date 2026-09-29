from __future__ import annotations

from dataclasses import dataclass

from qte import OrderSide, OrderStatus, Strategy, market_order


@dataclass(frozen=True)
class MovingAverageConfig:
    fast_period: int
    slow_period: int
    quantity: int

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in (self.fast_period, self.slow_period, self.quantity)):
            raise ValueError("periods and quantity must be integers")
        if self.fast_period <= 0:
            raise ValueError("fast_period must be positive")
        if self.slow_period <= self.fast_period:
            raise ValueError("slow_period must be greater than fast_period")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


class MovingAverageRegimeStrategy(Strategy):
    """Long-only SMA regime baseline with one outstanding strategy order at a time."""

    def __init__(self, symbol: str, config: MovingAverageConfig) -> None:
        super().__init__()
        if not symbol:
            raise ValueError("symbol must not be empty")
        self.symbol = symbol
        self.config = config
        self._pending_order_id: int | None = None

    def on_bar(self, context, bar) -> None:
        if bar.symbol != self.symbol or self._pending_order_id is not None:
            return
        history = context.history(self.symbol)
        if len(history) < self.config.slow_period:
            return
        closes = [item.close for item in history]
        fast = sum(closes[-self.config.fast_period :]) / self.config.fast_period
        slow = sum(closes[-self.config.slow_period :]) / self.config.slow_period
        position = context.position(self.symbol)
        quantity = 0 if position is None else position.quantity
        if fast > slow and quantity == 0:
            receipt = context.submit_order(
                market_order(self.symbol, OrderSide.BUY, self.config.quantity)
            )
            self._pending_order_id = receipt.order_id
        elif fast < slow and quantity > 0:
            receipt = context.submit_order(
                market_order(self.symbol, OrderSide.SELL, quantity)
            )
            self._pending_order_id = receipt.order_id

    def on_fill(self, context, fill) -> None:
        if fill.order_id == self._pending_order_id:
            self._pending_order_id = None

    def on_order_update(self, context, update) -> None:
        if update.order_id == self._pending_order_id and update.status in (
            OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.FILLED
        ):
            self._pending_order_id = None
