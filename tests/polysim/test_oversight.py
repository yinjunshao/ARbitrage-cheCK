"""Risk limits, kill switch, approval, audit log, baskets and manual settlement."""

import json

import pytest

from polysim_helpers import NO, YES, make_book
from polysim import (
    AuditLog,
    Backtester,
    BasketLeg,
    BookEvent,
    Market,
    OrderStatus,
    ResolutionEvent,
    RiskLimits,
    Strategy,
    TimeInForce,
    Token,
    cli_approver,
    deny_all,
    verify_file,
)
from polysim.risk import estimate_notional
from polysim.models import Order, OrderBook, OrderType, PriceLevel, Side

A_YES, A_NO, B_YES, B_NO = "A-YES", "A-NO", "B-YES", "B-NO"


@pytest.fixture
def ladder():
    """'by Nov 30' (A) implies 'by Dec 31' (B): buying A-NO + B-YES pays at least $1."""
    a = Market("A", "Will X happen by Nov 30?", (Token(A_YES, "Yes"), Token(A_NO, "No")), min_order_size=1.0)
    b = Market("B", "Will X happen by Dec 31?", (Token(B_YES, "Yes"), Token(B_NO, "No")), min_order_size=1.0)
    return a, b


def book(token, ts, bids=((0.40, 200),), asks=((0.42, 200),)):
    return make_book(ts, bids=bids, asks=asks, token=token)


class Script(Strategy):
    """Runs `actions[i]` on the i-th book the strategy sees."""

    def __init__(self, *actions):
        self.actions = list(actions)
        self.n = 0
        self.out = {}
        self.baskets = []
        self.kills = []

    def on_book(self, ctx, book):
        if self.n < len(self.actions) and self.actions[self.n]:
            self.actions[self.n](ctx, self.out)
        self.n += 1

    def on_basket(self, ctx, basket):
        self.baskets.append(basket)

    def on_kill_switch(self, ctx, reason):
        self.kills.append(reason)


# --------------------------------------------------------------------------- limits


def test_order_notional_limit_rejects_before_the_exchange(market):
    s = Script(lambda ctx, out: out.setdefault("o", ctx.buy(YES, 100, price=0.52, tif=TimeInForce.FAK)))
    bt = Backtester([BookEvent(0, make_book(0))], [market], s, risk_limits=RiskLimits(max_order_notional=20))
    res = bt.run()
    o = s.out["o"]
    assert o.status is OrderStatus.REJECTED
    assert o.reject_reason.startswith("risk: order 52.00 vs max 20.00")
    assert res.fills == [] and res.risk["rejections"] == 1
    assert [r["kind"] for r in bt.audit.records()][-2:] == ["order_rejected", "run_end"]


def test_position_cost_and_gross_exposure_limits(market):
    lim = RiskLimits(max_position_cost=60, max_gross_exposure=80)

    def act(ctx, out):
        out["first"] = ctx.buy(YES, 100, price=0.52, tif=TimeInForce.FAK)  # 52 USDC: ok
        out["same_token"] = ctx.buy(YES, 20, price=0.53, tif=TimeInForce.FAK)  # position 62.6 > 60
        out["gross"] = ctx.buy(NO, 60, price=0.50, tif=TimeInForce.GTC)  # 52 + 30 > 80

    s = Script(act)
    Backtester([BookEvent(0, make_book(0))], [market], s, risk_limits=lim).run()
    assert s.out["first"].status is OrderStatus.FILLED
    assert "position 62.60 vs max 60.00" in s.out["same_token"].reject_reason
    assert "exposure 82.00 vs max 80.00" in s.out["gross"].reject_reason


def test_max_orders_per_day_resets_next_day(market):
    lim = RiskLimits(max_orders_per_day=1)
    buy = lambda ctx, out: out.setdefault(ctx.now, []).append(ctx.buy(YES, 1, price=0.30))
    s = Script(buy, buy, buy)
    events = [BookEvent(0, make_book(0)), BookEvent(10, make_book(10)), BookEvent(86_400, make_book(86_400))]
    Backtester(events, [market], s, risk_limits=lim).run()
    assert s.out[0][0].status is OrderStatus.OPEN
    assert "order 2 of max 1 today" in s.out[10][0].reject_reason
    assert s.out[86_400][0].status is OrderStatus.OPEN


def test_market_order_notional_walks_the_asks_and_prices_the_rest_at_one_dollar():
    b = OrderBook(YES, 0, [], [PriceLevel(0.50, 10), PriceLevel(0.60, 10)])
    o = Order(YES, Side.BUY, 25, None, OrderType.MARKET, TimeInForce.FAK)
    assert estimate_notional(o, b) == pytest.approx(5 + 6 + 5 * 1.0)


# --------------------------------------------------------------------------- kill switch


