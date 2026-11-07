"""Risk limits, enforced in code before any paper order reaches the exchange.

Nothing a strategy (or an LLM upstream) says can loosen these limits: the engine
runs every order through :meth:`RiskManager.check_order` and rejects it if any
check fails.

The kill switch trips automatically on a daily loss or a drawdown limit. Once
tripped the account is **reduce-only**: buys are rejected, sells of shares you
hold are still allowed so positions can be closed. Only a named human can reset
it (:meth:`RiskManager.reset`).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from .models import EPS, Order, OrderBook, Side

if TYPE_CHECKING:
    from .portfolio import Portfolio

# Names that are not people. A reset must come from a human, so these are refused.
NOT_HUMAN = {"", "auto", "auto_approve", "system", "strategy", "engine"}


@dataclass(frozen=True)
class RiskLimits:
    """Every limit is optional; None means "no limit"."""

    max_order_notional: float | None = None  # USDC per buy order (price x size)
    max_position_cost: float | None = None  # USDC cost basis per token after the order
    max_gross_exposure: float | None = None  # USDC: positions' cost + reserved cash + the order
    max_orders_per_day: int | None = None
    max_daily_loss: float | None = None  # USDC below the day's opening equity -> kill switch
    max_drawdown_pct: float | None = None  # % below peak equity -> kill switch
    day_seconds: float = 86_400.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class RiskDecision:
    ok: bool
    checks: list[Check] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(c.detail for c in self.checks if not c.passed)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": [asdict(c) for c in self.checks]}


def estimate_notional(order: Order, book: OrderBook | None) -> float:
    """USDC a buy could spend: limit price x size, or a walk of the asks for market orders.

    Size the book cannot cover is priced at $1 (the worst possible price), so the
    estimate never understates the risk.
    """
    if order.price is not None:
        return order.price * order.size
    left, total = order.size, 0.0
    for lv in book.asks if book else []:
        q = min(left, lv.size)
        total += q * lv.price
        left -= q
        if left <= EPS:
            break
    return total + max(left, 0.0) * 1.0


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.killed = False
        self.kill_reason = ""
        self.killed_at: float | None = None
        self.rejections = 0
        self._day: int | None = None
        self._day_open_equity = 0.0
        self._peak = -math.inf
        self._orders_today = 0
        self._orders_day: int | None = None

    # ------------------------------------------------------------------ order checks

    def check_order(
        self,
        order: Order,
        portfolio: "Portfolio",
        book: OrderBook | None,
        now: float,
        *,
        pending_exposure: float = 0.0,
        pending_orders: int = 0,
        count: bool = True,
    ) -> RiskDecision:
        """Run every limit for one order.

        ``pending_exposure`` / ``pending_orders`` let a basket be checked as a whole
        before any leg is sent. ``count=False`` checks without using up the daily
        order allowance (used for that up-front basket check).
        """
        lim = self.limits
        checks: list[Check] = []
        day = int(now // lim.day_seconds)
        if self._orders_day != day:
            self._orders_day, self._orders_today = day, 0

        buying = order.side is Side.BUY
        if self.killed and buying:
            checks.append(Check("kill_switch", False, f"kill switch on ({self.kill_reason}): reduce-only"))
        else:
            checks.append(Check("kill_switch", True, "off" if not self.killed else "on, but this order reduces risk"))

        n = self._orders_today + pending_orders + 1
        if lim.max_orders_per_day is not None:
            ok = n <= lim.max_orders_per_day
            checks.append(Check("orders_per_day", ok, f"order {n} of max {lim.max_orders_per_day} today"))

        if buying:
            notional = estimate_notional(order, book)
            if lim.max_order_notional is not None:
                ok = notional <= lim.max_order_notional + EPS
                checks.append(
                    Check("order_notional", ok, f"order {notional:.2f} vs max {lim.max_order_notional:.2f} USDC")
                )
            if lim.max_position_cost is not None:
                after = portfolio.position(order.token_id).cost_basis + notional
                ok = after <= lim.max_position_cost + EPS
                checks.append(
                    Check("position_cost", ok, f"position {after:.2f} vs max {lim.max_position_cost:.2f} USDC")
                )
            if lim.max_gross_exposure is not None:
                gross = gross_exposure(portfolio) + pending_exposure + notional
                ok = gross <= lim.max_gross_exposure + EPS
                checks.append(
                    Check("gross_exposure", ok, f"exposure {gross:.2f} vs max {lim.max_gross_exposure:.2f} USDC")
                )

        decision = RiskDecision(all(c.passed for c in checks), checks)
        if not decision.ok:
            self.rejections += 1
        elif count:
            self._orders_today += 1
        return decision

    # ------------------------------------------------------------------ kill switch

    def update_equity(self, now: float, equity: float) -> str | None:
        """Feed the latest equity. Returns the reason if this call tripped the kill switch."""
        lim = self.limits
        day = int(now // lim.day_seconds)
        if day != self._day:
            self._day, self._day_open_equity = day, equity
        self._peak = max(self._peak, equity)
        if self.killed:
            return None
        if lim.max_daily_loss is not None:
            loss = self._day_open_equity - equity
            if loss >= lim.max_daily_loss - EPS:
                return self.trip(f"daily loss {loss:.2f} >= limit {lim.max_daily_loss:.2f} USDC", now)
        if lim.max_drawdown_pct is not None and self._peak > 0:
            dd = (self._peak - equity) / self._peak * 100
            if dd >= lim.max_drawdown_pct - EPS:
                return self.trip(f"drawdown {dd:.2f}% >= limit {lim.max_drawdown_pct:.2f}%", now)
        return None

    def trip(self, reason: str, now: float) -> str:
        self.killed, self.kill_reason, self.killed_at = True, reason, now
        return reason

    def reset(self, by: str, equity: float) -> None:
        """Turn the kill switch off. Only a named human may do this."""
        if by.strip().lower() in NOT_HUMAN:
            raise PermissionError("the kill switch can only be reset by a named human")
        self.killed, self.kill_reason, self.killed_at = False, "", None
        # Limits restart from here, otherwise the old peak would trip it again at once.
        self._peak, self._day_open_equity = equity, equity

    def status(self) -> dict:
        return {
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "killed_at": self.killed_at,
            "rejections": self.rejections,
            "limits": self.limits.as_dict(),
        }


def gross_exposure(portfolio: "Portfolio") -> float:
    """Cost of open positions plus cash reserved by resting buy orders."""
    return sum(p.cost_basis for p in portfolio.positions.values()) + portfolio.locked_cash
