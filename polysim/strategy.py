"""Strategy interface and the context object strategies use to trade."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Fill, Market, Order, OrderBook, OrderType, Side, TimeInForce

if TYPE_CHECKING:
    from .basket import Basket, BasketLeg
    from .engine import Backtester
    from .events import ResolutionEvent
    from .portfolio import Position


class StrategyContext:
    """The strategy's only handle on the simulation (orders, books, account)."""

    def __init__(self, engine: "Backtester") -> None:
        self._engine = engine

    # ------------------------------------------------------------------ state

    @property
    def now(self) -> float:
        return self._engine.exchange.now

    @property
    def markets(self) -> dict[str, Market]:
        return self._engine.markets

    @property
    def cash(self) -> float:
        return self._engine.portfolio.available_cash

    @property
    def equity(self) -> float:
        return self._engine.equity()

    def book(self, token_id: str) -> OrderBook | None:
        return self._engine.exchange.book(token_id)

    def position(self, token_id: str) -> "Position":
        return self._engine.portfolio.position(token_id)

    def open_orders(self, token_id: str | None = None) -> list[Order]:
        return self._engine.exchange.open_orders(token_id)

    @property
    def kill_switch_on(self) -> bool:
        return self._engine.risk.killed

    # ------------------------------------------------------------------ actions

    def submit(self, order: Order) -> Order:
        """Send an order through the engine's gate: risk limits -> approval -> exchange."""
        return self._engine.submit_order(order)

    def execute_basket(self, legs: "list[BasketLeg]", decision_id: str = "", tag: str = "") -> "Basket":
        """Buy several legs as one approved plan, unwinding if a leg fails."""
        return self._engine.execute_basket(legs, decision_id=decision_id, tag=tag)

    def buy(
        self,
        token_id: str,
        size: float,
        price: float | None = None,
        tif: TimeInForce = TimeInForce.GTC,
        tag: str = "",
    ) -> Order:
        """Limit buy if `price` is given, otherwise a market (FAK) buy."""
        return self.submit(_make(token_id, Side.BUY, size, price, tif, tag))

    def sell(
        self,
        token_id: str,
        size: float,
        price: float | None = None,
        tif: TimeInForce = TimeInForce.GTC,
        tag: str = "",
    ) -> Order:
        return self.submit(_make(token_id, Side.SELL, size, price, tif, tag))

    def cancel(self, order_id: str) -> bool:
        return self._engine.exchange.cancel(order_id)

    def cancel_all(self, token_id: str | None = None) -> int:
        return self._engine.exchange.cancel_all(None if token_id is None else [token_id])


def _make(token_id, side, size, price, tif, tag) -> Order:
    if price is None:
        return Order(token_id, side, size, None, OrderType.MARKET, TimeInForce.FAK, tag)
    return Order(token_id, side, size, price, OrderType.LIMIT, tif, tag)


class Strategy:
    """Override any of the hooks. All are no-ops by default."""

    name: str = "strategy"

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_book(self, ctx: StrategyContext, book: OrderBook) -> None: ...

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None: ...

    def on_resolution(self, ctx: StrategyContext, event: "ResolutionEvent") -> None: ...

    def on_basket(self, ctx: StrategyContext, basket: "Basket") -> None:
        """A basket finished: filled, unwound or rejected."""

    def on_kill_switch(self, ctx: StrategyContext, reason: str) -> None:
        """The kill switch just tripped; the account is now reduce-only."""

    def on_end(self, ctx: StrategyContext) -> None: ...
