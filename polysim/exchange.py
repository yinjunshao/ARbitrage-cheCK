"""Simulated Polymarket CLOB.

The exchange keeps the latest L2 snapshot per token and matches the strategy's
orders against it:

* **Taker flow** – marketable orders walk the opposite side of the book level by
  level (with price cap, cash and FOK checks). Liquidity we consume is removed
  from the local copy of the book until the next snapshot replaces it, so the
  strategy cannot hit the same size twice.
* **Maker flow** – resting GTC orders are not in the recorded book, so they fill
  when a later snapshot shows the opposite side at or through our price
  (``FillMode.TOUCH``) or strictly through it (``FillMode.THROUGH``, more
  conservative). Maker fills execute at our limit price.
* **Latency** – with ``latency > 0`` an order only reaches the book ``latency``
  seconds after submission, and is matched against the book as it was then.

Resting orders reserve cash / shares in the :class:`Portfolio` so the strategy
cannot over-commit.
"""

from __future__ import annotations

import heapq
import itertools
from enum import Enum
from typing import Iterable

from .fees import FeeModel
from .models import (
    EPS,
    Fill,
    Liquidity,
    Market,
    Order,
    OrderBook,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
    is_on_tick,
)
from .portfolio import Portfolio


def market_fee_model(m: Market | None) -> FeeModel:
    if m is None:
        return FeeModel()
    if m.fee_rate is not None:
        maker = 0.0 if m.fee_taker_only else m.fee_rate
        return FeeModel(taker_rate=m.fee_rate, maker_rate=maker, exponent=m.fee_exponent)
    if m.category:
        return FeeModel.for_category(m.category)
    return FeeModel()  # e.g. an invented market with no fee information


class FillMode(str, Enum):
    TOUCH = "touch"
    THROUGH = "through"


DEFAULT_TICK = 0.01


