"""Fee models.

Polymarket charges ``fee = feeRate * p * (1 - p) * shares`` (in USDC), so fees
peak at 50c and vanish towards 0/1. The fee rate depends on the market category,
e.g. 100 shares of a crypto market at 0.50 -> 0.07 * 0.5 * 0.5 * 100 = $1.75.

Only rates that have been checked against the docs are listed in
``CATEGORY_TAKER_RATES``; pass any other rate explicitly. The default
``FeeModel()`` is fee-free.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Liquidity

# Taker fee rates (fraction, not bps) per market category, verified against the docs.
CATEGORY_TAKER_RATES: dict[str, float] = {
    "crypto": 0.07,
}


@dataclass(frozen=True, slots=True)
class FeeModel:
    taker_rate: float = 0.0
    maker_rate: float = 0.0

    @classmethod
    def for_category(cls, category: str, maker_rate: float = 0.0) -> "FeeModel":
        try:
            rate = CATEGORY_TAKER_RATES[category.lower()]
        except KeyError:
            known = ", ".join(sorted(CATEGORY_TAKER_RATES))
            raise ValueError(
                f"no verified fee rate for category {category!r} (known: {known}); "
                "pass FeeModel(taker_rate=...) explicitly"
            ) from None
        return cls(taker_rate=rate, maker_rate=maker_rate)

    def fee(self, price: float, size: float, liquidity: Liquidity) -> float:
        rate = self.maker_rate if liquidity is Liquidity.MAKER else self.taker_rate
        if rate == 0:
            return 0.0
        return rate * price * (1.0 - price) * size

    def max_fee(self, price: float, size: float) -> float:
        """Upper bound used when reserving cash for a resting order."""
        return max(
            self.fee(price, size, Liquidity.MAKER), self.fee(price, size, Liquidity.TAKER)
        )
