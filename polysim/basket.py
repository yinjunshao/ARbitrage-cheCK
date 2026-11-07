"""Multi-leg baskets: the paper version of taking an arbitrage.

A basket is approved once, as a whole. Its legs are then sent one after the
other as fill-or-kill buys, each one subject to the exchange latency, so the
book can move between legs exactly as it would for a real trader.

If a leg cannot be filled in full, the legs already bought are sold straight
back (market sells, fill-and-kill). That unwind is part of the plan the person
approved, so it needs no second approval. What the round trip lost is the
basket's **leg-risk cost**: the money an arbitrage can lose even when the
arbitrage itself was real.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .models import EPS, Fill, Order, OrderStatus

_ids = itertools.count(1)


@dataclass(frozen=True)
class BasketLeg:
    token_id: str
    size: float
    max_price: float | None = None  # worst price per share we accept; None = take what is there


@dataclass
class Basket:
    legs: tuple[BasketLeg, ...]
    decision_id: str = ""
    tag: str = ""
    basket_id: str = field(default_factory=lambda: f"b{next(_ids)}")
    # pending -> executing -> filled | unwinding -> unwound ; or rejected
    status: str = "pending"
    reason: str = ""
    approved_by: str = ""
    orders: list[Order] = field(default_factory=list)
    unwind_orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.status in ("filled", "unwound", "rejected")

    def _spent(self, orders: list[Order]) -> float:
        ids = {o.order_id for o in orders}
        return sum(f.notional + f.fee for f in self.fills if f.order_id in ids)

    @property
    def cost(self) -> float:
        """USDC paid for the legs that filled, fees included."""
        return self._spent(self.orders)

    @property
    def unwind_proceeds(self) -> float:
        """USDC received from selling legs back, after fees."""
        ids = {o.order_id for o in self.unwind_orders}
        return sum(f.notional - f.fee for f in self.fills if f.order_id in ids)

    @property
    def leg_risk_cost(self) -> float:
        """What the failed attempt lost: legs bought minus what selling them back returned."""
        return self.cost - self.unwind_proceeds if self.status == "unwound" else 0.0

    @property
    def residual_shares(self) -> dict[str, float]:
        """Shares the unwind could not sell (thin bids). These stay in the account."""
        out: dict[str, float] = {}
        for leg_order in self.orders:
            sold = sum(o.filled_size for o in self.unwind_orders if o.token_id == leg_order.token_id)
            left = leg_order.filled_size - sold
            if self.status in ("unwinding", "unwound") and left > EPS:
                out[leg_order.token_id] = left
        return out

    def summary(self) -> dict:
        return {
            "basket_id": self.basket_id,
            "decision_id": self.decision_id,
            "status": self.status,
            "reason": self.reason,
            "approved_by": self.approved_by,
            "legs": [
                {
                    "token_id": leg.token_id,
                    "size": leg.size,
                    "max_price": leg.max_price,
                    "order_id": o.order_id if o else None,
                    "status": o.status.value if o else "not sent",
                    "avg_price": o.avg_fill_price if o else None,
                }
                for leg, o in itertools.zip_longest(self.legs, self.orders)
            ],
            "cost": self.cost,
            "unwind_proceeds": self.unwind_proceeds,
            "leg_risk_cost": self.leg_risk_cost,
            "residual_shares": self.residual_shares,
        }


def leg_failed(order: Order) -> bool:
    return not order.is_active and order.status is not OrderStatus.FILLED
