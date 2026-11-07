import pytest

from polysim_helpers import NO, YES, make_book
from polysim import Backtester, BookEvent, PriceEvent, ResolutionEvent, Strategy, TimeInForce
from polysim.data import SyntheticConfig, generate_binary_market
from polysim.strategies import REGISTRY, BuyAndHold


def test_buy_and_hold_settles_at_resolution(market):
    events = [
        BookEvent(0, make_book(0)),
        BookEvent(1, make_book(1, bids=((0.70, 100),), asks=((0.72, 100),))),
        ResolutionEvent(2, market.market_id, {YES: 1.0, NO: 0.0}),
    ]
    res = Backtester(events, [market], BuyAndHold("Yes", 100)).run()
    assert res.metrics["final_equity"] == pytest.approx(1000 - 52 + 100)
    assert res.metrics["realized_pnl"] == pytest.approx(48)
    # marked at the best bid (what the shares could be sold for), then settled at $1
    assert [round(v, 6) for _, v in res.equity_curve] == [1000 - 52 + 48, 1000 - 52 + 70, 1048]
    mid = Backtester(events, [market], BuyAndHold("Yes", 100), mark="mid").run()
    assert [round(v, 6) for _, v in mid.equity_curve] == [1000 - 52 + 50, 1000 - 52 + 71, 1048]


def test_resolution_cancels_open_orders_and_closes_market(market):
    class Quoter(Strategy):
        def on_book(self, ctx, book):
            ctx.buy(YES, 10, price=0.40)

        def on_resolution(self, ctx, ev):
            self.after = ctx.buy(YES, 10, price=0.40)

    s = Quoter()
    events = [BookEvent(0, make_book(0)), ResolutionEvent(1, market.market_id, {YES: 0.0, NO: 1.0})]
    bt = Backtester(events, [market], s)
    res = bt.run()
    assert bt.portfolio.locked_cash == 0
    assert s.after.reject_reason == "market closed"
    assert res.metrics["final_equity"] == pytest.approx(1000)


def test_price_events_are_turned_into_books(market):
    class Taker(Strategy):
        def on_book(self, ctx, book):
            if ctx.position(YES).qty == 0:
                ctx.buy(YES, 10, tif=TimeInForce.FAK)

    events = [PriceEvent(0, YES, 0.30), PriceEvent(1, YES, 0.40)]
    res = Backtester(events, [market], Taker()).run()
    assert res.fills[0].price == pytest.approx(0.31)  # mid 0.30 + half of 0.02 spread
    assert res.final_portfolio["positions"][YES]["qty"] == pytest.approx(10)


def test_out_of_order_events_raise(market):
    with pytest.raises(ValueError):
        Backtester([PriceEvent(1, YES, 0.3), PriceEvent(0, YES, 0.3)], [market], Strategy()).run()


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_example_strategies_run_and_conserve_cash(name):
    market, events = generate_binary_market(SyntheticConfig(n_steps=300), seed=3)
    bt = Backtester(events, [market], REGISTRY[name]())
    res = bt.run()
    # after resolution everything is cash; equity must equal cash
    assert res.metrics["final_equity"] == pytest.approx(bt.portfolio.cash)
    assert bt.portfolio.locked_cash == pytest.approx(0)
    assert res.metrics["pnl"] == pytest.approx(res.metrics["realized_pnl"])


def test_synthetic_generator_is_deterministic():
    a = generate_binary_market(seed=7)[1]
    b = generate_binary_market(seed=7)[1]
    assert [e.ts for e in a] == [e.ts for e in b]
    assert a[10].book.asks[0].size == b[10].book.asks[0].size
    assert isinstance(a[-1], ResolutionEvent)
