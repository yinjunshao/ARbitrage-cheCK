import pytest

from polysim import FeeModel, Liquidity


def test_crypto_example_from_docs():
    # 100 shares of a crypto market @ 0.50 -> $1.75
    fm = FeeModel.for_category("crypto")
    assert fm.fee(0.50, 100, Liquidity.TAKER) == pytest.approx(1.75)


def test_formula_is_p_times_one_minus_p():
    fm = FeeModel(taker_rate=0.07)
    assert fm.fee(0.80, 100, Liquidity.TAKER) == pytest.approx(0.07 * 0.80 * 0.20 * 100)
    # symmetric around 0.5, vanishes at the edges
    assert fm.fee(0.20, 100, Liquidity.TAKER) == pytest.approx(fm.fee(0.80, 100, Liquidity.TAKER))
    assert fm.fee(0.99, 100, Liquidity.TAKER) < fm.fee(0.90, 100, Liquidity.TAKER)


def test_maker_and_taker_rates_are_separate():
    fm = FeeModel(taker_rate=0.07, maker_rate=0.0)
    assert fm.fee(0.5, 100, Liquidity.MAKER) == 0.0
    assert fm.max_fee(0.5, 100) == pytest.approx(1.75)


def test_default_is_fee_free_and_unknown_category_raises():
    assert FeeModel().fee(0.5, 100, Liquidity.TAKER) == 0.0
    assert FeeModel.for_category("CRYPTO").taker_rate == 0.07
    with pytest.raises(ValueError, match="no verified fee rate"):
        FeeModel.for_category("not-a-category")


def test_all_documented_categories():
    assert FeeModel.for_category("sports").taker_rate == 0.05
    assert FeeModel.for_category("politics").taker_rate == 0.04
    assert FeeModel.for_category("geopolitics").taker_rate == 0.0


def test_fee_is_rounded_to_five_decimals():
    fm = FeeModel(taker_rate=0.07)
    raw = 0.07 * 0.333 * 0.667 * 3
    assert fm.fee(0.333, 3, Liquidity.TAKER) == round(raw, 5) != raw


def test_gamma_fee_schedule_is_parsed():
    from polysim.data import parse_gamma_market

    raw = {
        "conditionId": "0x1", "question": "q", "outcomes": '["Yes","No"]', "clobTokenIds": '["1","2"]',
        "feesEnabled": True, "feeType": "politics_fees",
        "feeSchedule": {"exponent": 1, "rate": 0.04, "takerOnly": True, "rebateRate": 0.25},
    }
    m = parse_gamma_market(raw)
    assert (m.category, m.fee_rate, m.fee_exponent, m.fee_taker_only) == ("politics", 0.04, 1.0, True)
    assert parse_gamma_market({**raw, "feesEnabled": False}).fee_rate == 0.0
    no_info = {k: v for k, v in raw.items() if k not in ("feesEnabled", "feeType", "feeSchedule")}
    assert parse_gamma_market(no_info).fee_rate is None


def test_exchange_uses_each_markets_own_rate():
    from polysim import Market, Portfolio, SimExchange, Token

    pol = Market("p", "p?", (Token("P1", "Yes"), Token("P2", "No")), fee_rate=0.04)
    cry = Market("c", "c?", (Token("C1", "Yes"), Token("C2", "No")), category="crypto")
    free = Market("f", "f?", (Token("F1", "Yes"), Token("F2", "No")))
    ex = SimExchange(Portfolio(1000), [pol, cry, free])
    assert ex.fee_for("P1").taker_rate == 0.04
    assert ex.fee_for("C2").taker_rate == 0.07
    assert ex.fee_for("F1").taker_rate == 0.0
    # an explicit model overrides every market
    ex2 = SimExchange(Portfolio(1000), [pol], fee_model=FeeModel(taker_rate=0.01))
    assert ex2.fee_for("P1").taker_rate == 0.01
