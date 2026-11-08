"""The return calculator: fee formulas, ROI as prediction.com defines it, break-even, size curve."""
import pytest

from frame import calc
from frame.calc import Leg


def test_their_worked_example_polymarket_27_plus_novig_63():
    """Their page: 27c + 63c = 90c, 1.3c fees -> 8.7c on 90c = +9.7%. Our fee rules reproduce the shape of it."""
    legs = [Leg("polymarket", "YES", 0.27), Leg("novig", "NO", 0.63)]
    r = calc.evaluate(legs, 1)
    assert r.cost_before_fees == pytest.approx(0.90)
    # 1 contract: polymarket 0.05*0.27*0.73 = 0.0099 -> up to 0.010 ; novig 0.06*0.63*0.37 = 0.0140
    assert r.fees == pytest.approx(0.010 + 0.0139860, abs=1e-6)
    assert r.profit == pytest.approx(1 - 0.90 - r.fees)
    assert r.roi == pytest.approx(r.profit / r.cost)


def test_roi_is_profit_over_cost_and_apy_scales_with_time():
    legs = [Leg("manual", "YES", 0.45), Leg("manual", "NO", 0.50)]
    r = calc.evaluate(legs, 100, days=10)
    assert r.profit == pytest.approx(5.0) and r.roi == pytest.approx(5 / 95)
    assert r.apy_simple == pytest.approx(r.roi * 36.5)
    assert r.apy_compound > r.apy_simple


def test_kalshi_fee_rounds_up_to_the_cent_and_is_not_linear():
    f = calc.FEES["kalshi"]
    assert f.order_fee(0.5, 1) == pytest.approx(0.02)             # 0.0175 -> 0.02
    assert f.order_fee(0.5, 100) == pytest.approx(1.75)           # 1.75 exactly: no rounding up
    assert f.order_fee(0.5, 100) < 100 * f.order_fee(0.5, 1)      # why fee_usd for one contract misleads


def test_other_venue_fee_formulas():
    assert calc.FEES["opinion"].order_fee(0.5, 100) == pytest.approx(25.0)
    assert calc.FEES["prophetx"].order_fee(0.4, 100) == pytest.approx(1.2)
    assert calc.FEES["predictfun"].order_fee(0.5, 100) == pytest.approx(1.0)
    assert calc.FEES["polymarket"].order_fee(0.5, 100, 0.07) == pytest.approx(1.75)


def test_predictit_exit_fee_applies_to_profit():
    r = calc.evaluate([Leg("predictit", "YES", 0.45), Leg("manual", "NO", 0.45)], 100)
    assert r.exit_fees == pytest.approx(0.10 * 10.0) and r.profit == pytest.approx(10.0 - 1.0)


def test_no_gap_means_not_executable():
    r = calc.evaluate([Leg("kalshi", "YES", 0.52), Leg("polymarket", "NO", 0.49)], 10)
    assert not r.executable and r.profit < 0 and "No profit" in r.note


def test_break_even_and_slippage():
    legs = [Leg("manual", "YES", 0.45), Leg("manual", "NO", 0.50)]
    r = calc.evaluate(legs, 100)
    assert r.breakeven_total == pytest.approx(1.0, abs=1e-6)
    assert r.slip_per_leg == pytest.approx(0.025, abs=1e-6)       # 5c of room split over two legs


def test_resolution_risk_haircuts_only_the_profit():
    legs = [Leg("manual", "YES", 0.45), Leg("manual", "NO", 0.50)]
    r = calc.evaluate(legs, 100, resolution_risk=0.4)
    assert r.risk_adjusted_profit == pytest.approx(3.0)
    assert calc.evaluate([Leg("manual", "YES", 0.6), Leg("manual", "NO", 0.5)], 100, resolution_risk=0.4).risk_adjusted_profit < 0


def test_walk_stops_when_the_next_level_no_longer_profits():
    yes = Leg("manual", "YES", 0.40, book=[(0.40, 100), (0.44, 100), (0.50, 100)])
    no = Leg("manual", "NO", 0.50, book=[(0.50, 150), (0.60, 100)])
    fills = calc.walk([yes, no])
    assert [round(p, 2) for p, _ in fills] == [0.90, 0.94]        # 0.44 + 0.50; the next level (0.44+0.60) is a loss
    assert sum(q for _, q in fills) == pytest.approx(150.0)


def test_size_curve_with_depth_falls_as_size_grows():
    yes = Leg("manual", "YES", 0.40, book=[(0.40, 100), (0.48, 1000)])
    no = Leg("manual", "NO", 0.50, book=[(0.50, 1000)])
    curve = calc.size_curve([yes, no], [50, 100, 300])
    assert curve[0]["roi"] > curve[1]["roi"] > curve[2]["roi"] or curve[2]["profit"] < curve[1]["profit"] * 3
    assert curve[0]["profit"] == pytest.approx(50 * 0.10)


def test_a_four_outcome_set_pays_three_dollars_per_basket_not_one():
    """Buy NO on every outcome of an at-most-one set of 4: exactly 3 of the 4 'No's pay. The core's floor is 3."""
    legs = [Leg("manual", "NO", p) for p in (0.59, 0.69, 0.79, 0.85)]        # total 2.92
    wrong = calc.evaluate(legs, 100)                                         # assuming a $1 payout: a loss
    right = calc.evaluate(legs, 100, payout_per_pair=3.0)
    assert wrong.profit < 0 < right.profit
    assert right.profit == pytest.approx(100 * (3.0 - 2.92))
    curve = calc.size_curve(legs, [10, 100], payout=3.0)
    assert all(c["profit"] > 0 for c in curve)


def test_capital_buys_fewer_contracts_than_price_alone_because_of_fees():
    legs = [Leg("kalshi", "YES", 0.45), Leg("polymarket", "NO", 0.45)]
    q = calc.capital_to_contracts(legs, 1000)
    assert q < 1000 / 0.90
    r = calc.evaluate(legs, q)
    assert r.cost == pytest.approx(1000, abs=0.5)
