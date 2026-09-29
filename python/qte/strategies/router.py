"""One-book macro routing with explicit exit-before-switch execution semantics."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields

from qte import OrderStatus, Strategy
from qte.regimes import MacroRegimeFilter, RegimeDecision
from qte.strategies.candidates import CandidateConfig, CandidateStrategy, FAMILIES


@dataclass(frozen=True)
class RouterConfig:
    risk_on_low: tuple[str, ...] = ("contraction", "breakout", "trend")
    risk_on_normal: tuple[str, ...] = ("pullback", "trend")
    risk_on_high: tuple[str, ...] = ("trend", "rebound")
    neutral_low: tuple[str, ...] = ("mean_reversion", "pullback")
    neutral_normal: tuple[str, ...] = ("mean_reversion", "pullback")

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not isinstance(value, (list, tuple)):
                raise ValueError("router priorities must be lists/tuples of family names")
            value = tuple(value)
            if any(not isinstance(kind, str) or kind not in FAMILIES for kind in value):
                raise ValueError("unknown family in router priorities")
            if len(set(value)) != len(value):
                raise ValueError("router priorities must not repeat a family")
            object.__setattr__(self, item.name, value)


@dataclass(frozen=True)
class RouteEvent:
    timestamp_ns: int
    target_family: str | None
    active_family: str | None
    phase: str
    macro_regime: str
    volatility_regime: str
    dealer_regime: str
    reasons: tuple[str, ...]


class _ChildContext:
    """Forward public context access while recording submission receipts centrally.

    This wrapper owns no portfolio or execution state and never reads child
    private attributes. The engine still authorizes commands and assigns IDs.
    """

    def __init__(self, context, owner: MacroRouterStrategy):
        self._context, self._owner = context, owner

    @property
    def portfolio(self):
        return self._context.portfolio

    def position(self, symbol):
        return self._context.position(symbol)

    def history(self, symbol):
        return self._context.history(symbol)

    def submit_order(self, request):
        if self._owner._pending_order_id is not None:
            raise RuntimeError("router cannot submit while another order is pending")
        receipt = self._context.submit_order(request)
        self._owner._pending_order_id = receipt.order_id
        self._owner._last_submitted_order_id = receipt.order_id
        return receipt

    def cancel_order(self, order_id):
        return self._context.cancel_order(order_id)


class MacroRouterStrategy(Strategy):
    """Select one permitted family by declared priority, never by future outcome.

    Switching an invested child latches retirement until flat. Lifecycle callbacks
    never activate another child: activation and signals happen on a later bar.
    """

    def __init__(self, symbol: str, configs: Sequence[CandidateConfig],
                 regime_filter: MacroRegimeFilter,
                 config: RouterConfig = RouterConfig()) -> None:
        super().__init__()
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be nonempty")
        if not isinstance(configs, Sequence) or isinstance(configs, (str, bytes)):
            raise TypeError("configs must be a sequence of CandidateConfig values")
        values = tuple(configs)
        if not values or any(not isinstance(value, CandidateConfig) for value in values):
            raise ValueError("at least one CandidateConfig is required")
        if len({value.kind for value in values}) != len(values):
            raise ValueError("router requires at most one config per family")
        if not isinstance(regime_filter, MacroRegimeFilter):
            raise TypeError("regime_filter must be MacroRegimeFilter")
        if not isinstance(config, RouterConfig):
            raise TypeError("config must be RouterConfig")
        self.symbol, self.configs = symbol, values
        self.regime_filter, self.config = regime_filter, config
        self._by_family = {value.kind: value for value in values}
        self._reset()

    @property
    def history_required(self) -> int:
        return max(value.history_required for value in self.configs)

    @property
    def active_family(self) -> str | None:
        return self._active_family

    @property
    def pending_order_id(self) -> int | None:
        return self._pending_order_id

    @property
    def route_events(self) -> tuple[RouteEvent, ...]:
        return tuple(self._route_events)

    def _reset(self) -> None:
        self._child: CandidateStrategy | None = None
        self._active_family: str | None = None
        self._pending_order_id: int | None = None
        self._last_submitted_order_id: int | None = None
        self._retiring = False
        self._entry_allowed = False
        self._bars_seen = 0
        self._route_events: list[RouteEvent] = []
        self._last_route_state = None

    def on_start(self, context) -> None:
        self._reset()

    def _target(self, decision: RegimeDecision) -> str | None:
        priorities = getattr(self.config, f"{decision.macro_regime}_{decision.volatility_regime}", ())
        return next((kind for kind in priorities
                     if kind in self._by_family and kind in decision.permitted_families), None)

    def _gate(self, timestamp_ns: int, family: str) -> bool:
        # Set only from the current bar's causal macro decision. Retirement is
        # latched even if a newer regime changes back before the exit completes.
        return self._entry_allowed and family == self._active_family

    def _activate(self, family: str | None, context) -> None:
        self._child = None
        self._active_family = family
        self._retiring = False
        self._last_submitted_order_id = None
        if family is not None:
            self._child = CandidateStrategy(self.symbol, self._by_family[family], self._gate)
            self._child.on_start(_ChildContext(context, self))

    def _record(self, bar, target: str | None, decision: RegimeDecision) -> None:
        phase = "retiring" if self._retiring else "active" if self._child is not None else "cash"
        state = (target, self._active_family, phase, decision.macro_regime,
                 decision.volatility_regime, decision.dealer_regime, decision.reasons)
        if state != self._last_route_state:
            self._route_events.append(RouteEvent(bar.end_ns, *state))
            self._last_route_state = state

    def on_bar(self, context, bar) -> None:
        if bar.symbol != self.symbol:
            return
        self._bars_seen += 1
        history = context.history(self.symbol)
        if self._bars_seen >= self.history_required and len(history) < self.history_required:
            raise ValueError("engine history_capacity is smaller than router.history_required")
        position = context.position(self.symbol)
        quantity = 0 if position is None else position.quantity
        if quantity < 0:
            raise ValueError("MacroRouterStrategy is long-only")
        decision = self.regime_filter.explain(bar.end_ns)
        target = self._target(decision)
        if self._child is None:
            if quantity != 0 or self._pending_order_id is not None:
                raise RuntimeError("router cannot adopt a position or order it did not create")
            self._activate(target, context)
        elif self._retiring or target != self._active_family:
            if quantity == 0 and self._pending_order_id is None:
                self._activate(target, context)
            else:
                self._retiring = True
        self._entry_allowed = not self._retiring and target == self._active_family
        self._record(bar, target, decision)
        if self._child is not None:
            self._child.on_bar(_ChildContext(context, self), bar)

    def on_order_update(self, context, update) -> None:
        if self._child is None or update.order_id != self._pending_order_id:
            return
        self._child.on_order_update(_ChildContext(context, self), update)
        if update.status in (OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.FILLED):
            self._pending_order_id = None

    def on_fill(self, context, fill) -> None:
        if self._child is None or fill.order_id != self._last_submitted_order_id:
            return
        self._child.on_fill(_ChildContext(context, self), fill)
        self._pending_order_id = None

    def on_end(self, context) -> None:
        if self._child is not None:
            self._child.on_end(context)
