"""Account state: cash, outcome-token positions, reservations and PnL."""

from __future__ import annotations

from dataclasses import dataclass

from .models import EPS, Fill, Side


class InsufficientFunds(Exception):
    pass


@dataclass(slots=True)
class Position:
    token_id: str
    qty: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def cost_basis(self) -> float:
        return self.qty * self.avg_price


class Portfolio:
    """Long-only outcome-token account (Polymarket has no shorting: buy the other side).

    `locked_cash` / `locked_shares` are reserved by resting orders so the strategy
    cannot over-commit. The exchange is responsible for locking and unlocking.
    """

    def __init__(self, initial_cash: float = 1_000.0) -> None:
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.locked_cash = 0.0
        self.positions: dict[str, Position] = {}
        self.locked_shares: dict[str, float] = {}
        self.fees_paid = 0.0
        self.settlement_pnl = 0.0

    # ------------------------------------------------------------------ queries

    @property
    def available_cash(self) -> float:
        return self.cash - self.locked_cash

    def position(self, token_id: str) -> Position:
        pos = self.positions.get(token_id)
        if pos is None:
            pos = self.positions[token_id] = Position(token_id)
        return pos

    def qty(self, token_id: str) -> float:
        pos = self.positions.get(token_id)
        return pos.qty if pos else 0.0

    def available_shares(self, token_id: str) -> float:
        return self.qty(token_id) - self.locked_shares.get(token_id, 0.0)

    def market_value(self, marks: dict[str, float]) -> float:
        total = 0.0
        for tid, pos in self.positions.items():
            if pos.qty > EPS:
                total += pos.qty * marks.get(tid, pos.avg_price)
        return total

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + self.market_value(marks)

    def unrealized_pnl(self, marks: dict[str, float]) -> float:
        return sum(
            pos.qty * (marks.get(tid, pos.avg_price) - pos.avg_price)
            for tid, pos in self.positions.items()
            if pos.qty > EPS
        )

    @property
    def realized_pnl(self) -> float:
        """Trading + settlement PnL, net of fees."""
        return sum(p.realized_pnl for p in self.positions.values()) - self.fees_paid

    # ------------------------------------------------------------------ reservations

    def lock_cash(self, amount: float) -> None:
        if amount > self.available_cash + EPS:
            raise InsufficientFunds(f"need {amount:.4f}, available {self.available_cash:.4f}")
        self.locked_cash += amount

    def unlock_cash(self, amount: float) -> None:
        self.locked_cash = max(self.locked_cash - amount, 0.0)

    def lock_shares(self, token_id: str, qty: float) -> None:
        if qty > self.available_shares(token_id) + EPS:
            raise InsufficientFunds(
                f"need {qty:.4f} shares of {token_id}, available {self.available_shares(token_id):.4f}"
            )
        self.locked_shares[token_id] = self.locked_shares.get(token_id, 0.0) + qty

    def unlock_shares(self, token_id: str, qty: float) -> None:
        left = self.locked_shares.get(token_id, 0.0) - qty
        if left > EPS:
            self.locked_shares[token_id] = left
        else:
            self.locked_shares.pop(token_id, None)

    # ------------------------------------------------------------------ mutations

    def apply_fill(self, fill: Fill) -> None:
        pos = self.position(fill.token_id)
        self.fees_paid += fill.fee
        if fill.side is Side.BUY:
            new_qty = pos.qty + fill.size
            pos.avg_price = (pos.cost_basis + fill.notional) / new_qty
            pos.qty = new_qty
            self.cash -= fill.notional + fill.fee
        else:
            if fill.size > pos.qty + EPS:
                raise InsufficientFunds(f"selling {fill.size} > held {pos.qty} of {fill.token_id}")
            pos.realized_pnl += (fill.price - pos.avg_price) * fill.size
            pos.qty -= fill.size
            if pos.qty <= EPS:
                pos.qty, pos.avg_price = 0.0, 0.0
            self.cash += fill.notional - fill.fee

    def settle(self, token_id: str, payout_per_share: float) -> float:
        """Redeem a resolved token. Returns the cash credited."""
        pos = self.positions.get(token_id)
        if pos is None or pos.qty <= EPS:
            return 0.0
        proceeds = pos.qty * payout_per_share
        pnl = (payout_per_share - pos.avg_price) * pos.qty
        pos.realized_pnl += pnl
        self.settlement_pnl += pnl
        self.cash += proceeds
        self.locked_shares.pop(token_id, None)
        pos.qty, pos.avg_price = 0.0, 0.0
        return proceeds

    def snapshot(self, marks: dict[str, float]) -> dict:
        return {
            "cash": self.cash,
            "locked_cash": self.locked_cash,
            "equity": self.equity(marks),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(marks),
            "fees_paid": self.fees_paid,
            "positions": {
                tid: {"qty": p.qty, "avg_price": p.avg_price, "realized_pnl": p.realized_pnl}
                for tid, p in self.positions.items()
                if p.qty > EPS or abs(p.realized_pnl) > EPS
            },
        }
