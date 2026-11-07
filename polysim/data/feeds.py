"""Event feed helpers: merging, JSONL storage and live recording."""

from __future__ import annotations

import heapq
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator

from ..events import BookEvent, Event, event_from_dict, event_to_dict
from ..models import Market, Token

if TYPE_CHECKING:
    from .polymarket import PolymarketClient


def merge_feeds(*feeds: Iterable[Event]) -> Iterator[Event]:
    """Merge already-sorted feeds into one time-ordered stream."""
    return heapq.merge(*feeds, key=lambda e: e.ts)


def write_events(path: str | Path, events: Iterable[Event]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(event_to_dict(ev)) + "\n")
            n += 1
    return n


def read_events(path: str | Path) -> Iterator[Event]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield event_from_dict(json.loads(line))


def market_to_dict(m: Market) -> dict:
    return {
        "market_id": m.market_id,
        "question": m.question,
        "slug": m.slug,
        "tokens": [[t.token_id, t.outcome] for t in m.tokens],
        "tick_size": m.tick_size,
        "min_order_size": m.min_order_size,
        "end_ts": m.end_ts,
        "category": m.category,
        "fee_rate": m.fee_rate,
        "fee_exponent": m.fee_exponent,
        "fee_taker_only": m.fee_taker_only,
    }


def market_from_dict(d: dict) -> Market:
    return Market(
        market_id=d["market_id"],
        question=d.get("question", ""),
        slug=d.get("slug", ""),
        tokens=tuple(Token(t, o) for t, o in d["tokens"]),
        tick_size=float(d.get("tick_size", 0.01)),
        min_order_size=float(d.get("min_order_size", 5)),
        end_ts=d.get("end_ts"),
        category=d.get("category", ""),
        fee_rate=d.get("fee_rate"),
        fee_exponent=float(d.get("fee_exponent", 1.0)),
        fee_taker_only=bool(d.get("fee_taker_only", True)),
    )


def save_markets(path: str | Path, markets: Iterable[Market]) -> None:
    Path(path).write_text(json.dumps([market_to_dict(m) for m in markets], indent=2))


def load_markets(path: str | Path) -> list[Market]:
    return [market_from_dict(d) for d in json.loads(Path(path).read_text())]


def record_books(
    client: "PolymarketClient",
    token_ids: list[str],
    path: str | Path,
    *,
    interval: float = 5.0,
    duration: float = 60.0,
    on_error=None,
) -> int:
    """Poll /book for each token every `interval` seconds and append snapshots to JSONL.

    Snapshots taken this way can be replayed with full depth, which is far more
    realistic than synthesizing books from /prices-history.
    """
    n = 0
    deadline = time.time() + duration
    with open(path, "a", encoding="utf-8") as fh:
        while time.time() < deadline:
            started = time.time()
            for tid in token_ids:
                try:
                    book = client.get_order_book(tid)
                except Exception as exc:  # network blips should not kill a long recording
                    if on_error:
                        on_error(tid, exc)
                    continue
                book.token_id = book.token_id or tid
                book.ts = started
                fh.write(json.dumps(event_to_dict(BookEvent(started, book))) + "\n")
                n += 1
            fh.flush()
            time.sleep(max(0.0, interval - (time.time() - started)))
    return n
