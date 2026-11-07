"""Small reference strategies showing how to use the API. Not trading advice."""

from __future__ import annotations

from collections import deque

from ..models import OrderBook, TimeInForce
from ..strategy import Strategy, StrategyContext


class BuyAndHold(Strategy):
    """Market-buy `size` shares of one outcome on the first book, hold to resolution."""

    name = "buy_and_hold"

    def __init__(self, outcome: str = "Yes", size: float = 100.0) -> None:
        self.outcome = outcome
        self.size = size
        self._done = False

    def on_book(self, ctx: StrategyContext, book: OrderBook) -> None:
        if self._done:
            return
        for m in ctx.markets.values():
            if m.token(self.outcome).token_id == book.token_id:
                ctx.buy(book.token_id, self.size)
                self._done = True


class MeanReversion(Strategy):
    """Buy an outcome when its mid drops `threshold` below its rolling mean, sell the
    position back when the mid reverts to (or above) the mean."""

    name = "mean_reversion"

    def __init__(self, window: int = 30, threshold: float = 0.03, size: float = 50.0) -> None:
        self.window = window
        self.threshold = threshold
        self.size = size
        self._mids: dict[str, deque[float]] = {}

    def on_book(self, ctx: StrategyContext, book: OrderBook) -> None:
        mid = book.mid
        if mid is None:
            return
        hist = self._mids.setdefault(book.token_id, deque(maxlen=self.window))
        hist.append(mid)
        if len(hist) < self.window:
            return
        mean = sum(hist) / len(hist)
        held = ctx.position(book.token_id).qty
        if held == 0 and mid < mean - self.threshold and book.best_ask is not None:
            ctx.buy(book.token_id, self.size, price=book.best_ask, tif=TimeInForce.FAK)
        elif held > 0 and mid >= mean and book.best_bid is not None:
            ctx.sell(book.token_id, held, price=book.best_bid, tif=TimeInForce.FAK)


class SimpleMarketMaker(Strategy):
    """Quote one tick inside/at the touch on both sides of each token, re-quoting every
    `requote_every` books and skewing away from inventory above `max_inventory`."""

    name = "market_maker"

    def __init__(self, size: float = 20.0, max_inventory: float = 200.0, requote_every: int = 5) -> None:
        self.size = size
        self.max_inventory = max_inventory
        self.requote_every = requote_every
        self._count: dict[str, int] = {}

    def on_book(self, ctx: StrategyContext, book: OrderBook) -> None:
        n = self._count[book.token_id] = self._count.get(book.token_id, 0) + 1
        if n % self.requote_every:
            return
        if book.best_bid is None or book.best_ask is None:
            return
        ctx.cancel_all(book.token_id)
        inv = ctx.position(book.token_id).qty
        if inv < self.max_inventory and ctx.cash > book.best_bid * self.size:
            ctx.buy(book.token_id, self.size, price=book.best_bid)
        sell_qty = min(self.size, inv)
        if sell_qty > 0:
            ctx.sell(book.token_id, sell_qty, price=book.best_ask)
