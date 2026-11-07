import pytest

from conftest import NO, YES, make_book
from polysim import FeeModel, FillMode, Liquidity, Order, OrderStatus, OrderType, Side, TimeInForce


def mkt(side, size, price=None):
    return Order(YES, side, size, price, OrderType.MARKET, TimeInForce.FAK)


def lim(side, size, price, tif=TimeInForce.GTC):
    return Order(YES, side, size, price, OrderType.LIMIT, tif)


def test_market_buy_walks_the_book_and_consumes_liquidity(exchange):
    ex = exchange()
    ex.on_book(make_book())
    o = ex.submit(mkt(Side.BUY, 150))
    assert o.status is OrderStatus.FILLED
    assert o.avg_fill_price == pytest.approx((0.52 * 100 + 0.53 * 50) / 150)
    assert ex.portfolio.qty(YES) == pytest.approx(150)
    # consumed liquidity is gone until the next snapshot
    assert ex.book(YES).best_ask == pytest.approx(0.53)
    assert ex.book(YES).asks[0].size == pytest.approx(150)


def test_market_buy_with_price_cap_is_partial_and_cancelled(exchange):
    ex = exchange()
    ex.on_book(make_book())
    o = ex.submit(mkt(Side.BUY, 150, price=0.52))
    assert o.filled_size == pytest.approx(100)
    assert o.status is OrderStatus.CANCELLED


def test_market_buy_limited_by_cash(exchange):
    ex = exchange(cash=26.0)
    ex.on_book(make_book())
    o = ex.submit(mkt(Side.BUY, 100))
    assert o.filled_size == pytest.approx(50)
    assert ex.portfolio.cash == pytest.approx(0.0, abs=1e-9)


def test_fok_rejects_when_not_fully_fillable(exchange):
    ex = exchange()
    ex.on_book(make_book())
    o = ex.submit(lim(Side.BUY, 150, 0.52, TimeInForce.FOK))
    assert o.status is OrderStatus.CANCELLED and o.filled_size == 0
    o2 = ex.submit(lim(Side.BUY, 100, 0.52, TimeInForce.FOK))
    assert o2.status is OrderStatus.FILLED


def test_validation(exchange):
    ex = exchange()
    assert ex.submit(lim(Side.BUY, 10, 0.555)).reject_reason.startswith("price not on tick")
    assert ex.submit(lim(Side.BUY, 10, 1.0)).status is OrderStatus.REJECTED
    assert ex.submit(lim(Side.BUY, 0.5, 0.5)).status is OrderStatus.REJECTED  # below min size
    assert ex.submit(Order("other", Side.BUY, 10, 0.5)).status is OrderStatus.REJECTED
    assert ex.submit(lim(Side.SELL, 10, 0.5)).reject_reason == "insufficient shares"
    assert ex.submit(lim(Side.BUY, 5000, 0.5)).reject_reason == "insufficient cash"


def test_resting_limit_locks_cash_and_fills_as_maker(exchange):
    ex = exchange()
    ex.on_book(make_book())
    o = ex.submit(lim(Side.BUY, 100, 0.50))
    assert o.status is OrderStatus.OPEN
    assert ex.portfolio.locked_cash == pytest.approx(50)
    # next snapshot: someone offers at 0.50 -> our bid is hit for the available 60
    ex.on_book(make_book(ts=1, bids=((0.49, 100),), asks=((0.50, 60), (0.51, 100))))
    assert o.status is OrderStatus.PARTIALLY_FILLED and o.filled_size == pytest.approx(60)
    assert ex.fills[-1].liquidity is Liquidity.MAKER and ex.fills[-1].price == pytest.approx(0.50)
    assert ex.portfolio.locked_cash == pytest.approx(20)
    ex.on_book(make_book(ts=2, bids=((0.45, 100),), asks=((0.48, 500),)))
    assert o.status is OrderStatus.FILLED
    assert ex.portfolio.locked_cash == pytest.approx(0)
    assert ex.portfolio.cash == pytest.approx(1000 - 50)


def test_through_mode_requires_price_to_trade_through(exchange):
    ex = exchange(fill_mode=FillMode.THROUGH)
    ex.on_book(make_book())
    o = ex.submit(lim(Side.BUY, 10, 0.50))
    ex.on_book(make_book(ts=1, asks=((0.50, 100),)))
    assert o.filled_size == 0
    ex.on_book(make_book(ts=2, asks=((0.49, 100),)))
    assert o.status is OrderStatus.FILLED


def test_resting_sell_locks_shares_and_cancel_releases(exchange):
    ex = exchange()
    ex.on_book(make_book())
    ex.submit(mkt(Side.BUY, 50))
    o = ex.submit(lim(Side.SELL, 50, 0.60))
    assert ex.portfolio.available_shares(YES) == pytest.approx(0)
    assert ex.submit(lim(Side.SELL, 1, 0.60)).status is OrderStatus.REJECTED
    assert ex.cancel(o.order_id)
    assert ex.portfolio.available_shares(YES) == pytest.approx(50)
    assert not ex.cancel(o.order_id)


def test_latency_matches_against_book_at_arrival(exchange):
    ex = exchange(latency=1.5)
    ex.on_book(make_book(ts=0))
    o = ex.submit(mkt(Side.BUY, 10), now=0)
    assert o.status is OrderStatus.PENDING
    ex.on_book(make_book(ts=1, asks=((0.60, 100),)))  # arrives at 1.5, not yet
    assert o.status is OrderStatus.PENDING
    ex.on_book(make_book(ts=2, asks=((0.70, 100),)))  # arrival at 1.5 sees the ts=1 book
    assert o.status is OrderStatus.FILLED
    assert o.avg_fill_price == pytest.approx(0.60)


def test_taker_fees_follow_polymarket_formula(exchange):
    ex = exchange(fee_model=FeeModel(taker_rate=0.07))
    ex.on_book(make_book(asks=((0.80, 100),)))
    ex.submit(mkt(Side.BUY, 100))
    # feeRate * p * (1 - p) * shares
    assert ex.fills[-1].fee == pytest.approx(0.07 * 0.80 * 0.20 * 100)
    assert ex.portfolio.cash == pytest.approx(1000 - 80 - 1.12)


def test_resting_buy_reserves_cash_for_fees(exchange):
    ex = exchange(fee_model=FeeModel(taker_rate=0.07, maker_rate=0.07))
    ex.on_book(make_book())
    ex.submit(lim(Side.BUY, 100, 0.50))
    assert ex.portfolio.locked_cash == pytest.approx(50 + 1.75)
