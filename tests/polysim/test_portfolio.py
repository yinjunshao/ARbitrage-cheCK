import pytest

from polysim import Fill, InsufficientFunds, Liquidity, Portfolio, Side


def fill(side, price, size, fee=0.0, token="t"):
    return Fill("o", token, side, price, size, fee, 0.0, Liquidity.TAKER)


def test_average_price_and_realized_pnl():
    pf = Portfolio(1000)
    pf.apply_fill(fill(Side.BUY, 0.40, 100))
    pf.apply_fill(fill(Side.BUY, 0.60, 100))
    assert pf.position("t").avg_price == pytest.approx(0.50)
    pf.apply_fill(fill(Side.SELL, 0.70, 50, fee=0.1))
    assert pf.position("t").realized_pnl == pytest.approx(10.0)
    assert pf.realized_pnl == pytest.approx(9.9)
    assert pf.cash == pytest.approx(1000 - 100 + 35 - 0.1)
    assert pf.equity({"t": 0.70}) == pytest.approx(pf.cash + 150 * 0.70)


def test_settlement_pays_one_per_winning_share():
    pf = Portfolio(100)
    pf.apply_fill(fill(Side.BUY, 0.25, 100, token="yes"))
    pf.apply_fill(fill(Side.BUY, 0.70, 10, token="no"))
    assert pf.settle("yes", 1.0) == pytest.approx(100)
    assert pf.settle("no", 0.0) == 0.0
    assert pf.cash == pytest.approx(100 - 25 - 7 + 100)
    assert pf.realized_pnl == pytest.approx(75 - 7)
    assert pf.qty("yes") == 0


def test_cannot_oversell_or_overlock():
    pf = Portfolio(10)
    with pytest.raises(InsufficientFunds):
        pf.apply_fill(fill(Side.SELL, 0.5, 1))
    with pytest.raises(InsufficientFunds):
        pf.lock_cash(11)
    pf.lock_cash(6)
    assert pf.available_cash == pytest.approx(4)
