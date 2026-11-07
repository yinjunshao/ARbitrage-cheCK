"""Fee models.

Polymarket charges takers ``fee = feeRate * p * (1 - p) * shares`` (in USDC,
rounded to 5 decimals), so fees peak at 50c and vanish towards 0/1. Makers pay
nothing. Example: 100 shares of a crypto market at 0.50 ->
0.07 * 0.5 * 0.5 * 100 = $1.75.

Where the rate comes from, in order of preference:

1. an explicit ``FeeModel`` passed to the exchange (a what-if override);
2. the market's own ``feeSchedule`` from the Gamma API (``Market.fee_rate``);
3. the market's category, looked up in ``CATEGORY_TAKER_RATES``.

Source for the table: docs.polymarket.com/trading/fees, read Nov 2026. Rates
change, so re-check them. Maker rebates exist but are ignored (conservative).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Liquidity

FEE_DECIMALS = 5

# Taker fee rates (fraction, not bps) per market category.
CATEGORY_TAKER_RATES: dict[str, float] = {
    "crypto": 0.07,
    "sports": 0.05,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "combos": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
}


@dataclass(frozen=True, slots=True)
class FeeModel:
    taker_rate: float = 0.0
    maker_rate: float = 0.0
    # Gamma's feeSchedule carries an "exponent"; every market we have seen uses 1,
    # which is the documented formula. Other values are applied as
    # rate * (p * (1 - p)) ** exponent, which is our assumption, not documented.
    exponent: float = 1.0

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

    def rate(self, liquidity: Liquidity) -> float:
        return self.maker_rate if liquidity is Liquidity.MAKER else self.taker_rate

    def per_share(self, price: float, liquidity: Liquidity) -> float:
        """Unrounded fee for one share; used when planning how much cash allows."""
        rate = self.rate(liquidity)
        if rate == 0:
            return 0.0
        return rate * (price * (1.0 - price)) ** self.exponent

    def fee(self, price: float, size: float, liquidity: Liquidity) -> float:
        """Fee charged for one fill, rounded to 5 decimals like Polymarket."""
        return round(self.per_share(price, liquidity) * size, FEE_DECIMALS)

    def max_fee(self, price: float, size: float) -> float:
        """Upper bound used when reserving cash for a resting order."""
        return max(
            self.fee(price, size, Liquidity.MAKER), self.fee(price, size, Liquidity.TAKER)
        )
