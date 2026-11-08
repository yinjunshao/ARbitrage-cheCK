"""`polysim demo`: every control, triggered on screen, on invented data.

Four short scenes, each a separate paper backtest on SYNTHETIC markets:

1. A real ladder arbitrage, bought as one approved basket and held to resolution.
2. The same basket when the second leg's sellers vanish during the delay:
   the first leg is sold back and the leg-risk cost is reported.
3. Risk limits: an oversized order is refused, a price crash trips the kill
   switch, the account goes reduce-only, and only a named person can reset it.
4. The audit log: the hash chain verifies, then one edited line is caught.

Nothing here is real-world performance.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable

from .approval import Approval, ApprovalRequest
from .audit import verify_file
from .basket import BasketLeg
from .engine import Backtester
from .events import BookEvent, ResolutionEvent
from .models import Market, OrderBook, PriceLevel, TimeInForce, Token
from .risk import RiskLimits
from .strategy import Strategy

A_YES, A_NO, B_YES, B_NO = "A-YES", "A-NO", "B-YES", "B-NO"


def ladder_markets() -> list[Market]:
    """'by Nov 30' implies 'by Dec 31', so A-NO + B-YES always pays at least $1."""
    kw = dict(min_order_size=5.0, tick_size=0.01, category="politics", fee_rate=0.04)
    return [
        Market("A", "Will X happen by Nov 30? (invented)", (Token(A_YES, "Yes"), Token(A_NO, "No")), **kw),
        Market("B", "Will X happen by Dec 31? (invented)", (Token(B_YES, "Yes"), Token(B_NO, "No")), **kw),
    ]


def _book(token: str, ts: float, bid: float | None, ask: float | None, size: float = 300) -> BookEvent:
    bids = [PriceLevel(bid, size)] if bid is not None else []
    asks = [PriceLevel(ask, size)] if ask is not None else []
    return BookEvent(ts, OrderBook(token, ts, bids, asks))


class _OnBook(Strategy):
    """Calls `act(ctx)` once, on the n-th book."""

    name = "demo"

    def __init__(self, n: int, act: Callable) -> None:
        self.n, self.act, self.seen = n, act, 0

    def on_book(self, ctx, book):
        self.seen += 1
        if self.seen == self.n:
            self.act(ctx)


def _named(name: str):
    def approve(req: ApprovalRequest) -> Approval:
        return Approval(True, name)

    approve.__name__ = f"approved by {name}"
    return approve


def run_demo(out: Callable[[str], None] = print, audit_dir: str | Path | None = None, approver_name: str = "demo user") -> dict:
    audit_dir = Path(audit_dir or tempfile.mkdtemp(prefix="polysim-demo-"))
    audit_dir.mkdir(parents=True, exist_ok=True)
    results: dict = {}
    legs = [BasketLeg(A_NO, 100, 0.56), BasketLeg(B_YES, 100, 0.41)]

    out("=" * 78)
    out("PAPER DEMO on SYNTHETIC data: invented markets and books, not real-world performance")
    out("=" * 78)

    # ------------------------------------------------------------------ scene 1
    out("\n[1] A real ladder arbitrage, bought as one basket")
    out("    A = 'X by Nov 30', B = 'X by Dec 31'. A implies B, so A-NO + B-YES pays at least $1.")
    out("    Asks: A-NO 0.55, B-YES 0.40. Fees: 0.04 x p x (1-p) per share (politics rate).")
    events = [
        _book(A_NO, 0, 0.53, 0.55), _book(B_YES, 0, 0.38, 0.40),
        ResolutionEvent(100, "A", {A_YES: 0.0, A_NO: 1.0}),
        ResolutionEvent(100, "B", {B_YES: 0.0, B_NO: 1.0}),
    ]
    s = _OnBook(2, lambda ctx: ctx.execute_basket(legs, decision_id="demo-arb-1"))
    bt = Backtester(events, ladder_markets(), s, approver=_named(approver_name), audit=audit_dir / "scene1.jsonl")
    r = bt.run()
    b = r.baskets[0]
    out(f"    approved once by: {b.approved_by}")
    out(f"    basket {b.status}: cost {b.cost:.4f} USDC for 100 baskets (fees included)")
    out("    X never happened: A-NO pays $1, B-YES pays $0 -> 100.00 USDC back")
    out(f"    realised P&L: {r.metrics['realized_pnl']:+.4f} USDC  (locked in whatever happened)")
    results["scene1"] = r

    # ------------------------------------------------------------------ scene 2
    out("\n[2] Same basket, 2 s order delay, and B's sellers disappear meanwhile")
    events = [
        _book(A_NO, 0, 0.53, 0.55), _book(B_YES, 0, 0.38, 0.40),
        _book(A_NO, 2, 0.53, 0.55),        # leg 1 arrives and fills
        _book(B_YES, 3, 0.38, None),       # B's asks are gone
        _book(A_NO, 4, 0.53, 0.55),        # leg 2 arrives: fill-or-kill fails
        _book(A_NO, 6, 0.53, 0.55),        # the pre-approved unwind sell arrives
    ]
    s = _OnBook(2, lambda ctx: ctx.execute_basket(legs, decision_id="demo-arb-2"))
    bt = Backtester(events, ladder_markets(), s, latency=2.0, approver=_named(approver_name),
                    audit=audit_dir / "scene2.jsonl")
    r = bt.run()
    b = r.baskets[0]
    out(f"    basket {b.status}: {b.reason}")
    out(f"    bought leg 1 for {b.cost:.4f}, sold it back for {b.unwind_proceeds:.4f} (no second approval needed)")
    out(f"    leg-risk cost: {b.leg_risk_cost:.4f} USDC  <- what a 'risk-free' arbitrage can still lose")
    results["scene2"] = r

    # ------------------------------------------------------------------ scene 3
    out("\n[3] Risk limits enforced in code: max order 150 USDC, kill switch at 5% drawdown")
    limits = RiskLimits(max_order_notional=150, max_drawdown_pct=5)
    seen: dict = {}

    def open_trades(ctx):
        seen["too_big"] = ctx.buy(A_YES, 400, price=0.45, tif=TimeInForce.FAK)
        seen["ok"] = ctx.buy(A_YES, 200, price=0.45, tif=TimeInForce.FAK)

    def after_crash(ctx):
        seen["buy_more"] = ctx.buy(A_YES, 50, price=0.10, tif=TimeInForce.FAK)
        seen["close"] = ctx.sell(A_YES, 200)

    class Scene3(Strategy):
        name = "demo"

        def __init__(self):
            self.n = 0

        def on_book(self, ctx, book):
            self.n += 1
            (open_trades if self.n == 1 else after_crash if self.n == 2 else (lambda c: None))(ctx)

    events = [_book(A_YES, 0, 0.43, 0.45, 500), _book(A_YES, 1, 0.20, 0.24, 500)]
    bt = Backtester(events, ladder_markets(), Scene3(), risk_limits=limits, approver=_named(approver_name),
                    audit=audit_dir / "scene3.jsonl")
    r = bt.run()
    out(f"    buy 400 @ 0.45  -> {seen['too_big'].status.value}: {seen['too_big'].reject_reason}")
    out(f"    buy 200 @ 0.45  -> {seen['ok'].status.value}")
    out("    the bid drops from 0.43 to 0.20 ...")
    out(f"    KILL SWITCH ON: {r.risk['kill_reason']}")
    out(f"    buy 50 more     -> {seen['buy_more'].status.value}: {seen['buy_more'].reject_reason}")
    out(f"    sell 200        -> {seen['close'].status.value} (closing a position is still allowed)")
    try:
        bt.reset_kill_switch("auto")
    except PermissionError as e:
        out(f"    reset by 'auto' -> refused: {e}")
    bt.reset_kill_switch(approver_name)
    out(f"    reset by '{approver_name}' -> kill switch off: {not bt.risk.killed}")
    results["scene3"] = r
    results["scene3_bt"] = bt

    # ------------------------------------------------------------------ scene 4
    out("\n[4] Every action is in a hash-chained audit log")
    log = audit_dir / "scene3.jsonl"
    ok, _ = verify_file(log)
    lines = log.read_text().splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    out(f"    {log}: {len(lines)} records, chain {'verified' if ok else 'BROKEN'}")
    out(f"    record kinds: {', '.join(sorted(set(kinds)))}")
    tampered = audit_dir / "scene3-tampered.jsonl"
    at = kinds.index("kill_switch")
    rec = json.loads(lines[at])
    rec["payload"]["reason"] = "nothing to see here"
    tampered.write_text("\n".join(lines[:at] + [json.dumps(rec)] + lines[at + 1:]) + "\n")
    ok2, bad = verify_file(tampered)
    out(f"    edited the kill_switch record in a copy -> chain {'verified' if ok2 else 'BROKEN'} at record {bad}")
    results["tamper_detected_at"] = bad
    results["audit_dir"] = audit_dir
    out("\nAll results above are simulated on invented data.")
    return results