def test_drawdown_trips_kill_switch_and_account_goes_reduce_only(market):
    def open_trade(ctx, out):
        out["buy"] = ctx.buy(YES, 100, price=0.52, tif=TimeInForce.FAK)
        out["resting"] = ctx.buy(NO, 10, price=0.30)

    def after_crash(ctx, out):
        out["buy_again"] = ctx.buy(YES, 10, price=0.20, tif=TimeInForce.FAK)
        out["sell"] = ctx.sell(YES, 100)

    s = Script(open_trade, after_crash)
    crash = make_book(1, bids=((0.10, 500),), asks=((0.15, 500),))
    bt = Backtester([BookEvent(0, make_book(0)), BookEvent(1, crash)], [market], s,
                    risk_limits=RiskLimits(max_drawdown_pct=3))
    res = bt.run()
    # peak 1000 (before the trade) -> 958 (cash 948 + 100 shares marked at the 0.10 bid).
    # The switch trips on the new book BEFORE the strategy can react to it.
    assert res.risk["killed"] and "drawdown 4.20%" in res.risk["kill_reason"]
    assert s.kills and s.out["resting"].status is OrderStatus.CANCELLED  # resting buys cancelled
    assert "reduce-only" in s.out["buy_again"].reject_reason
    assert s.out["sell"].status is OrderStatus.FILLED  # closing a position is still allowed
    kill = bt.audit.records("kill_switch")[0]["payload"]
    assert kill["cancelled_buys"] == [s.out["resting"].order_id]


def test_daily_loss_limit(market):
    s = Script(lambda ctx, out: ctx.buy(YES, 100, price=0.52, tif=TimeInForce.FAK))
    crash = make_book(1, bids=((0.30, 500),), asks=((0.35, 500),))
    res = Backtester([BookEvent(0, make_book(0)), BookEvent(1, crash)], [market], s,
                     risk_limits=RiskLimits(max_daily_loss=10)).run()
    assert res.risk["killed"] and "daily loss 22.00" in res.risk["kill_reason"]  # 1000 -> 948 + 30


def test_only_a_named_human_can_reset_the_kill_switch(market):
    bt = Backtester([], [market], Strategy())
    bt.risk.trip("test", 0)
    with pytest.raises(PermissionError):
        bt.reset_kill_switch("auto_approve")
    bt.reset_kill_switch("Louis")
    assert not bt.risk.killed
    assert bt.audit.records("kill_switch_reset")[0]["payload"]["by"] == "Louis"


# --------------------------------------------------------------------------- approval


def test_denied_orders_never_reach_the_exchange(market):
    s = Script(lambda ctx, out: out.setdefault("o", ctx.buy(YES, 10, price=0.52, tif=TimeInForce.FAK)))
    bt = Backtester([BookEvent(0, make_book(0))], [market], s, approver=deny_all)
    res = bt.run()
    assert s.out["o"].reject_reason == "not approved by deny_all"
    assert res.fills == []
    assert bt.audit.records("approval")[0]["payload"]["by"] == "deny_all"


def test_cli_approver_records_the_persons_name(market):
    answers = iter(["y", "n"])
    approver = cli_approver("Louis", input_fn=lambda _: next(answers), print_fn=lambda *a: None)

    def act(ctx, out):
        out["yes"] = ctx.buy(YES, 10, price=0.52, tif=TimeInForce.FAK)
        out["no"] = ctx.buy(YES, 10, price=0.52, tif=TimeInForce.FAK)

    s = Script(act)
    bt = Backtester([BookEvent(0, make_book(0))], [market], s, approver=approver)
    bt.run()
    assert s.out["yes"].status is OrderStatus.FILLED
    assert s.out["no"].reject_reason == "not approved by Louis"
    assert [r["payload"]["by"] for r in bt.audit.records("approval")] == ["Louis", "Louis"]


def test_backtests_say_they_were_auto_approved(market):
    s = Script(lambda ctx, out: ctx.buy(YES, 10, price=0.52, tif=TimeInForce.FAK))
    bt = Backtester([BookEvent(0, make_book(0))], [market], s)
    bt.run()
    assert bt.audit.records("approval")[0]["payload"]["by"] == "auto_approve (backtest)"


# --------------------------------------------------------------------------- audit


def test_audit_chain_verifies_and_detects_tampering(market, tmp_path):
    path = tmp_path / "audit.jsonl"
    s = Script(lambda ctx, out: ctx.buy(YES, 10, price=0.52, tif=TimeInForce.FAK))
    res = Backtester([BookEvent(0, make_book(0))], [market], s, audit=path).run()
    assert res.audit_ok and verify_file(path) == (True, None)

    lines = path.read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert all(r["paper_trading"] is True for r in records)
    fill_at = next(i for i, r in enumerate(records) if r["kind"] == "fill")

    edited = dict(records[fill_at], payload={**records[fill_at]["payload"], "price": 0.01})
    path.write_text("\n".join(lines[:fill_at] + [json.dumps(edited)] + lines[fill_at + 1:]) + "\n")
    assert verify_file(path) == (False, fill_at)

    path.write_text("\n".join(lines[:fill_at] + lines[fill_at + 1:]) + "\n")  # delete a line
    assert verify_file(path) == (False, fill_at)


def test_audit_log_continues_an_existing_chain(tmp_path):
    path = tmp_path / "a.jsonl"
    AuditLog(path).append("one", {})
    log = AuditLog(path)
    log.append("two", {})
    assert len(log) == 2 and log.verify() == (True, None)


