"""Market-data events consumed by the backtest engine, plus JSON (de)serialisation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .models import OrderBook, PriceLevel


@dataclass(frozen=True, slots=True)
class BookEvent:
    """A full L2 snapshot for one token."""

    ts: float
    book: OrderBook

    @property
    def token_id(self) -> str:
        return self.book.token_id


@dataclass(frozen=True, slots=True)
class PriceEvent:
    """A price observation only (e.g. from /prices-history). The engine turns it
    into a synthetic book using its :class:`~polysim.engine.BookSynthesizer`."""

    ts: float
    token_id: str
    price: float


@dataclass(frozen=True, slots=True)
class ResolutionEvent:
    """Market resolved: `payouts` maps token_id -> USDC paid per share (1/0, or 0.5 for splits)."""

    ts: float
    market_id: str
    payouts: dict[str, float]


Event = Union[BookEvent, PriceEvent, ResolutionEvent]


def event_to_dict(ev: Event) -> dict:
    if isinstance(ev, BookEvent):
        b = ev.book
        return {
            "type": "book",
            "ts": ev.ts,
            "token_id": b.token_id,
            "bids": [[lv.price, lv.size] for lv in b.bids],
            "asks": [[lv.price, lv.size] for lv in b.asks],
        }
    if isinstance(ev, PriceEvent):
        return {"type": "price", "ts": ev.ts, "token_id": ev.token_id, "price": ev.price}
    if isinstance(ev, ResolutionEvent):
        return {"type": "resolution", "ts": ev.ts, "market_id": ev.market_id, "payouts": ev.payouts}
    raise TypeError(f"unknown event {ev!r}")


def event_from_dict(d: dict) -> Event:
    kind = d["type"]
    ts = float(d["ts"])
    if kind == "book":
        book = OrderBook(
            d["token_id"],
            ts,
            [PriceLevel(float(p), float(s)) for p, s in d["bids"]],
            [PriceLevel(float(p), float(s)) for p, s in d["asks"]],
        )
        return BookEvent(ts, book)
    if kind == "price":
        return PriceEvent(ts, d["token_id"], float(d["price"]))
    if kind == "resolution":
        return ResolutionEvent(ts, d["market_id"], {k: float(v) for k, v in d["payouts"].items()})
    raise ValueError(f"unknown event type {kind!r}")
