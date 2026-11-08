"""The paper desk: execute approved tickets on recorded or live order books.

Every ticket goes through the same gate as any other order in polysim:
risk limits, then one approval for the whole basket, then the legs one by one
as fill-or-kill buys with latency, unwinding if a leg fails. Everything is
written to the hash-chained audit log under the ticket's id.

The report compares what the checker expected with what the paper desk got:

* ``slippage``       paper cost minus the checker's expected cost
* ``locked_profit``  guaranteed payout (floor x shares) minus paper cost: the
                     part of the profit that does not depend on any outcome
* ``leg_risk_cost``  what a failed attempt lost on the unwind
* ``settled``        if every leg resolved during the data, what it actually paid,
                     and whether that payout respected the promised floor
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from .approval import Approver, auto_approve
from .audit import AuditLog
from .basket import Basket, BasketLeg
from .engine import Backtester, BacktestResult
from .events import BookEvent, Event
from .models import EPS, Market
from .risk import RiskLimits
from .strategy import Strategy, StrategyContext
from .tickets import Ticket


@dataclass
class TicketOutcome:
    ticket: Ticket
    status: str = "no_data"  # filled | unwound | rejected | expired | no_data
    reason: str = ""
    started_ts: float | None = None
    basket: Basket | None = None
    settled_payout: float | None = None  # USDC actually paid by the legs, if all resolved

    @property
    def paper_cost(self) -> float:
        return self.basket.cost if self.basket else 0.0

    @property
    def slippage(self) -> float | None:
        if self.status != "filled" or not self.ticket.expected_cost:
            return None
        return self.paper_cost - self.ticket.expected_cost

    @property
    def locked_profit(self) -> float | None:
        return self.ticket.guaranteed_value - self.paper_cost if self.status == "filled" else None

    @property
    def leg_risk_cost(self) -> float:
        return self.basket.leg_risk_cost if self.basket else 0.0

    @property
    def realised(self) -> float | None:
        if self.settled_payout is None:
            return None
        if self.status == "filled":
            return self.settled_payout - self.paper_cost
        return None

    @property
    def floor_held(self) -> bool | None:
        """After resolution: did the basket pay at least what the checker promised?"""
        if self.settled_payout is None or self.status != "filled":
            return None
        return self.settled_payout >= self.ticket.guaranteed_value - 1e-6

    def as_dict(self) -> dict:
        t = self.ticket
        return {
            "ticket_id": t.ticket_id,
            "kind": t.kind,
            "legs": [leg.label or f"{leg.outcome} {leg.token_id[:10]}…" for leg in t.legs],
            "shares": t.shares,
            "status": self.status,
            "reason": self.reason,
            "delay_s": None if self.started_ts is None else round(self.started_ts - t.ts, 3),
            "expected_cost": t.expected_cost,
            "expected_profit": t.expected_profit,
            "paper_cost": round(self.paper_cost, 6),
            "slippage": None if self.slippage is None else round(self.slippage, 6),
            "locked_profit": None if self.locked_profit is None else round(self.locked_profit, 6),
            "leg_risk_cost": round(self.leg_risk_cost, 6),
            "settled_payout": self.settled_payout,
            "realised": None if self.realised is None else round(self.realised, 6),
            "floor_held": self.floor_held,
            "basket": self.basket.summary() if self.basket else None,
        }


class TicketTrader(Strategy):
    """Sends each ticket once the data reaches its time and every leg has a fresh book."""

    name = "paper_desk"

    def __init__(self, tickets: Iterable[Ticket], *, max_delay: float = 300.0, max_book_age: float = 120.0) -> None:
        self.pending = sorted(tickets, key=lambda t: t.ts)
        self.outcomes: dict[str, TicketOutcome] = {t.ticket_id: TicketOutcome(t) for t in self.pending}
        self.max_delay, self.max_book_age = max_delay, max_book_age

    def on_book(self, ctx: StrategyContext, book) -> None:
        now = ctx.now
        while self.pending and self.pending[0].ts <= now:
            t = self.pending[0]
            out = self.outcomes[t.ticket_id]
            if now - t.ts > self.max_delay:
                self.pending.pop(0)
                out.status, out.reason = "expired", f"no fresh book for every leg within {self.max_delay:g}s"
                continue
            books = [ctx.book(leg.token_id) for leg in t.legs]
            if any(b is None or now - b.ts > self.max_book_age for b in books):
                # Wait for the next books; later tickets wait too, so they keep their order.
                return
            self.pending.pop(0)
            out.started_ts = now
            legs = [BasketLeg(leg.token_id, t.shares, leg.max_price) for leg in t.legs]
            out.basket = ctx.execute_basket(legs, decision_id=t.ticket_id, tag=f"ticket {t.ticket_id}")

    def on_basket(self, ctx: StrategyContext, basket: Basket) -> None:
        out = self.outcomes.get(basket.decision_id)
        if out is not None:
            out.status, out.reason = basket.status, basket.reason

    def on_end(self, ctx: StrategyContext) -> None:
        now = ctx.now
        for t in self.pending:
            out = self.outcomes[t.ticket_id]
            if t.ts <= now and now - t.ts > self.max_delay:
                out.status, out.reason = "expired", f"no fresh book for every leg within {self.max_delay:g}s"
            else:
                out.reason = "the data ends before every leg had a fresh book"
        for out in self.outcomes.values():
            if out.basket is not None and not out.basket.done:
                out.status, out.reason = out.basket.status, "the data ended while the basket was still executing"


@dataclass
class DeskReport:
    outcomes: list[TicketOutcome]
    result: BacktestResult
    source: str = ""
    problems: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            out[o.status] = out.get(o.status, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {
            "paper_trading": True,
            "source": self.source,
            "counts": self.counts(),
            "tickets": [o.as_dict() for o in self.outcomes],
            "rejected_inputs": self.problems,
            "metrics": self.result.metrics,
            "risk": self.result.risk,
            "audit_ok": self.result.audit_ok,
            "audit_records": self.result.audit_records,
        }


def paper_trade(
    tickets: Iterable[Ticket],
    events: Iterable[Event],
    markets: Iterable[Market],
    *,
    cash: float = 1_000.0,
    latency: float = 2.0,
    risk_limits: RiskLimits | None = None,
    approver: Approver = auto_approve,
    audit: AuditLog | str | None = None,
    max_delay: float = 300.0,
    max_book_age: float = 120.0,
    source: str = "",
    problems: list[str] | None = None,
) -> DeskReport:
    """Replay `events` and execute every ticket at (or just after) its own time."""
    problems = list(problems or [])
    seen, unique = set(), []
    for t in tickets:
        if t.ticket_id in seen:
            problems.append(f"{t.ticket_id}: duplicate ticket id, only the first is traded")
            continue
        seen.add(t.ticket_id)
        unique.append(t)
    tickets = unique
    trader = TicketTrader(tickets, max_delay=max_delay, max_book_age=max_book_age)
    bt = Backtester(events, list(markets), trader, initial_cash=cash, latency=latency,
                    risk_limits=risk_limits, approver=approver, audit=audit)
    result = bt.run()
    payout = {}
    for ev in result.resolutions:
        if all(abs(p) <= EPS or abs(p - 1) <= EPS for p in ev.payouts.values()):
            payout.update(ev.payouts)
    for out in trader.outcomes.values():
        if out.status == "filled" and all(leg.token_id in payout for leg in out.ticket.legs):
            out.settled_payout = sum(payout[leg.token_id] * out.ticket.shares for leg in out.ticket.legs)
    return DeskReport([trader.outcomes[t.ticket_id] for t in sorted(tickets, key=lambda t: t.ts)],
                      result, source, problems)


# --------------------------------------------------------------------------- live


def live_snapshot(client, tickets: Iterable[Ticket], now: float | None = None) -> tuple[list[Market], list[Event]]:
    """Current books for every leg (read-only GETs), as one moment the desk can trade on."""
    tickets = list(tickets)
    now = time.time() if now is None else now
    ids = sorted({leg.market_id for t in tickets for leg in t.legs if leg.market_id})
    markets = client.get_markets_by_condition_ids(ids) if ids else []
    known = {tid for m in markets for tid in m.token_ids}
    missing = sorted({leg.token_id for t in tickets for leg in t.legs} - known)
    if missing:
        raise LookupError(f"no Polymarket market found for token(s) {', '.join(x[:12] + '…' for x in missing)}")
    events: list[Event] = []
    for token in sorted(known & {leg.token_id for t in tickets for leg in t.legs}):
        book = client.get_order_book(token)
        book.token_id, book.ts = token, now
        events.append(BookEvent(now, book))
    return markets, events


def format_report(rep: DeskReport) -> str:
    lines = [f"PAPER RESULT on {rep.source or 'the given data'}: simulated fills, not real-world performance", ""]
    head = f"{'ticket':<26} {'status':<9} {'delay':>6} {'expected':>10} {'paper':>10} {'slippage':>9} {'locked':>9} {'leg-risk':>9}"
    lines += [head, "-" * len(head)]

    def f(x, w, d=2):
        return f"{'—':>{w}}" if x is None else f"{x:>{w}.{d}f}"

    for o in rep.outcomes:
        d = o.as_dict()
        lines.append(f"{o.ticket.ticket_id[:26]:<26} {o.status:<9} {f(d['delay_s'], 6, 1)} {f(o.ticket.expected_cost, 10)} "
                     f"{f(o.paper_cost if o.basket else None, 10)} {f(o.slippage, 9, 3)} {f(o.locked_profit, 9, 3)} "
                     f"{f(o.leg_risk_cost if o.basket else None, 9, 3)}")
        if o.reason:
            lines.append(f"{'':<26} {o.reason}")
        if o.settled_payout is not None:
            held = "floor held" if o.floor_held else "FLOOR BROKEN: the relation did not hold"
            lines.append(f"{'':<26} resolved: paid {o.settled_payout:.2f}, realised {o.realised:+.3f} USDC ({held})")
    c = rep.counts()
    lines += ["", "tickets: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())) if c else "tickets: none"]
    filled = [o for o in rep.outcomes if o.status == "filled"]
    if filled:
        lines.append(f"locked profit on filled tickets: {sum(o.locked_profit for o in filled):+.3f} USDC "
                     f"(checker expected {sum(o.ticket.expected_profit for o in filled):+.3f})")
    lr = sum(o.leg_risk_cost for o in rep.outcomes)
    if lr:
        lines.append(f"leg-risk cost on unwound tickets: {lr:.3f} USDC")
    r = rep.result
    if r.risk.get("rejections"):
        lines.append(f"risk rejections: {r.risk['rejections']}")
    if rep.problems:
        lines.append(f"rejected inputs: {len(rep.problems)}")
        lines += [f"  - {p}" for p in rep.problems]
    lines.append(f"audit log: {r.audit_records} records, hash chain {'verified' if r.audit_ok else 'BROKEN'}")
    return "\n".join(lines)
