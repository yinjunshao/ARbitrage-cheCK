"""Event-driven backtest loop.

For each event (in timestamp order):

1. deliver latency-delayed orders that have arrived by ``event.ts``;
2. update the book (resting orders may fill) or settle a resolved market;
3. notify the strategy of new fills, then of the event itself;
4. deliver fills caused by orders the strategy just sent (zero latency);
5. record an equity snapshot, marked at the best bid by default (what the
   shares could actually be sold for), and feed it to the kill switch.

Orders a strategy sends while handling an event are matched against the book
it just saw (or, with latency, the book in effect when they arrive) – the
engine never lets a strategy trade on a future snapshot.

Every order goes through the same gate, in this order, and each step is written
to the hash-chained audit log: risk limits -> approval -> exchange. A strategy
has no other way to reach the exchange.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from . import metrics
from .approval import Approval, ApprovalRequest, Approver, auto_approve
from .audit import AuditLog, book_digest
from .basket import Basket, BasketLeg, leg_failed
from .events import BookEvent, Event, PriceEvent, ResolutionEvent
from .exchange import FillMode, SimExchange
from .fees import FeeModel
from .models import EPS, Fill, Market, Order, OrderBook, OrderStatus, OrderType, Side, TimeInForce
from .portfolio import Portfolio
from .risk import RiskLimits, RiskManager, estimate_notional, gross_exposure
from .strategy import Strategy, StrategyContext


@dataclass(slots=True)
class BookSynthesizer:
    """Turns a bare price observation into an L2 book (for price-history-only data)."""

    spread: float = 0.02
    levels: int = 5
    size_per_level: float = 500.0

    def make(self, token_id: str, ts: float, price: float, tick: float) -> OrderBook:
        return OrderBook.from_mid(
            token_id,
            ts,
            price,
            spread=self.spread,
            levels=self.levels,
            size_per_level=self.size_per_level,
            tick=tick,
        )


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: list[tuple[float, float]]
    fills: list[Fill]
    orders: list[Order]
    final_portfolio: dict
    resolutions: list[ResolutionEvent] = field(default_factory=list)
    baskets: list[Basket] = field(default_factory=list)
    risk: dict = field(default_factory=dict)
    audit_ok: bool = True
    audit_records: int = 0
    manual_settlements: list[str] = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"final equity   {m['final_equity']:>12.2f} USDC  (start {m['initial_cash']:.2f})",
            f"pnl            {m['pnl']:>12.2f} USDC  ({m['return_pct']:+.2f}%)",
            f"realized pnl   {m['realized_pnl']:>12.2f} USDC",
            f"max drawdown   {m['max_drawdown_pct']:>12.2f} %",
            f"sharpe/step    {m['sharpe_per_step']:>12.4f}",
            f"fills          {m['n_fills']:>12d}  (maker {m['maker_fills']})",
            f"volume         {m['volume_usdc']:>12.2f} USDC",
            f"fees           {m['fees_paid']:>12.4f} USDC",
            f"risk rejects   {self.risk.get('rejections', 0):>12d}",
            f"kill switch    {'ON: ' + self.risk['kill_reason'] if self.risk.get('killed') else 'off':>12}",
        ]
        if self.baskets:
            filled = sum(b.status == "filled" for b in self.baskets)
            unwound = sum(b.status == "unwound" for b in self.baskets)
            leg_risk = sum(b.leg_risk_cost for b in self.baskets)
            lines.append(f"baskets        {len(self.baskets):>12d}  (filled {filled}, unwound {unwound})")
            lines.append(f"leg-risk cost  {leg_risk:>12.4f} USDC")
        if self.manual_settlements:
            lines.append(f"needs manual   {len(self.manual_settlements):>12d}  market(s) with disputed results")
        verdict = "verified" if self.audit_ok else "BROKEN"
        lines.append(f"audit log      {self.audit_records:>12d}  records, hash chain {verdict}")
        return "\n".join(lines)


class Backtester:
    def __init__(
        self,
        events: Iterable[Event],
        markets: Iterable[Market],
        strategy: Strategy,
        *,
        initial_cash: float = 1_000.0,
        fee_model: FeeModel | None = None,
        latency: float = 0.0,
        fill_mode: FillMode | str = FillMode.TOUCH,
        synthesizer: BookSynthesizer | None = None,
        risk_limits: RiskLimits | None = None,
        approver: Approver = auto_approve,
        audit: AuditLog | str | Path | None = None,
        mark: str = "bid",
    ) -> None:
        if mark not in ("bid", "mid"):
            raise ValueError("mark must be 'bid' or 'mid'")
        self.events = events
        self.markets: dict[str, Market] = {m.market_id: m for m in markets}
        self.strategy = strategy
        self.portfolio = Portfolio(initial_cash)
        self.exchange = SimExchange(
            self.portfolio,
            self.markets.values(),
            fee_model=fee_model,
            latency=latency,
            fill_mode=FillMode(fill_mode),
        )
        self.synthesizer = synthesizer or BookSynthesizer()
        self.risk = RiskManager(risk_limits)
        self.approver = approver
        self.audit = audit if isinstance(audit, AuditLog) else AuditLog(audit)
        self.mark = mark
        self.ctx = StrategyContext(self)
        self.marks: dict[str, float] = {}
        self.equity_curve: list[tuple[float, float]] = []
        self.resolutions: list[ResolutionEvent] = []
        self.baskets: list[Basket] = []
        self.manual_settlements: list[str] = []
        self._fill_owner: dict[str, Basket] = {}

    def equity(self) -> float:
        return self.portfolio.equity(self.marks)

    def run(self) -> BacktestResult:
        self._log(
            "run_start",
            {
                "strategy": getattr(self.strategy, "name", type(self.strategy).__name__),
                "initial_cash": self.portfolio.initial_cash,
                "markets": [m.market_id for m in self.markets.values()],
                "risk_limits": self.risk.limits.as_dict(),
                "approver": getattr(self.approver, "__name__", repr(self.approver)),
                "latency_s": self.exchange.latency,
                "fill_mode": self.exchange.fill_mode.value,
                "mark": self.mark,
            },
        )
        self.strategy.on_start(self.ctx)
        last_ts = None
        for ev in self.events:
            if last_ts is not None and ev.ts < last_ts:
                raise ValueError(f"events out of order: {ev.ts} < {last_ts}")
            last_ts = ev.ts
            self._step(ev)
        self.strategy.on_end(self.ctx)
        self._dispatch_fills()
        self._log("run_end", {"equity": self.equity(), "risk": self.risk.status()})
        return self._result()

    # ------------------------------------------------------------------ order gate

    def submit_order(self, order: Order, *, pre_approved: str = "") -> Order:
        """The only way an order reaches the exchange: risk check -> approval -> exchange.

        ``pre_approved`` names an approval already given (a basket's), so the
        approver is not asked again. Risk limits are still checked.
        """
        ex = self.exchange
        book = ex.book(order.token_id)
        decision = self.risk.check_order(order, self.portfolio, book, ex.now)
        self._log(
            "risk_check",
            {"order": _order_dict(order), "book": book_digest(book), **decision.as_dict()},
        )
        if not decision.ok:
            return self._refuse(order, f"risk: {decision.reason}")

        if pre_approved:
            approval = Approval(True, pre_approved)
        else:
            notional = estimate_notional(order, book) if order.side is Side.BUY else None
            approval = self.approver(
                ApprovalRequest(
                    "order",
                    f"{order.side.value} {order.size:g} of {order.token_id}"
                    + (f" at {order.price:g}" if order.price is not None else " at market"),
                    {"order": _order_dict(order), "est_notional": notional},
                    ex.now,
                )
            )
            self._log("approval", {"order_id": order.order_id, **_approval_dict(approval)})
        if not approval.approved:
            return self._refuse(order, f"not approved by {approval.by}")

        ex.submit(order)
        self._log("order_submitted", {"order": _order_dict(order)})
        return order

    def _refuse(self, order: Order, reason: str) -> Order:
        order.status, order.reject_reason, order.created_ts = OrderStatus.REJECTED, reason, self.exchange.now
        self.exchange.orders[order.order_id] = order
        self._log("order_rejected", {"order_id": order.order_id, "reason": reason})
        return order

    # ------------------------------------------------------------------ baskets

    def execute_basket(self, legs: Iterable[BasketLeg], *, decision_id: str = "", tag: str = "") -> Basket:
        """Approve a multi-leg buy once, then send the legs one by one (see :mod:`polysim.basket`)."""
        b = Basket(tuple(legs), decision_id=decision_id, tag=tag)
        self.baskets.append(b)
        ex = self.exchange
        if not b.legs:
            return self._basket_done(b, "rejected", "basket has no legs")

        # 1. risk, for the basket as a whole, before anything is sent
        pending, checks = 0.0, []
        for i, leg in enumerate(b.legs):
            probe = Order(leg.token_id, Side.BUY, leg.size, leg.max_price,
                          OrderType.LIMIT if leg.max_price is not None else OrderType.MARKET, TimeInForce.FOK)
            d = self.risk.check_order(probe, self.portfolio, ex.book(leg.token_id), ex.now,
                                      pending_exposure=pending, pending_orders=i, count=False)
            checks.append({"leg": i, **d.as_dict()})
            if not d.ok:
                self._log("basket_risk_check", {"basket_id": b.basket_id, "legs": checks})
                return self._basket_done(b, "rejected", f"risk (leg {i}): {d.reason}")
            pending += estimate_notional(probe, ex.book(leg.token_id))
        self._log("basket_risk_check", {"basket_id": b.basket_id, "legs": checks, "est_cost": pending})

        # 2. one approval for the whole plan, including the unwind
        approval = self.approver(
            ApprovalRequest(
                "basket",
                f"buy {len(b.legs)} legs, about {pending:.2f} USDC; if a leg fails, sell the filled legs back",
                {
                    "basket_id": b.basket_id,
                    "decision_id": decision_id,
                    "legs": [vars(leg) for leg in b.legs],
                    "est_cost": round(pending, 6),
                    "unwind_on_failure": True,
                },
                ex.now,
            )
        )
        self._log("approval", {"basket_id": b.basket_id, **_approval_dict(approval)})
        if not approval.approved:
            return self._basket_done(b, "rejected", f"not approved by {approval.by}")
        b.approved_by = approval.by

        # 3. send the first leg; the rest follow as each one finishes
        b.status = "executing"
        self._send_leg(b)
        self._progress_baskets()
        return b

    def _send_leg(self, b: Basket) -> None:
        leg = b.legs[len(b.orders)]
        order = Order(
            leg.token_id,
            Side.BUY,
            leg.size,
            leg.max_price,
            OrderType.LIMIT if leg.max_price is not None else OrderType.MARKET,
            TimeInForce.FOK,
            tag=b.tag or b.basket_id,
            decision_id=b.decision_id,
        )
        b.orders.append(order)
        self._fill_owner[order.order_id] = b
        self.submit_order(order, pre_approved=f"{b.approved_by} (basket {b.basket_id})")

    def _start_unwind(self, b: Basket, reason: str) -> None:
        b.status, b.reason = "unwinding", reason
        self._log("basket_leg_failed", {"basket_id": b.basket_id, "reason": reason})
        for leg_order in b.orders:
            if leg_order.filled_size > EPS:
                o = Order(leg_order.token_id, Side.SELL, leg_order.filled_size, None, OrderType.MARKET,
                          TimeInForce.FAK, tag=f"unwind {b.basket_id}", decision_id=b.decision_id)
                b.unwind_orders.append(o)
                self._fill_owner[o.order_id] = b
                self.submit_order(o, pre_approved=f"pre-approved unwind (basket {b.basket_id})")

    def _progress_baskets(self) -> None:
        changed = True
        while changed:  # with zero latency a whole basket can finish in one call
            changed = False
            self._dispatch_fills()
            for b in self.baskets:
                if b.status == "executing":
                    cur = b.orders[-1]
                    if cur.is_active:
                        continue
                    changed = True
                    if leg_failed(cur):
                        self._start_unwind(b, f"leg {len(b.orders) - 1} not filled: {cur.reject_reason or cur.status.value}")
                    elif len(b.orders) == len(b.legs):
                        self._basket_done(b, "filled")
                    else:
                        self._send_leg(b)
                elif b.status == "unwinding" and not any(o.is_active for o in b.unwind_orders):
                    changed = True
                    self._basket_done(b, "unwound", b.reason)

    def _basket_done(self, b: Basket, status: str, reason: str = "") -> Basket:
        b.status, b.reason = status, reason
        self._log(f"basket_{status}", b.summary())
        self.strategy.on_basket(self.ctx, b)
        return b

    # ------------------------------------------------------------------ kill switch

    def reset_kill_switch(self, by: str) -> None:
        """Human-only: turn the kill switch off again."""
        self.risk.reset(by, self.equity())
        self._log("kill_switch_reset", {"by": by})

    # ------------------------------------------------------------------ manual settlement

    def settle_manually(self, market_id: str, payouts: dict[str, float], by: str) -> None:
        """Settle a market whose result was not cleanly 0/1, after a person has checked it."""
        if market_id not in self.manual_settlements:
            raise ValueError(f"{market_id} is not waiting for a manual settlement")
        self.manual_settlements.remove(market_id)
        self._settle(market_id, payouts, kind="manual_settlement", by=by)

    # ------------------------------------------------------------------ internals

    def _step(self, ev: Event) -> None:
        ex = self.exchange
        ex.advance_time(ev.ts)
        self._progress_baskets()

        if isinstance(ev, PriceEvent):
            ev = BookEvent(ev.ts, self.synthesizer.make(ev.token_id, ev.ts, ev.price, ex.tick_size(ev.token_id)))

        if isinstance(ev, BookEvent):
            ex.on_book(ev.book)
            mark = ev.book.best_bid if self.mark == "bid" else ev.book.mid
            if mark is not None:
                self.marks[ev.token_id] = mark
            self._progress_baskets()
            self._check_equity(ev.ts)  # the kill switch sees the new prices before the strategy does
            self.strategy.on_book(self.ctx, ex.book(ev.token_id))
        elif isinstance(ev, ResolutionEvent):
            self._resolve(ev)
            self._check_equity(ev.ts)
            self.strategy.on_resolution(self.ctx, ev)
        else:
            raise TypeError(f"unsupported event {ev!r}")

        self._progress_baskets()
        self.equity_curve.append((ev.ts, self._check_equity(ev.ts)))

    def _check_equity(self, ts: float) -> float:
        """Feed equity to the kill switch; if it trips, cancel resting buys (reduce-only)."""
        equity = self.equity()
        tripped = self.risk.update_equity(ts, equity)
        if tripped:
            ex = self.exchange
            cancelled = [o.order_id for o in ex.open_orders() if o.side is Side.BUY]
            for oid in cancelled:
                ex.cancel(oid)
            self._log("kill_switch", {"reason": tripped, "equity": equity, "cancelled_buys": cancelled})
            self.strategy.on_kill_switch(self.ctx, tripped)
        return equity

    def _resolve(self, ev: ResolutionEvent) -> None:
        self.resolutions.append(ev)
        self.exchange.cancel_all(ev.payouts.keys())
        m = self.markets.get(ev.market_id)
        if m is not None:
            closed = replace(m, closed=True, active=False)
            self.markets[ev.market_id] = closed
            self.exchange.add_market(closed)
        clean = all(abs(p) <= EPS or abs(p - 1) <= EPS for p in ev.payouts.values())
        if not clean:
            # Disputed or split results: never settle these automatically.
            self.manual_settlements.append(ev.market_id)
            self._log("manual_settlement_required", {"market_id": ev.market_id, "reported_payouts": ev.payouts})
            return
        self._settle(ev.market_id, ev.payouts, kind="settlement", by="Gamma closed + outcomePrices")

    def _settle(self, market_id: str, payouts: dict[str, float], *, kind: str, by: str) -> None:
        credited = {}
        for token_id, payout in payouts.items():
            credited[token_id] = self.portfolio.settle(token_id, payout)
            self.marks[token_id] = payout
        self._log(kind, {"market_id": market_id, "payouts": payouts, "credited": credited, "by": by})

    def _dispatch_fills(self) -> None:
        # on_fill may place orders that fill immediately; loop until quiet.
        while fills := self.exchange.drain_fills():
            for f in fills:
                self._log("fill", _fill_dict(f))
                owner = self._fill_owner.get(f.order_id)
                if owner is not None:
                    owner.fills.append(f)
                self.strategy.on_fill(self.ctx, f)

    def _log(self, kind: str, payload: dict) -> None:
        self.audit.append(kind, {"sim_ts": self.exchange.now, **payload})

    def _result(self) -> BacktestResult:
        eq = [v for _, v in self.equity_curve]
        ok, _ = self.audit.verify()
        return BacktestResult(
            metrics=metrics.summarize(
                self.portfolio.initial_cash,
                eq,
                self.exchange.fills,
                self.portfolio.realized_pnl,
                self.portfolio.fees_paid,
            ),
            equity_curve=self.equity_curve,
            fills=list(self.exchange.fills),
            orders=list(self.exchange.orders.values()),
            final_portfolio={**self.portfolio.snapshot(self.marks), "gross_exposure": gross_exposure(self.portfolio)},
            resolutions=self.resolutions,
            baskets=list(self.baskets),
            risk=self.risk.status(),
            audit_ok=ok,
            audit_records=len(self.audit),
            manual_settlements=list(self.manual_settlements),
        )


def _order_dict(o: Order) -> dict:
    return {
        "order_id": o.order_id,
        "token_id": o.token_id,
        "side": o.side.value,
        "size": o.size,
        "price": o.price,
        "type": o.order_type.value,
        "tif": o.tif.value,
        "status": o.status.value,
        "filled": o.filled_size,
        "tag": o.tag,
        "decision_id": o.decision_id,
        "reject_reason": o.reject_reason,
    }


def _fill_dict(f: Fill) -> dict:
    return {
        "order_id": f.order_id,
        "token_id": f.token_id,
        "side": f.side.value,
        "price": f.price,
        "size": f.size,
        "fee": f.fee,
        "liquidity": f.liquidity.value,
        "fill_ts": f.ts,
    }


def _approval_dict(a: Approval) -> dict:
    return {"approved": a.approved, "by": a.by, "note": a.note}
