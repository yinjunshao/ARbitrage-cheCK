"""Plain data types shared by every module. Standard library only.

Prices are dollars per share between 0 and 1 (a share pays $1 if it wins), so
0.01 dollars = 1 cent = 1 percentage point of probability.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .fees import NO_FEE, Fee


@dataclass(frozen=True)
class Level:
    """One rung of an order book: `size` shares available at `price` dollars."""
    price: float
    size: float


@dataclass
class Book:
    """Order book for ONE token (the YES token or the NO token of a market).

    asks = sellers. When we BUY we pay the asks, cheapest first.
    bids = buyers. We only use them for spreads and mid prices.
    """
    asks: list[Level] = field(default_factory=list)
    bids: list[Level] = field(default_factory=list)

    def __post_init__(self) -> None:
        # APIs do not guarantee ordering, so always sort: best price first.
        self.asks = sorted(self.asks, key=lambda lv: lv.price)
        self.bids = sorted(self.bids, key=lambda lv: -lv.price)

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def mid(self) -> float | None:
        """Midpoint price: what a price-only scanner would use."""
        if self.best_ask is None or self.best_bid is None:
            return None
        return (self.best_ask + self.best_bid) / 2

    @property
    def spread(self) -> float | None:
        if self.best_ask is None or self.best_bid is None:
            return None
        return self.best_ask - self.best_bid


_BY_RE = re.compile(r"\bby\s+(.+?)\??$", re.I)


@dataclass
class Market:
    """One binary (Yes/No) contract."""
    id: str
    question: str
    rules: str          # the full resolution text: this is what the LLM reads
    end: datetime       # resolution deadline, timezone-aware (UTC)
    yes: Book
    no: Book
    venue: str = "Polymarket"
    url: str = ""
    meta: dict = field(default_factory=dict)   # e.g. CLOB token ids for live data
    fee: Fee = NO_FEE                          # taker fee model for this market's venue
    tradeable: bool = True                     # False for play-money venues (Manifold): signal only
    prob: float | None = None                  # the venue's own displayed probability (for signals)
    group: str = ""                            # id of the event this market belongs to, if any

    @property
    def short(self) -> str:
        """Short label for UI, e.g. 'by December 31, 2026'."""
        m = _BY_RE.search(self.question.strip())
        return f"by {m.group(1)}" if m else self.question[:40]

    @property
    def has_book(self) -> bool:
        return bool(self.yes.asks or self.no.asks)

    @property
    def mid_yes(self) -> float | None:
        """Best available estimate of the probability: order-book mid, else the venue's own number."""
        return self.yes.mid if self.yes.mid is not None else self.prob


def question_stem(question: str) -> str:
    """The question with its deadline removed.

    'Will X happen by June 30, 2027?' -> 'will x happen'. Two markets with the same
    stem and different deadlines are candidates for a nested-deadline relation.
    """
    head = re.split(r"\s+by\s+", question.strip(), maxsplit=1, flags=re.I)[0]
    return re.sub(r"[^a-z0-9 ]+", "", head.lower()).strip()
