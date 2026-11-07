"""Read-only Kalshi adapter. OFF unless the person running the tool opted in.

Why opt-in: Kalshi's Developer Agreement (v1.1, section 3) limits API use to facilitating a
member's own trading on Kalshi, and bars collecting, caching, aggregating or storing API
data for other uses and sharing it with third parties. So this adapter is only built when
the app is started with --kalshi-member on the member's own computer; `ReadOnlyClient`
refuses every Kalshi request otherwise; Kalshi data lives in memory for 60 seconds and is
never written to disk by this project.

Public market-data endpoints need no API key. We never use trading, portfolio or account
endpoints (they are not on the allowlist).

Kalshi order books list BIDS only, for YES and for NO. A bid for NO at price q is an offer
to sell YES at 1 - q, so we build the YES asks from the NO bids and vice versa.
Response shapes follow docs.kalshi.com; untested against the live API from the build
sandbox. Both the newer dollar-string fields and the older cent fields are accepted.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .compliance import FetchError, ReadOnlyClient
from .fees import KALSHI_FEE
from .models import Book, Level, Market
from .relations import similarity

BASE = "https://api.elections.kalshi.com/trade-api/v2"
OPEN_STATUSES = {"active", "open"}
_URL_RE = re.compile(r"^https?://(?:www\.)?kalshi\.com/(.+)$", re.I)


def _dollars(raw: dict, base: str) -> float | None:
    """A price field in dollars: '<base>_dollars' (string) or '<base>' (integer cents)."""
    v = raw.get(f"{base}_dollars")
    if v not in (None, ""):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    v = raw.get(base)
    try:
        return float(v) / 100 if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _time(raw: dict, *keys: str) -> datetime | None:
    for k in keys:
        v = raw.get(k)
        if v:
            try:
                dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def market_from(raw: dict, event: dict | None = None) -> Market | None:
    """A Market with EMPTY books from a Kalshi market record, or None if it is not a plain open binary."""
    if raw.get("market_type") not in (None, "binary"):
        return None
    if str(raw.get("status", "open")).lower() not in OPEN_STATUSES:
        return None
    end = _time(raw, "close_time", "expiration_time", "expected_expiration_time")
    ticker = raw.get("ticker")
    if not ticker or end is None:
        return None
    title = raw.get("title") or ""
    sub = raw.get("yes_sub_title") or raw.get("subtitle") or ""
    ev_title = (event or {}).get("title") or ""
    question = f"{ev_title}: {sub}" if (ev_title and sub and sub not in title) else (title or ev_title)
    rules = "\n".join(x for x in (raw.get("rules_primary"), raw.get("rules_secondary")) if x).strip() or question
    bid, ask = _dollars(raw, "yes_bid"), _dollars(raw, "yes_ask")
    prob = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else _dollars(raw, "last_price")
    ev_ticker = raw.get("event_ticker") or (event or {}).get("event_ticker") or ""
    return Market(
        id=ticker, question=question, rules=rules, end=end, yes=Book(), no=Book(), venue="Kalshi",
        url=f"https://kalshi.com/markets/{ev_ticker.lower()}" if ev_ticker else "",
        meta={"event_slug": ev_ticker, "exclusive": bool((event or {}).get("mutually_exclusive")),
              "exhaustive": False},
        fee=KALSHI_FEE, tradeable=True, prob=prob, group=ev_ticker)


def parse_orderbook(raw: dict) -> tuple[Book, Book]:
    """(YES book, NO book) from Kalshi's bids-only order book."""
    ob = raw.get("orderbook_fp") or raw.get("orderbook") or {}

    def side(dollar_key: str, cent_key: str) -> list[tuple[float, float]]:
        if ob.get(dollar_key) is not None:
            return [(float(p), float(q)) for p, q in ob[dollar_key]]
        return [(float(p) / 100, float(q)) for p, q in (ob.get(cent_key) or [])]

    yes_bids, no_bids = side("yes_dollars", "yes"), side("no_dollars", "no")
    yes = Book(bids=[Level(p, q) for p, q in yes_bids],
               asks=[Level(round(1 - p, 4), q) for p, q in no_bids])
    no = Book(bids=[Level(p, q) for p, q in no_bids],
              asks=[Level(round(1 - p, 4), q) for p, q in yes_bids])
    return yes, no


class Kalshi:
    name = "Kalshi"
    tradeable = True

    def __init__(self, client: ReadOnlyClient):
        self.client = client

    def parse_url(self, text: str) -> dict | None:
        m = _URL_RE.match(text.strip())
        if not m:
            return None
        segments = [s for s in re.split(r"[/?#]", m.group(1)) if s]
        return {"ticker": segments[-1].upper()} if segments else None

    def lookup(self, ref: dict) -> list[Market]:
        ticker = ref["ticker"]
        try:
            data = self.client.get(f"{BASE}/events/{ticker}", {"with_nested_markets": "true"})
            event = data.get("event") or {}
            markets = event.get("markets") or data.get("markets") or []
            return [m for m in (market_from(r, event) for r in markets) if m]
        except FetchError as err:
            if err.status != 404:
                raise
        data = self.client.get(f"{BASE}/markets/{ticker}")
        raw = data.get("market") or {}
        m = market_from(raw)
        return [m] if m else []

    def search(self, text: str, limit: int = 5) -> list[Market]:
        """Kalshi has no documented free-text search, so we read a few pages of open events
        (cached in memory for a minute) and rank them locally."""
        found: list[tuple[float, Market]] = []
        cursor = ""
        for _ in range(3):
            params = {"status": "open", "with_nested_markets": "true", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = self.client.get(f"{BASE}/events", params)
            for event in data.get("events") or []:
                for raw in event.get("markets") or []:
                    m = market_from(raw, event)
                    if m:
                        found.append((similarity(text, m.question), m))
            cursor = data.get("cursor") or ""
            if not cursor:
                break
        found.sort(key=lambda x: -x[0])
        return [m for score, m in found[:limit] if score > 0]

    def siblings(self, market: Market) -> list[Market]:
        ticker = market.meta.get("event_slug")
        if not ticker:
            return []
        return [m for m in self.lookup({"ticker": ticker}) if m.id != market.id]

    def hydrate(self, market: Market) -> Market:
        raw = self.client.get(f"{BASE}/markets/{market.id}/orderbook")
        market.yes, market.no = parse_orderbook(raw)
        return market
