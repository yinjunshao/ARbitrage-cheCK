"""Fee models. Standard library only.

Why this file exists: a gap of 1 to 2 cents is common, and venue fees are often the same
size. Treating fees as one flat rate (as the first version did) is wrong across venues,
because Polymarket and Kalshi both charge takers a fee that peaks at 50 cents and shrinks
toward 0 and 100:

    fee per share = k * price * (1 - price)

Sources (read on 2026-11-07, they change, so every number can be overridden):
  * Kalshi fee schedule: k = 0.07, rounded UP to the next cent per order.
  * Polymarket fee docs (docs.polymarket.com/trading/fees): k depends on the market
    category, makers pay nothing, geopolitics is fee-free.

This tool only ever prices TAKER fills (it assumes you cross the spread), which is the
cautious choice: resting limit orders can be cheaper but may never fill.

When the category or fee of a market is unknown we assume the HIGHER fee. Overstating a
cost can only hide an opportunity; understating it can show a fake one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Fee:
    name: str = "No fee"
    k: float = 0.0            # quadratic part: k * p * (1 - p) per share
    rate: float = 0.0         # proportional part: rate * p per share (used for a flat --fee)
    cent_round: bool = False  # round the order's fee UP to the next cent

    def per_share(self, price: float) -> float:
        """Fee in dollars for buying one share at `price` (continuous, used while walking the book)."""
        return self.k * price * (1.0 - price) + self.rate * price

    def order_fee(self, fills: Iterable[tuple[float, float]]) -> float:
        """Fee in dollars for one order made of (price, shares) fills."""
        raw = sum(shares * self.per_share(price) for price, shares in fills)
        if self.cent_round and raw > 0:
            return math.ceil(raw * 100 - 1e-9) / 100
        return raw


NO_FEE = Fee()
KALSHI_FEE = Fee("Kalshi taker fee: 0.07 × price × (1 − price), rounded up to the cent", k=0.07, cent_round=True)

# Taker fee parameter k by Polymarket category (docs.polymarket.com/trading/fees, 2026-11-07).
POLYMARKET_K = {
    "crypto": 0.07, "sports": 0.05, "finance": 0.04, "politics": 0.04, "economics": 0.05,
    "culture": 0.05, "weather": 0.05, "other": 0.05, "mentions": 0.04, "tech": 0.04,
    "geopolitics": 0.0, "combos": 0.05,
}


def polymarket_fee(category: str | None = None, k: float | None = None) -> Fee:
    """Fee for a Polymarket market. Unknown category -> 'other' (0.05): the cautious guess."""
    if k is not None:
        return Fee(f"Polymarket taker fee: {k:g} × price × (1 − price)", k=k) if k > 0 else NO_FEE
    key = (category or "").strip().lower()
    known = key in POLYMARKET_K
    k = POLYMARKET_K.get(key, POLYMARKET_K["other"])
    if k == 0:
        return Fee("Polymarket: fee-free category", k=0.0)
    suffix = "" if known else " (category unknown, assumed)"
    return Fee(f"Polymarket taker fee: {k:g} × price × (1 − price){suffix}", k=k)


def flat_fee(rate: float) -> Fee:
    """A flat fraction of notional, for the --fee override and for tests."""
    return Fee(f"Flat fee: {rate:.2%} of the amount spent", rate=rate) if rate > 0 else NO_FEE