class SimExchange:
    def __init__(
        self,
        portfolio: Portfolio,
        markets: Iterable[Market] = (),
        *,
        fee_model: FeeModel | None = None,
        latency: float = 0.0,
        fill_mode: FillMode = FillMode.TOUCH,
    ) -> None:
        self.portfolio = portfolio
        # An explicit fee model overrides every market's own rate (useful for what-ifs).
        self.fee_override = fee_model
        self._fee_cache: dict[str, FeeModel] = {}
        self.latency = float(latency)
        self.fill_mode = FillMode(fill_mode)
        self.market_by_token: dict[str, Market] = {}
        for m in markets:
            self.add_market(m)

        self.now = 0.0
        self.books: dict[str, OrderBook] = {}
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self._pending: list[tuple[float, int, Order]] = []
        self._seq = itertools.count()
        self._resting: dict[str, list[Order]] = {}
        self._locks: dict[str, float] = {}  # order_id -> reserved cash (BUY) or shares (SELL)
        self._unreported: list[Fill] = []

    # ------------------------------------------------------------------ setup / queries

    def add_market(self, market: Market) -> None:
        for tid in market.token_ids:
            self.market_by_token[tid] = market
            self._fee_cache.pop(tid, None)

    def fee_for(self, token_id: str) -> FeeModel:
        """Fee model for this token: override, else the market's own schedule, else its category."""
        if self.fee_override is not None:
            return self.fee_override
        fm = self._fee_cache.get(token_id)
        if fm is None:
            fm = self._fee_cache[token_id] = market_fee_model(self.market_by_token.get(token_id))
        return fm

    def book(self, token_id: str) -> OrderBook | None:
        return self.books.get(token_id)

    def open_orders(self, token_id: str | None = None) -> list[Order]:
        return [
            o
            for o in self.orders.values()
            if o.is_active and (token_id is None or o.token_id == token_id)
        ]

    def drain_fills(self) -> list[Fill]:
        """Fills generated since the last call (consumed by the engine)."""
        out, self._unreported = self._unreported, []
        return out

    # ------------------------------------------------------------------ order entry

    def submit(self, order: Order, now: float | None = None) -> Order:
        now = self.now if now is None else now
        order.created_ts = now
        self.orders[order.order_id] = order
        err = self._validate_static(order)
        if err:
            return self._reject(order, err)
        if self.latency > 0:
            heapq.heappush(self._pending, (now + self.latency, next(self._seq), order))
        else:
            self._activate(order)
        return order

    def cancel(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None or not order.is_active:
            return False
        if order.status is not OrderStatus.PENDING:
            self._release_lock(order)
            book_orders = self._resting.get(order.token_id, [])
            if order in book_orders:
                book_orders.remove(order)
        order.status = OrderStatus.CANCELLED  # pending ones are skipped when popped
        return True

    def cancel_all(self, token_ids: Iterable[str] | None = None) -> int:
        wanted = None if token_ids is None else set(token_ids)
        n = 0
        for o in list(self.orders.values()):
            if o.is_active and (wanted is None or o.token_id in wanted):
                n += self.cancel(o.order_id)
        return n

    # ------------------------------------------------------------------ market data

    def advance_time(self, now: float) -> None:
        """Deliver orders whose latency has elapsed by `now` (against the current book)."""
        while self._pending and self._pending[0][0] <= now + EPS:
            arrive_ts, _, order = heapq.heappop(self._pending)
            self.now = max(self.now, arrive_ts)
            if order.status is OrderStatus.PENDING:
                self._activate(order)
        self.now = max(self.now, now)

    def on_book(self, book: OrderBook) -> None:
        self.advance_time(book.ts)
        self.books[book.token_id] = book.copy()
        self._match_resting(book.token_id)

    # ------------------------------------------------------------------ internals

    def tick_size(self, token_id: str) -> float:
        m = self.market_by_token.get(token_id)
        return m.tick_size if m else DEFAULT_TICK

    def _min_size(self, token_id: str) -> float:
        m = self.market_by_token.get(token_id)
        return m.min_order_size if m else 0.0

    def _validate_static(self, order: Order) -> str:
        if self.market_by_token and order.token_id not in self.market_by_token:
            return f"unknown token {order.token_id}"
        m = self.market_by_token.get(order.token_id)
        if m is not None and m.closed:
            return "market closed"
        if order.size <= EPS:
            return "size must be positive"
        if order.size + EPS < self._min_size(order.token_id):
            return f"size below market minimum {self._min_size(order.token_id)}"
        if order.order_type is OrderType.LIMIT and order.price is None:
            return "limit order needs a price"
        if order.price is not None:
            if not (0.0 < order.price < 1.0):
                return "price must be in (0, 1)"
            if order.order_type is OrderType.LIMIT and not is_on_tick(
                order.price, self.tick_size(order.token_id)
            ):
                return f"price not on tick {self.tick_size(order.token_id)}"
        return ""

    def _reject(self, order: Order, reason: str) -> Order:
        order.status = OrderStatus.REJECTED
        order.reject_reason = reason
        return order

    def _activate(self, order: Order) -> None:
        pf = self.portfolio
        if order.side is Side.SELL and order.size > pf.available_shares(order.token_id) + EPS:
            self._reject(order, "insufficient shares")
            return
        if not order.is_immediate:
            # GTC limit buy: make sure the whole order could be reserved if it rests.
            need = order.price * order.size + self.fee_for(order.token_id).max_fee(order.price, order.size)
            if order.side is Side.BUY and need > pf.available_cash + EPS:
                self._reject(order, "insufficient cash")
                return

        order.status = OrderStatus.OPEN
        book = self.books.get(order.token_id)
        plan = self._plan_take(order, book) if book else []
        if order.tif is TimeInForce.FOK and sum(q for _, q in plan) < order.size - EPS:
            order.status = OrderStatus.CANCELLED
            order.reject_reason = "FOK not fully fillable"
            return
        self._execute_take(order, book, plan)

        if order.remaining <= EPS:
            order.status = OrderStatus.FILLED
        elif order.is_immediate:
            order.status = OrderStatus.CANCELLED
        else:
            self._rest(order)

    def _plan_take(self, order: Order, book: OrderBook) -> list[tuple[float, float]]:
        """Return the (price, qty) the order would take from `book`, without mutating it."""
        levels = book.asks if order.side is Side.BUY else book.bids
        cash = self.portfolio.available_cash
        fm = self.fee_for(order.token_id)
        left = order.remaining
        plan: list[tuple[float, float]] = []
        for lv in levels:
            if left <= EPS:
                break
            if order.price is not None:
                if order.side is Side.BUY and lv.price > order.price + EPS:
                    break
                if order.side is Side.SELL and lv.price < order.price - EPS:
                    break
            qty = min(left, lv.size)
            if order.side is Side.BUY:
                per_share = lv.price + fm.per_share(lv.price, Liquidity.TAKER)
                qty = min(qty, cash / per_share)
                cash -= qty * per_share
            if qty <= EPS:
                break
            plan.append((lv.price, qty))
            left -= qty
        return plan

    def _execute_take(self, order: Order, book: OrderBook | None, plan) -> None:
        if not plan or book is None:
            return
        levels = book.asks if order.side is Side.BUY else book.bids
        for (price, qty), lv in zip(plan, levels):
            lv.size -= qty
            self._fill(order, price, qty, Liquidity.TAKER)
        levels[:] = [lv for lv in levels if lv.size > EPS]

    def _rest(self, order: Order) -> None:
        if order.side is Side.BUY:
            amount = order.price * order.remaining + self.fee_for(order.token_id).max_fee(
                order.price, order.remaining
            )
            self.portfolio.lock_cash(amount)
        else:
            amount = order.remaining
            self.portfolio.lock_shares(order.token_id, amount)
        self._locks[order.order_id] = amount
        order.status = OrderStatus.PARTIALLY_FILLED if order.filled_size > EPS else OrderStatus.OPEN
        self._resting.setdefault(order.token_id, []).append(order)

    def _release_lock(self, order: Order, qty: float | None = None) -> None:
        """Release the reservation for `qty` shares of the order (all of it if None)."""
        locked = self._locks.get(order.order_id)
        if locked is None:
            return
        if qty is None:
            amount = locked
        elif order.side is Side.BUY:
            amount = order.price * qty + self.fee_for(order.token_id).max_fee(order.price, qty)
        else:
            amount = qty
        amount = min(amount, locked)
        if order.side is Side.BUY:
            self.portfolio.unlock_cash(amount)
        else:
            self.portfolio.unlock_shares(order.token_id, amount)
        if locked - amount <= EPS:
            self._locks.pop(order.order_id)
        else:
            self._locks[order.order_id] = locked - amount

    def _match_resting(self, token_id: str) -> None:
        resting = self._resting.get(token_id)
        book = self.books.get(token_id)
        if not resting or book is None:
            return
        through = self.fill_mode is FillMode.THROUGH
        buys = sorted((o for o in resting if o.side is Side.BUY), key=lambda o: -o.price)
        sells = sorted((o for o in resting if o.side is Side.SELL), key=lambda o: o.price)

        for order in buys:
            for lv in book.asks:
                crosses = lv.price < order.price - EPS or (not through and lv.price <= order.price + EPS)
                if not crosses or order.remaining <= EPS:
                    break
                qty = min(order.remaining, lv.size)
                lv.size -= qty
                self._fill(order, order.price, qty, Liquidity.MAKER)
            book.asks[:] = [lv for lv in book.asks if lv.size > EPS]

        for order in sells:
            for lv in book.bids:
                crosses = lv.price > order.price + EPS or (not through and lv.price >= order.price - EPS)
                if not crosses or order.remaining <= EPS:
                    break
                qty = min(order.remaining, lv.size)
                lv.size -= qty
                self._fill(order, order.price, qty, Liquidity.MAKER)
            book.bids[:] = [lv for lv in book.bids if lv.size > EPS]

        for order in [o for o in resting if o.remaining <= EPS]:
            order.status = OrderStatus.FILLED
            self._release_lock(order)
            resting.remove(order)

    def _fill(self, order: Order, price: float, qty: float, liquidity: Liquidity) -> None:
        if liquidity is Liquidity.MAKER:
            self._release_lock(order, qty)
        fill = Fill(
            order_id=order.order_id,
            token_id=order.token_id,
            side=order.side,
            price=price,
            size=qty,
            fee=self.fee_for(order.token_id).fee(price, qty, liquidity),
            ts=self.now,
            liquidity=liquidity,
        )
        self.portfolio.apply_fill(fill)
        order.filled_size += qty
        order.filled_notional += price * qty
        if order.status is OrderStatus.OPEN and liquidity is Liquidity.MAKER:
            order.status = OrderStatus.PARTIALLY_FILLED
        self.fills.append(fill)
        self._unreported.append(fill)
