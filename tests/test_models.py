import pytest

from polysim import OrderBook, PriceLevel
from polysim.models import is_on_tick


def test_book_sorts_and_drops_empty_levels():
    b = OrderBook("t", 0, [PriceLevel(0.4, 1), PriceLevel(0.45, 2), PriceLevel(0.44, 0)],
                  [PriceLevel(0.6, 1), PriceLevel(0.55, 3)])
    assert [lv.price for lv in b.bids] == [0.45, 0.4]
    assert [lv.price for lv in b.asks] == [0.55, 0.6]
    assert b.mid == pytest.approx(0.5)
    assert b.spread == pytest.approx(0.10)


def test_from_mid_builds_symmetric_book_inside_bounds():
    b = OrderBook.from_mid("t", 0, 0.5, spread=0.02, levels=3, tick=0.01)
    assert b.best_bid == pytest.approx(0.49) and b.best_ask == pytest.approx(0.51)
    edge = OrderBook.from_mid("t", 0, 0.015, spread=0.02, levels=5, tick=0.01)
    assert all(0 < lv.price < 1 for lv in edge.bids + edge.asks)
    assert edge.best_bid < edge.best_ask


def test_is_on_tick():
    assert is_on_tick(0.53, 0.01)
    assert not is_on_tick(0.535, 0.01)
    assert is_on_tick(0.535, 0.001)
