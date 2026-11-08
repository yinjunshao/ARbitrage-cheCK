"""Core domain models: markets, order books, orders and fills.

Conventions
-----------
* Prices are probabilities in USDC per share, strictly between 0 and 1.
* Sizes are in shares. A winning share settles at 1 USDC, a losing one at 0.
* Timestamps are unix seconds (float).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

EPS = 1e-9


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"  # take liquidity only (optionally capped by `price`)
    LIMIT = "LIMIT"


class TimeInForce(str, Enum):
    GTC = "GTC"  # good till cancelled: rest whatever is not filled immediately
    FOK = "FOK"  # fill or kill: fill entirely right now or cancel
    FAK = "FAK"  # fill and kill (IOC): fill what you can now, cancel the rest


class OrderStatus(str, Enum):
    PENDING = "PENDING"  # submitted, waiting out simulated latency
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class Liquidity(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"


# --------------------------------------------------------------------------- markets


@dataclass(frozen=True, slots=True)
class Token:
    """One outcome token of a market (e.g. the YES or the NO side)."""

    token_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class Market:
    market_id: str  # Polymarket condition id
    question: str
    tokens: tuple[Token, ...]
    slug: str = ""
    tick_size: float = 0.01
    min_order_size: float = 5.0
    end_ts: float | None = None
    active: bool = True
    closed: bool = False
    category: str = ""  # e.g. "politics" (from Gamma's feeType "politics_fees")
    fee_rate: float | None = None  # taker feeRate from Gamma's feeSchedule; None = unknown
    fee_exponent: float = 1.0
    fee_taker_only: bool = True  # makers pay nothing
    event_slug: str = ""  # the Gamma event this market belongs to (e.g. a ladder)
    group_title: str = ""  # its rung in that event, e.g. "December 31" or "↑ 100,000"

    def token(self, outcome: str) -> Token:
        for t in self.tokens:
            if t.outcome.lower() == outcome.lower():
                return t
        raise KeyError(f"market {self.market_id} has no outcome {outcome!r}")

    @property
    def token_ids(self) -> tuple[str, ...]:
        return tuple(t.token_id for t in self.tokens)


# --------------------------------------------------------------------------- books


@dataclass(slots=True)
class PriceLevel:
    price: float
    size: float


@dataclass(slots=True)
class OrderBook:
    """L2 snapshot for a single outcome token. Bids sorted desc, asks asc."""

    token_id: str
    ts: float
    bids: list[PriceLevel] = field(default_factory=list)
    asks: list[PriceLevel] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.bids = sorted((lv for lv in self.bids if lv.size > EPS), key=lambda lv: -lv.price)
        self.asks = sorted((lv for lv in self.asks if lv.size > EPS), key=lambda lv: lv.price)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is not None and ba is not None:
            return (bb + ba) / 2
        return bb if bb is not None else ba

    @property
    def spread(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        return None if bb is None or ba is None else ba - bb

    def copy(self) -> "OrderBook":
        return OrderBook(
            self.token_id,
            self.ts,
            [PriceLevel(lv.price, lv.size) for lv in self.bids],
            [PriceLevel(lv.price, lv.size) for lv in self.asks],
        )

    @classmethod
    def from_mid(
        cls,
        token_id: str,
        ts: float,
        mid: float,
        *,
        spread: float = 0.02,
        levels: int = 5,
        size_per_level: float = 500.0,
        tick: float = 0.01,
    ) -> "OrderBook":
        """Build a symmetric synthetic book around `mid` (used when only prices are known)."""
        half = max(spread / 2, tick / 2)
        # Keep both sides on the valid price grid [tick, 1 - tick] with at least one tick of spread.
        best_bid = min(max(_floor_tick(mid - half + EPS, tick), tick), 1 - 2 * tick)
        best_ask = max(min(_ceil_tick(mid + half - EPS, tick), 1 - tick), best_bid + tick)
        best_bid, best_ask = round(best_bid, 10), round(best_ask, 10)
        bids, asks = [], []
        for i in range(levels):
            b, a = round(best_bid - i * tick, 10), round(best_ask + i * tick, 10)
            if b >= tick - EPS:
                bids.append(PriceLevel(b, size_per_level))
            if a <= 1 - tick + EPS:
                asks.append(PriceLevel(a, size_per_level))
        return cls(token_id, ts, bids, asks)


def _floor_tick(x: float, tick: float) -> float:
    return round((x // tick) * tick, 10)


def _ceil_tick(x: float, tick: float) -> float:
    return round(-((-x) // tick) * tick, 10)


def is_on_tick(price: float, tick: float) -> bool:
    return abs(price / tick - round(price / tick)) < 1e-6


# --------------------------------------------------------------------------- orders


_order_ids = itertools.count(1)


def _next_order_id() -> str:
    return f"o{next(_order_ids)}"


@dataclass(slots=True)
class Order:
    token_id: str
    side: Side
    size: float
    price: float | None = None  # limit price; for MARKET orders an optional worst price
    order_type: OrderType = OrderType.LIMIT
    tif: TimeInForce = TimeInForce.GTC
    tag: str = ""
    decision_id: str = ""  # links the order to the checker's / LLM's reasoning trace
    order_id: str = field(default_factory=_next_order_id)
    created_ts: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    filled_notional: float = 0.0
    reject_reason: str = ""

    @property
    def remaining(self) -> float:
        return max(self.size - self.filled_size, 0.0)

    @property
    def avg_fill_price(self) -> float | None:
        return self.filled_notional / self.filled_size if self.filled_size > EPS else None

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)

    @property
    def is_immediate(self) -> bool:
        """True if any unfilled remainder must be cancelled rather than rested."""
        return self.order_type is OrderType.MARKET or self.tif is not TimeInForce.GTC


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    token_id: str
    side: Side
    price: float
    size: float
    fee: float
    ts: float
    liquidity: Liquidity

    @property
    def notional(self) -> float:
        return self.price * self.size
