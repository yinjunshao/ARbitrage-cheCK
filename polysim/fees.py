"""Fee models.

Polymarket's fee schedule charges ``rate * min(p, 1 - p) * shares`` so fees are
largest at 50c and vanish near 0/1. Most markets currently have a 0 bps rate;
make it configurable so fee-bearing markets can be simulated too.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Liquidity


@dataclass(frozen=True, slots=True)
class FeeModel:
    taker_bps: float = 0.0
    maker_bps: float = 0.0

    def fee(self, price: float, size: float, liquidity: Liquidity) -> float:
        bps = self.maker_bps if liquidity is Liquidity.MAKER else self.taker_bps
        if bps == 0:
            return 0.0
        return bps / 1e4 * min(price, 1.0 - price) * size

    def max_fee(self, price: float, size: float) -> float:
        """Upper bound used when reserving cash for a resting order."""
        return max(
            self.fee(price, size, Liquidity.MAKER), self.fee(price, size, Liquidity.TAKER)
        )
