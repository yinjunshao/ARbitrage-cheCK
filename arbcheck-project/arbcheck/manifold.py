"""Read-only Manifold adapter. PLAY MONEY: a signal, never a leg of a trade.

Manifold trades in mana, which has no cash value, so an "arbitrage" involving Manifold
cannot be traded for real money. We still read its prices, because a big gap between a
Manifold price and a real-money price is useful information (a stale price, or two questions
that are not really the same). Every Manifold market is built with tradeable=False, which
the engine treats as "no order book": it can never become a basket leg.

Terms (docs.manifold.markets/api): API use for tools and integrations is permitted;
scraping other than through the API and getting around the 500 requests/minute/IP limit are
prohibited; API data may not be used to train commercial AI models without a data license.
We do none of those.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .compliance import ReadOnlyClient
from .fees import NO_FEE
from .models import Book, Market

BASE = "https://api.manifold.markets/v0"
_URL_RE = re.compile(r"^https?://(?:www\.)?manifold\.markets/([^/?#]+)/([^/?#]+)", re.I)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)


def market_from(raw: dict) -> Market | None:
    """A signal-only Market from a Manifold market record (binary, unresolved, with a probability)."""
    if raw.get("outcomeType") != "BINARY" or raw.get("isResolved"):
        return None
    prob = raw.get("probability")
    if not isinstance(prob, (int, float)) or not raw.get("id"):
        return None
    close = raw.get("closeTime")
    end = datetime.fromtimestamp(close / 1000, tz=timezone.utc) if isinstance(close, (int, float)) else FAR_FUTURE
    question = raw.get("question", "")
    rules = (raw.get("textDescription") or "").strip() or question
    return Market(id=str(raw["id"]), question=question, rules=rules, end=end, yes=Book(), no=Book(),
                  venue="Manifold", url=raw.get("url", ""), fee=NO_FEE, tradeable=False,
                  prob=float(prob), group="", meta={"play_money": True})


class Manifold:
    name = "Manifold"
    tradeable = False

    def __init__(self, client: ReadOnlyClient):
        self.client = client

    def parse_url(self, text: str) -> dict | None:
        m = _URL_RE.match(text.strip())
        return {"slug": m.group(2)} if m else None

    def lookup(self, ref: dict) -> list[Market]:
        m = market_from(self.client.get(f"{BASE}/slug/{ref['slug']}"))
        return [m] if m else []

    def search(self, text: str, limit: int = 5) -> list[Market]:
        data = self.client.get(f"{BASE}/search-markets", {
            "term": text[:200], "filter": "open", "contractType": "BINARY", "limit": limit})
        return [m for m in (market_from(r) for r in data or []) if m]

    def siblings(self, market: Market) -> list[Market]:
        return []

    def hydrate(self, market: Market) -> Market:
        return market                       # nothing to fetch: there is no order book
