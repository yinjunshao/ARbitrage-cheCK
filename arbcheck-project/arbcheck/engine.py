"""Deterministic arbitrage maths. No LLM, no network: payoffs, order books, costs.

The core idea
-------------
Some binary contracts are linked by a logical STRUCTURE. A structure rules out some
combinations of outcomes. We call each remaining combination a "world":

    (A, B) = (1, 1) means both resolve Yes, (0, 1) means only B resolves Yes, ...

Example: "happens by June" (A) implies "happens by December" (B), so the world
(A=Yes, B=No) is impossible and only (0,0), (0,1), (1,1) remain.

We buy YES and/or NO shares of the markets (one share of each chosen leg: a "basket").
The basket's FLOOR is its smallest payout over the remaining worlds. If the floor is
larger than what the basket costs, the profit is locked in whatever happens.

One idea covers every arbitrage this tool looks for, with no special cases:

  complement      one market: YES + NO always pays $1, so buy both if they cost less
  pair relations  a_implies_b, b_implies_a, mutually_exclusive, exhaustive, equivalent
                  (nested deadlines, price thresholds, and the SAME event on two venues)
  at_most_one     N outcomes, never two Yes: buying NO on all of them pays at least N-1
  partition       N outcomes, exactly one Yes: buy all the Yes, or all the No

Every claim "this is guaranteed" is checked by enumerating the worlds, so it is easy to
unit-test. Prices come from real order books: we walk the book level by level, so thin
books are priced honestly, and each leg pays ITS OWN venue's fee.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations, product
from typing import Sequence

from .fees import Fee, flat_fee
from .models import Book, Level, Market

# Market labels A..Z, then AA..BZ: enough for the largest multi-outcome events we accept.
LABELS = [chr(65 + i) for i in range(26)] + [a + b for a in "AB" for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]

# Worlds (a, b) that remain possible under each two-market relation.
WORLDS: dict[str, list[tuple[int, int]]] = {
    "a_implies_b": [(0, 0), (0, 1), (1, 1)],       # A true forces B true
    "b_implies_a": [(0, 0), (1, 0), (1, 1)],
    "mutually_exclusive": [(0, 0), (0, 1), (1, 0)],  # never both Yes
    "exhaustive": [(0, 1), (1, 0), (1, 1)],          # never both No
    "equivalent": [(0, 0), (1, 1)],                  # always the same answer
}
PAIR_KINDS = tuple(WORLDS)
SET_KINDS = ("at_most_one", "partition")
MAX_FULL_ENUMERATION = 6      # above this many markets we only try pair and uniform baskets

Leg = tuple[str, str]  # ("A" | "B" | ..., "YES" | "NO")


def worlds_for(kind: str, n: int) -> list[tuple[int, ...]] | None:
    """The outcome combinations that stay possible, or None if this kind has no structure."""
    if n == 2 and kind in WORLDS:
        return list(WORLDS[kind])
    if n == 1 and kind == "complement":
        return [(0,), (1,)]
    if n >= 2 and kind == "partition":
        return [tuple(int(i == k) for i in range(n)) for k in range(n)]
    if n >= 2 and kind == "at_most_one":
        return [tuple([0] * n)] + [tuple(int(i == k) for i in range(n)) for k in range(n)]
    return None


def leg_payout(leg: Leg, world: tuple[int, ...]) -> int:
    """Dollars one share of this leg pays in this world."""
    outcome = world[LABELS.index(leg[0])]
    return outcome if leg[1] == "YES" else 1 - outcome


def basket_floor(legs: Sequence[Leg], worlds: Sequence[tuple[int, ...]]) -> int:
    """Smallest total payout of the basket over all still-possible worlds."""
    return min(sum(leg_payout(leg, w) for leg in legs) for w in worlds)


def candidate_baskets(n: int) -> list[tuple[Leg, ...]]:
    """Every basket worth testing for n markets.

    n = 1: buy both sides of the one market (the 'complement' arbitrage).
    n <= 6: every choice of none / YES / NO per market (3^n - 1 baskets, at most 728).
    larger: the two uniform baskets plus every two-leg basket, which keeps big
            multi-outcome events fast while still covering the standard arbitrages.
    """
    if n == 1:
        return [(("A", "YES"), ("A", "NO"))]
    out: list[tuple[Leg, ...]] = []
    if n <= MAX_FULL_ENUMERATION:
        for combo in product((0, 1, 2), repeat=n):
            legs = tuple((LABELS[i], "YES" if c == 1 else "NO") for i, c in enumerate(combo) if c)
            if legs:
                out.append(legs)
        return out
    out.append(tuple((LABELS[i], "YES") for i in range(n)))
    out.append(tuple((LABELS[i], "NO") for i in range(n)))
    for i, j in combinations(range(n), 2):
        for si, sj in product(("YES", "NO"), repeat=2):
            out.append(((LABELS[i], si), (LABELS[j], sj)))
    return out


def leg_book(markets: Sequence[Market], leg: Leg) -> Book:
    """The order book we would buy this leg from. Play-money markets have no usable book."""
    m = markets[LABELS.index(leg[0])]
    if not m.tradeable:
        return Book()
    return m.yes if leg[1] == "YES" else m.no


def leg_fee(markets: Sequence[Market], leg: Leg, override: Fee | None) -> Fee:
    return override if override is not None else markets[LABELS.index(leg[0])].fee


@dataclass
class Fill:
    """`shares` of the whole basket bought at these per-leg prices (dollars each)."""
    prices: tuple[float, ...]
    shares: float


@dataclass
class ArbResult:
    kind: str
    legs: tuple[Leg, ...] | None = None
    floor: float = 0.0          # guaranteed payout per basket share, dollars
    naive_edge: float = 0.0     # per share, from mid prices (what a price-only scanner sees)
    top_edge: float = 0.0       # per share at the best asks, before fees
    avg_edge: float = 0.0       # per share, averaged over the real fills, after fees
    shares: float = 0.0
    cost: float = 0.0           # dollars, including fees
    fee_cost: float = 0.0       # dollars of fees inside `cost`
    net_profit: float = 0.0     # dollars, guaranteed if the structure holds
    ret: float = 0.0            # net_profit / cost
    ann_ret: float = 0.0        # simple annualised return (ret * 365 / days)
    days: float = 1.0           # days until the latest contract resolves
    fees: list[Fee] = field(default_factory=list)   # one fee model per leg, aligned with `legs`
    fills: list[Fill] = field(default_factory=list)
    scenarios: list[dict] = field(default_factory=list)

    @property
    def executable(self) -> bool:
        return self.shares > 1e-9 and self.net_profit > 0


def unit_cost(prices: Sequence[float], fees: Sequence[Fee]) -> float:
    """Dollars to buy one basket share at these leg prices, fees included."""
    return sum(p + f.per_share(p) for p, f in zip(prices, fees))


def walk(asks: Sequence[Sequence[Level]], floor: float, fees: Sequence[Fee]) -> list[Fill]:
    """Buy the basket level by level, cheapest first, for as long as it still profits.

    Each step buys the smallest size left across the legs' current levels. Because levels
    are sorted by price the marginal edge only falls, so stopping at the first non-positive
    edge is optimal. This is how thin books are priced honestly.
    """
    n = len(asks)
    if n == 0 or any(not a for a in asks):
        return []
    idx = [0] * n
    rem = [a[0].size for a in asks]
    fills: list[Fill] = []
    while all(idx[k] < len(asks[k]) for k in range(n)):
        prices = tuple(asks[k][idx[k]].price for k in range(n))
        if floor - unit_cost(prices, fees) <= 0:
            break
        take = min(rem)
        if take > 1e-9:
            fills.append(Fill(prices, take))
        for k in range(n):
            rem[k] -= take
            if rem[k] <= 1e-9:
                idx[k] += 1
                rem[k] = asks[k][idx[k]].size if idx[k] < len(asks[k]) else 0.0
    return fills


def fee_total(fills: Sequence[Fill], fees: Sequence[Fee]) -> float:
    """Total fees in dollars: each leg is one order per venue, rounded the way that venue rounds."""
    return sum(fee.order_fee([(f.prices[k], f.shares) for f in fills]) for k, fee in enumerate(fees))


def totals(fills: Sequence[Fill], floor: float, fees: Sequence[Fee]) -> tuple[float, float, float]:
    """(shares, cost incl. fees, guaranteed profit) for a list of fills."""
    shares = sum(f.shares for f in fills)
    cost = sum(f.shares * sum(f.prices) for f in fills) + fee_total(fills, fees)
    return shares, cost, shares * floor - cost


def clip_to_budget(fills: Sequence[Fill], budget: float, fees: Sequence[Fee]) -> list[Fill]:
    """Keep only the first `budget` dollars of fills (the exposure cap)."""
    out: list[Fill] = []
    spent = 0.0
    for f in fills:
        unit = unit_cost(f.prices, fees)
        room = budget - spent
        if room <= 1e-9:
            break
        take = min(f.shares, room / unit)
        out.append(Fill(f.prices, take))
        spent += take * unit
        if take < f.shares - 1e-9:
            break
    return out


def avg_edges(fills: Sequence[Fill], floor: float, fees: Sequence[Fee]) -> tuple[float, float]:
    """(average edge before fees, average edge after fees) per share over a list of fills."""
    n = sum(f.shares for f in fills)
    if n <= 0:
        return 0.0, 0.0
    gross = sum(f.shares * (floor - sum(f.prices)) for f in fills) / n
    net = sum(f.shares * (floor - unit_cost(f.prices, fees)) for f in fills) / n
    return gross, net


def _fill_result(res: ArbResult, fills: list[Fill], worlds, legs, top_cost: float) -> None:
    shares, cost, profit = totals(fills, res.floor, res.fees)
    res.fills, res.shares, res.cost, res.net_profit = fills, shares, cost, profit
    res.fee_cost = fee_total(fills, res.fees)
    if shares > 1e-9 and cost > 0:
        res.ret = profit / cost
        res.ann_ret = res.ret * 365 / res.days
        res.avg_edge = profit / shares
        unit = cost / shares
    else:
        res.avg_edge = res.top_edge
        unit = top_cost
    # One row per still-possible world: what the basket pays and the profit per share.
    res.scenarios = [
        {"world": w, "payout": sum(leg_payout(leg, w) for leg in legs),
         "profit": sum(leg_payout(leg, w) for leg in legs) - unit}
        for w in worlds
    ]


def evaluate_structure(markets: Sequence[Market], kind: str, now: datetime,
                       fee_rate: float = 0.0) -> ArbResult:
    """Find the best basket for this structure and price it honestly.

    `naive_edge` is deliberately what a price-only scanner would report (mid prices, no
    depth, no fees) so the UI can show exactly where the apparent profit disappears.
    `fee_rate` > 0 replaces every venue's own fee with a flat fraction of notional.
    """
    n = len(markets)
    if n > len(LABELS):
        return ArbResult(kind=kind)     # absurdly large event: refuse rather than guess
    days = max((max(m.end for m in markets) - now).total_seconds() / 86400, 1.0)
    res = ArbResult(kind=kind, days=days)
    worlds = worlds_for(kind, n)
    if not worlds:
        return res                      # 'none': no logical link, nothing to price
    override = flat_fee(fee_rate) if fee_rate > 0 else None

    best = None
    best_score = float("-inf")
    naive_best = float("-inf")
    for legs in candidate_baskets(n):
        floor = basket_floor(legs, worlds)
        if floor <= 0:
            continue                    # this basket can lose everything: not an arbitrage
        books = [leg_book(markets, leg) for leg in legs]
        fees = [leg_fee(markets, leg, override) for leg in legs]
        if all(b.mid is not None for b in books):
            naive_best = max(naive_best, floor - sum(b.mid for b in books))
        if all(b.best_ask is not None for b in books):
            asks = [b.best_ask for b in books]
            score = floor - unit_cost(asks, fees)           # select on the edge AFTER fees
            if score > best_score:
                best_score, best = score, (legs, floor, books, fees, asks)

    res.naive_edge = naive_best if naive_best > float("-inf") else 0.0
    if best is None:
        return res
    legs, floor, books, fees, asks = best
    res.legs, res.floor, res.fees = legs, floor, list(fees)
    res.top_edge = floor - sum(asks)
    fills = walk([b.asks for b in books], floor, fees)
    _fill_result(res, fills, worlds, legs, sum(asks) + sum(f.per_share(p) for f, p in zip(fees, asks)))
    return res


def evaluate_pair(a: Market, b: Market, kind: str, now: datetime, fee_rate: float = 0.0) -> ArbResult:
    """Two-market convenience wrapper around `evaluate_structure`."""
    return evaluate_structure([a, b], kind, now, fee_rate)
