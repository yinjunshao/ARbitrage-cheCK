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
        FeeModel.for_category("sports")
