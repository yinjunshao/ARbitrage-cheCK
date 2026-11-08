"""Return calculator for a two-leg (or n-leg) arbitrage: pure arithmetic, no network, no LLM.

Inspired by prediction.com's published method (their /v2/arb docs):
    total cost  = YES price + NO price (per contract pair)
    net profit  = $1.00 - cost - entry fees
    ROI         = net profit / cost          (they quote it "net of fees")
    fees        = per-venue formulas with rounding (below); `fee_usd` is for ONE contract, but fees are not linear,
                  so for a real size you must apply the formula to the whole order.
What we add on top of that, because a headline ROI hides the things that decide whether a gap is real:
    * depth: optional order-book levels per leg, walked cheapest-first for as long as the pair still profits,
      so the size curve shows where the return falls to zero (the core does the same walk on live books);
    * break-even: the highest total price at which the pair still pays, and how many cents of slippage you can
      absorb on each leg;
    * time: return per year (simple, as the core does it, and compounded), because 3% in 2 days and 3% in 200 days
      are different trades;
    * a resolution-risk haircut you set yourself (an arbitrage is only "risk-free" if both venues settle the same way).
The fee formulas below are the venues' published ones as listed by prediction.com (checked 2026-11-08) or, for
Polymarket and Novig, the categories and coefficients the core uses. They are shown to the user; the core's own
numbers (frame/adapter.py) remain the authority for anything priced from live books.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def _ceil(x: float, step: float) -> float:
    return math.ceil(x / step - 1e-9) * step if x > 0 else 0.0


@dataclass(frozen=True)
class FeeRule:
    key: str
    label: str
    formula: str
    note: str = ""
    param: float | None = None       # the venue's own coefficient, if it has one
    param_name: str = ""

    def order_fee(self, price: float, qty: float, param: float | None = None) -> float:
        """Taker fee in dollars for buying `qty` contracts at `price` (0-1) in one order."""
        p, q = price, qty
        k = self.param if param is None else param
        if self.key == "kalshi":
            return _ceil(0.07 * q * p * (1 - p), 0.01)
        if self.key in ("polymarket", "polymarket_us"):
            step = 0.001 if self.key == "polymarket" else 0.01
            return _ceil((k or 0) * q * p * (1 - p), step)
        if self.key == "novig":
            return (k or 0.06) * q * p * (1 - p)             # charged in-play; always applied here (cautious)
        if self.key == "opinion":
            return _ceil(q * p * (1 - p), 0.01)
        if self.key == "predictfun":
            return _ceil(((k or 200) / 10000) * q * p, 0.01)
        if self.key == "prophetx":
            return 0.02 * q * (1 - p)
        return 0.0                                            # predictit: no entry fee (see exit_fee), manual: none


FEES: dict[str, FeeRule] = {r.key: r for r in [
    FeeRule("kalshi", "Kalshi", "0.07 × Q × P × (1 − P), up to the cent"),
    FeeRule("polymarket", "Polymarket", "k × Q × P × (1 − P), up to a tenth of a cent",
            "k depends on the category (crypto 0.07, sports/economics/culture/weather/other 0.05, finance/politics/"
            "mentions/tech 0.04, geopolitics 0); their docs default to 0.05", 0.05, "k"),
    FeeRule("polymarket_us", "Polymarket US", "Θ × Q × P × (1 − P), up to the cent", "Θ = the market's coefficient, default 0.06",
            0.06, "Θ"),
    FeeRule("novig", "Novig", "0.06 × Q × P × (1 − P)", "Novig charges it in-play; applied always here (cautious)", 0.06, "coefficient"),
    FeeRule("opinion", "Opinion", "Q × P × (1 − P), up to the cent"),
    FeeRule("predictfun", "Predict.fun", "(bps / 10,000) × Q × P, up to the cent", "bps default 200", 200, "bps"),
    FeeRule("prophetx", "ProphetX", "0.02 × Q × (1 − P)"),
    FeeRule("predictit", "PredictIt", "no entry fee", "a 10% fee on profit when you withdraw (modelled as an exit fee)"),
    FeeRule("manual", "Other / no fee", "none"),
]}
POLYMARKET_K = {"crypto": 0.07, "sports": 0.05, "finance": 0.04, "politics": 0.04, "economics": 0.05, "culture": 0.05,
                "weather": 0.05, "other": 0.05, "mentions": 0.04, "tech": 0.04, "geopolitics": 0.0}
PREDICTIT_EXIT = 0.10


@dataclass
class Leg:
    venue: str                       # key of FEES
    side: str = "YES"                # YES or NO (display only: the price is what you pay for that side)
    price: float = 0.5               # dollars per contract, 0-1
    param: float | None = None       # venue coefficient override
    book: list[tuple[float, float]] = field(default_factory=list)   # optional ask levels (price, contracts), best first


@dataclass
class Result:
    contracts: float
    legs: list[dict]
    cost_before_fees: float
    fees: float
    exit_fees: float
    cost: float
    payout: float
    profit: float
    roi: float
    days: float | None
    apy_simple: float | None
    apy_compound: float | None
    breakeven_total: float           # highest total price per pair that still pays (at this size, same fees)
    slip_per_leg: float              # cents of slippage each leg can absorb before profit is zero
    risk_adjusted_profit: float
    executable: bool
    note: str = ""


def _fees(legs: list[Leg], qty: float) -> tuple[list[float], float]:
    per = [FEES.get(l.venue, FEES["manual"]).order_fee(l.price, qty, l.param) for l in legs]
    exit_fee = 0.0
    return per, exit_fee


def evaluate(legs: list[Leg], contracts: float, payout_per_pair: float = 1.0, days: float | None = None,
             resolution_risk: float = 0.0) -> Result:
    """Return for buying `contracts` pairs (one of each leg) at the stated prices. resolution_risk = the fraction of
    the profit you want to hold back for the chance the venues settle differently (0-1)."""
    q = max(0.0, contracts)
    per_fee, _ = _fees(legs, q)
    before = q * sum(l.price for l in legs)
    fee = sum(per_fee)
    exit_fee = 0.0
    payout = q * payout_per_pair
    for l in legs:                                             # PredictIt: 10% of profit on withdrawal (worst case)
        if l.venue == "predictit" and q:
            exit_fee += PREDICTIT_EXIT * max(0.0, payout - before)
    cost = before + fee + exit_fee
    profit = payout - cost
    roi = profit / cost if cost > 0 else 0.0
    apy_s = roi * 365 / days if days and days > 0 else None
    apy_c = ((1 + roi) ** (365 / days) - 1) if days and days > 0 and roi > -1 and roi < 5 else None
    # break-even: scale every leg's price by x until profit is zero (fees follow the new prices)
    lo, hi = 0.0, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        test = [Leg(l.venue, l.side, min(0.999, l.price * mid), l.param) for l in legs]
        c = q * sum(t.price for t in test) + sum(_fees(test, q)[0])
        if payout - c > 0:
            lo = mid
        else:
            hi = mid
    total_be = sum(min(0.999, l.price * lo) for l in legs)
    slip = max(0.0, (total_be - sum(l.price for l in legs)) / max(1, len(legs)))
    return Result(
        contracts=q,
        legs=[{"venue": FEES.get(l.venue, FEES["manual"]).label, "side": l.side, "price": l.price, "fee": f,
               "formula": FEES.get(l.venue, FEES["manual"]).formula} for l, f in zip(legs, per_fee)],
        cost_before_fees=before, fees=fee, exit_fees=exit_fee, cost=cost, payout=payout, profit=profit, roi=roi,
        days=days, apy_simple=apy_s, apy_compound=apy_c, breakeven_total=total_be, slip_per_leg=slip,
        risk_adjusted_profit=profit - max(0.0, profit) * resolution_risk,
        executable=profit > 0 and q > 0,
        note="" if profit > 0 else "No profit at these prices and fees.")


def walk(legs: list[Leg], payout_per_pair: float = 1.0) -> list[tuple[float, float]]:
    """Fills (pair price, contracts) buying the pair level by level from each leg's `book`, cheapest first, for as
    long as one more pair still profits after fees (marginal check on a single contract)."""
    books = [list(l.book) for l in legs]
    if any(not b for b in books):
        return []
    idx, rem, out = [0] * len(legs), [b[0][1] for b in books], []
    while all(idx[k] < len(books[k]) for k in range(len(legs))):
        prices = [books[k][idx[k]][0] for k in range(len(legs))]
        one = [Leg(l.venue, l.side, p, l.param) for l, p in zip(legs, prices)]
        if payout_per_pair - sum(prices) - sum(_fees(one, 1.0)[0]) <= 0:
            break
        take = min(rem)
        if take > 1e-9:
            out.append((sum(prices), take))
        for k in range(len(legs)):
            rem[k] -= take
            if rem[k] <= 1e-9:
                idx[k] += 1
                rem[k] = books[k][idx[k]][1] if idx[k] < len(books[k]) else 0.0
    return out


def size_curve(legs: list[Leg], sizes: list[float], days: float | None = None, payout: float = 1.0) -> list[dict]:
    """Profit and return at each size. With order books on every leg the price per leg rises as you walk the book;
    without, prices are taken as flat (an upper bound: real books get worse with size)."""
    has_books = all(l.book for l in legs)
    out = []
    for s in sizes:
        if not has_books:
            r = evaluate(legs, s, payout_per_pair=payout, days=days)
            out.append({"contracts": s, "profit": r.profit, "roi": r.roi, "cost": r.cost, "capped": False})
            continue
        left, avg, filled = s, [0.0] * len(legs), 0.0
        books = [list(l.book) for l in legs]
        idx, rem = [0] * len(legs), [b[0][1] for b in books]
        while left > 1e-9 and all(idx[k] < len(books[k]) for k in range(len(legs))):
            take = min(left, *rem)
            for k in range(len(legs)):
                avg[k] += books[k][idx[k]][0] * take
                rem[k] -= take
                if rem[k] <= 1e-9:
                    idx[k] += 1
                    rem[k] = books[k][idx[k]][1] if idx[k] < len(books[k]) else 0.0
            filled += take
            left -= take
        if filled <= 0:
            out.append({"contracts": s, "profit": 0.0, "roi": 0.0, "cost": 0.0, "capped": True})
            continue
        avg_legs = [Leg(l.venue, l.side, a / filled, l.param) for l, a in zip(legs, avg)]
        r = evaluate(avg_legs, filled, payout_per_pair=payout, days=days)
        out.append({"contracts": filled, "profit": r.profit, "roi": r.roi, "cost": r.cost, "capped": filled < s - 1e-9})
    return out


def capital_to_contracts(legs: list[Leg], capital: float) -> float:
    """Contracts (pairs) that `capital` dollars buy including fees, by bisection (fees depend on size)."""
    lo, hi = 0.0, capital / max(1e-9, sum(l.price for l in legs)) + 1
    for _ in range(60):
        mid = (lo + hi) / 2
        c = mid * sum(l.price for l in legs) + sum(_fees(legs, mid)[0])
        if c <= capital:
            lo = mid
        else:
            hi = mid
    return lo
