import pytest

from polysim import Market, Portfolio, SimExchange, Token
from polysim_helpers import NO, YES


@pytest.fixture
def market():
    return Market("m1", "Will it happen?", (Token(YES, "Yes"), Token(NO, "No")), min_order_size=1.0)


@pytest.fixture
def exchange(market):
    def _make(cash=1000.0, **kw):
        return SimExchange(Portfolio(cash), [market], **kw)

    return _make
