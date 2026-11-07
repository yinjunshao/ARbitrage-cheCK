"""Read-only Polymarket adapter (public endpoints, no credentials, no orders).

Gamma API  -> market metadata, rule text, search   (gamma-api.polymarket.com)
CLOB API   -> order books                          (clob.polymarket.com)

All requests go through `compliance.ReadOnlyClient`, which only sends allowlisted GETs.
Response shapes follow Polymarket's public docs (docs.polymarket.com). Untested against the
live API from the build sandbox, so check them on first run; fields we cannot read are
skipped rather than guessed, and any market we cannot fully understand is dropped.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone

from .compliance import ReadOnlyClient
from .fees import NO_FEE, Fee, polymarket_fee
from .models import Book, Level, Market

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

_default_client = ReadOnlyClient()


def _get(url: str, params: dict | None = None, timeout: float = 15.0):
    """Module-level shortcut (used by the collector and by tests that patch it)."""
    return _default_client.get(url, params)


def _jsonish(value):
    """Gamma returns some list fields as JSON *strings*; decode them."""
    return json.loads(value) if isinstance(value, str) else value


def parse_book(raw: dict) -> Book:
    def level(x: dict) -> Level:
        return Level(float(x["price"]), float(x["size"]))
    return Book(asks=[level(x) for x in raw.get("asks", [])],
                bids=[level(x) for x in raw.get("bids", [])])


def parse_end(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fee_for(raw: dict, event: dict | None) -> Fee:
    """The taker fee for this market. When unsure, assume the higher fee (see fees.py)."""
    if raw.get("feesEnabled") is False:
        return NO_FEE
    schedule = raw.get("feeSchedule")
    if isinstance(schedule, dict):
        for key in ("rate", "feeRate", "takerRate"):
            try:
                k = float(schedule[key])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 < k <= 0.2:                      # anything else is not a plausible k: ignore it
                return polymarket_fee(k=k)
    category = raw.get("category") or (event or {}).get("category")
    if not category:
        tags = (event or {}).get("tags") or raw.get("tags") or []
        if tags and isinstance(tags[0], dict):
            category = tags[0].get("label") or tags[0].get("slug")
    return polymarket_fee(category)


def market_stub(raw: dict, event: dict | None = None) -> Market | None:
    """Build a Market with EMPTY books from a Gamma record (cheap: no extra requests).

    Returns None for anything that is not an open plain Yes/No market with an end date.
    """
    try:
        outcomes = _jsonish(raw["outcomes"])
        tokens = _jsonish(raw["clobTokenIds"])
        if [str(o).lower() for o in outcomes] != ["yes", "no"] or len(tokens) != 2:
            return None
        end = parse_end(raw["endDate"])
    except (KeyError, TypeError, ValueError):
        return None
    if raw.get("closed") or raw.get("active") is False:
        return None
    question = raw.get("question", "")
    rules = (raw.get("description") or "").strip()
    if raw.get("resolutionSource"):
        rules += f"\nResolution source: {raw['resolutionSource']}"
    rules = rules.strip() or question
    slug = raw.get("slug", "")
    ev_slug = (event or {}).get("slug", "")
    prob = None
    try:
        prob = float(_jsonish(raw.get("outcomePrices") or "[]")[0])
    except (TypeError, ValueError, IndexError):
        pass
    exclusive = bool((event or {}).get("negRisk") or raw.get("negRisk"))
    return Market(
        id=str(raw.get("id", slug)), question=question, rules=rules, end=end, yes=Book(), no=Book(),
        venue="Polymarket",
        url=f"https://polymarket.com/event/{ev_slug or slug}" if (ev_slug or slug) else "",
        meta={"yes_token": tokens[0], "no_token": tokens[1], "event_slug": ev_slug, "slug": slug,
              "exclusive": exclusive, "exhaustive": False},    # exhaustive is never assumed
        fee=_fee_for(raw, event), tradeable=True, prob=prob, group=ev_slug)


def stubs_from_event(event: dict) -> list[Market]:
    return [m for m in (market_stub(r, event) for r in event.get("markets") or []) if m]


_URL_RE = re.compile(r"^https?://(?:www\.)?polymarket\.com/(event|market)/([A-Za-z0-9\-_.]+)(?:/([A-Za-z0-9\-_.]+))?", re.I)


class Polymarket:
    name = "Polymarket"
    tradeable = True

    def __init__(self, client: ReadOnlyClient):
        self.client = client

    # --- input ---------------------------------------------------------------------
    def parse_url(self, text: str) -> dict | None:
        m = _URL_RE.match(text.strip())
        if not m:
            return None
        kind, first, second = m.group(1).lower(), m.group(2), m.group(3)
        return {"kind": kind, "slug": first, "market_slug": second}

    def lookup(self, ref: dict) -> list[Market]:
        """Markets for a pasted link: the whole event (so we can see its siblings)."""
        out: list[Market] = []
        if ref["kind"] == "event":
            events = self.client.get(f"{GAMMA}/events", {"slug": ref["slug"]})
            if isinstance(events, list) and events:
                out = stubs_from_event(events[0])
        if not out:
            slug = ref.get("market_slug") or ref["slug"]
            raw = self.client.get(f"{GAMMA}/markets", {"slug": slug})
            if isinstance(raw, list):
                out = [m for m in (market_stub(r) for r in raw) if m]
        # `linked` = the market(s) the person actually pointed at; the rest are its siblings.
        pointed = ref.get("market_slug") or (ref["slug"] if ref["kind"] == "market" else None)
        for m in out:
            m.meta["linked"] = (pointed is None) or m.meta.get("slug") == pointed
        if pointed and not any(m.meta["linked"] for m in out):
            for m in out:
                m.meta["linked"] = True
        return out

    def search(self, text: str, limit: int = 5) -> list[Market]:
        data = self.client.get(f"{GAMMA}/public-search", {"q": text[:200], "limit_per_type": limit})
        out: list[Market] = []
        for event in (data or {}).get("events") or []:
            out.extend(stubs_from_event(event))
        return out

    def siblings(self, market: Market) -> list[Market]:
        slug = market.meta.get("event_slug")
        if not slug:
            return []
        return [m for m in self.lookup({"kind": "event", "slug": slug}) if m.id != market.id]

    def hydrate(self, market: Market) -> Market:
        """Fetch live order books for both tokens of a market (2 requests)."""
        market.yes = parse_book(self.client.get(f"{CLOB}/book", {"token_id": market.meta["yes_token"]}))
        market.no = parse_book(self.client.get(f"{CLOB}/book", {"token_id": market.meta["no_token"]}))
        return market

    def geoblock(self) -> dict | None:
        """Is TRADING available from this computer's location? (Reading data always is.)"""
        try:
            data = self.client.get("https://polymarket.com/api/geoblock")
        except Exception:
            return None
        return data if isinstance(data, dict) else None


# --- legacy helpers used by the collector -------------------------------------------
def fetch_stubs(limit: int = 200) -> list[Market]:
    raw = _get(f"{GAMMA}/markets", {"closed": "false", "active": "true", "limit": limit})
    return [m for m in (market_stub(r) for r in raw) if m]


def hydrate(market: Market) -> Market:
    market.yes = parse_book(_get(f"{CLOB}/book", {"token_id": market.meta["yes_token"]}))
    market.no = parse_book(_get(f"{CLOB}/book", {"token_id": market.meta["no_token"]}))
    return market
