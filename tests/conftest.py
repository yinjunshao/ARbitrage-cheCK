import pytest

from polysim import FeeModel, Market, OrderBook, Portfolio, PriceLevel, SimExchange, Token

YES, NO = "T-YES", "T-NO"


@pytest.fixture
def market():
    return Market("m1", "Will it happen?", (Token(YES, "Yes"), Token(NO, "No")), min_order_size=1.0)


def make_book(ts=0.0, bids=((0.48, 100), (0.47, 200)), asks=((0.52, 100), (0.53, 200)), token=YES):
    return OrderBook(token, ts, [PriceLevel(p, s) for p, s in bids], [PriceLevel(p, s) for p, s in asks])


@pytest.fixture
def exchange(market):
    def _make(cash=1000.0, **kw):
        return SimExchange(Portfolio(cash), [market], **kw)

    return _make