def test_risk_checks_record_the_book_they_saw(market):
    s = Script(lambda ctx, out: ctx.buy(YES, 10, price=0.52, tif=TimeInForce.FAK))
    bt = Backtester([BookEvent(0, make_book(0))], [market], s)
    bt.run()
    check = bt.audit.records("risk_check")[0]["payload"]
    assert check["ok"] and check["book"]["best_ask"] == 0.52 and len(check["book"]["sha256"]) == 64


# --------------------------------------------------------------------------- baskets


def test_basket_fills_every_leg_with_one_approval(ladder):
    a, b = ladder
    answers = []

    def approver(req):
        answers.append(req.kind)
        from polysim import Approval
        return Approval(True, "Louis")

    s = Script(None, lambda ctx, out: out.setdefault(
        "b", ctx.execute_basket([BasketLeg(A_NO, 50, 0.60), BasketLeg(B_YES, 50, 0.40)], decision_id="d-1")))
    events = [BookEvent(0, book(A_NO, 0, asks=((0.55, 100),))), BookEvent(0, book(B_YES, 0, asks=((0.38, 100),)))]
    bt = Backtester(events, [a, b], s, approver=approver)
    bt.run()
    basket = s.out["b"]
    assert basket.status == "filled" and s.baskets == [basket]
    assert answers == ["basket"]  # legs did not ask again
    assert basket.cost == pytest.approx(50 * 0.55 + 50 * 0.38)
    assert all(o.decision_id == "d-1" for o in basket.orders)
    assert bt.audit.records("basket_filled")[0]["payload"]["approved_by"] == "Louis"


def test_basket_unwinds_when_a_later_leg_cannot_fill(ladder):
    """With 1s latency the second leg arrives after its book has emptied, so the first leg is sold back."""
    a, b = ladder
    s = Script(None, lambda ctx, out: out.setdefault(
        "b", ctx.execute_basket([BasketLeg(A_NO, 50, 0.60), BasketLeg(B_YES, 50, 0.40)])))
    events = [
        BookEvent(0, book(A_NO, 0, bids=((0.50, 100),), asks=((0.55, 100),))),
        BookEvent(0, book(B_YES, 0, asks=((0.38, 100),))),
        BookEvent(1, book(A_NO, 1, bids=((0.50, 100),), asks=((0.55, 100),))),  # leg 1 arrives and fills
        BookEvent(1.5, book(B_YES, 1.5, asks=())),  # B's sellers disappear
        BookEvent(2, book(A_NO, 2, bids=((0.50, 100),), asks=((0.55, 100),))),  # leg 2 arrives: FOK fails
        BookEvent(3, book(A_NO, 3, bids=((0.50, 100),), asks=((0.55, 100),))),  # unwind sell arrives
    ]
    bt = Backtester(events, [a, b], s, latency=1.0)
    res = bt.run()
    basket = s.out["b"]
    assert basket.status == "unwound"
    assert "leg 1 not filled: FOK not fully fillable" in basket.reason
    assert basket.cost == pytest.approx(27.5) and basket.unwind_proceeds == pytest.approx(25.0)
    assert basket.leg_risk_cost == pytest.approx(2.5)
    assert bt.portfolio.qty(A_NO) == 0 and basket.residual_shares == {}
    assert "leg-risk cost        2.5000" in res.summary()


def test_basket_is_rejected_up_front_if_the_whole_plan_breaks_a_limit(ladder):
    a, b = ladder
    s = Script(None, lambda ctx, out: out.setdefault(
        "b", ctx.execute_basket([BasketLeg(A_NO, 50, 0.60), BasketLeg(B_YES, 50, 0.40)])))
    events = [BookEvent(0, book(A_NO, 0)), BookEvent(0, book(B_YES, 0))]
    bt = Backtester(events, [a, b], s, risk_limits=RiskLimits(max_gross_exposure=40))
    res = bt.run()
    assert s.out["b"].status == "rejected" and "risk (leg 1)" in s.out["b"].reason
    assert s.out["b"].orders == [] and res.fills == []


# --------------------------------------------------------------------------- settlement


def test_disputed_results_wait_for_a_person(market):
    s = Script(lambda ctx, out: ctx.buy(YES, 100, price=0.52, tif=TimeInForce.FAK))
    events = [BookEvent(0, make_book(0)), ResolutionEvent(1, market.market_id, {YES: 0.5, NO: 0.5})]
    bt = Backtester(events, [market], s)
    res = bt.run()
    assert res.manual_settlements == [market.market_id]
    assert bt.portfolio.qty(YES) == 100  # nothing paid out automatically
    assert "needs manual" in res.summary()

    bt.settle_manually(market.market_id, {YES: 1.0, NO: 0.0}, by="Louis")
    assert bt.portfolio.qty(YES) == 0 and bt.portfolio.cash == pytest.approx(1000 - 52 + 100)
    assert bt.audit.records("manual_settlement")[0]["payload"]["by"] == "Louis"
