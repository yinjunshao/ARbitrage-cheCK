"""One front door for all venues: understand what the user typed, and find related markets.

The user can type either a link (Polymarket, Kalshi or Manifold) or the event in words.
Links are parsed locally and only their slug or ticker is used, so we never fetch an
arbitrary URL. Words are searched on every enabled venue and matched by plain word overlap,
and we show the user exactly what we matched so a wrong match is easy to spot.

A venue that is down, or refuses, never stops the others: the problem is recorded in
`notes` and shown in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .compliance import FetchError, ReadOnlyClient, Refused
from .kalshi import Kalshi
from .manifold import Manifold
from .models import Market
from .polymarket import Polymarket
from .relations import similarity

MIN_TEXT_SCORE = 0.35        # below this word overlap we say "no close match" instead of guessing
MIN_CROSS_SCORE = 0.45       # candidates for the same event on another venue
MAX_ANCHORS = 12
_SAFE = (FetchError, Refused, KeyError, TypeError, ValueError, AttributeError)


@dataclass
class Match:
    market: Market
    score: float
    how: str                 # "link" | "search"


@dataclass
class Resolution:
    line: str
    kind: str                # "link" | "text" | "unreadable"
    matches: list[Match] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Registry:
    def __init__(self, venues: list, skipped: dict[str, str] | None = None):
        self.venues = {v.name: v for v in venues}
        self.skipped = dict(skipped or {})      # venue name -> why it is off
        self.notes: list[str] = []

    # -- helpers --------------------------------------------------------------------
    def _safe(self, venue, what: str, fn, *args):
        try:
            return fn(*args)
        except _SAFE as err:
            self.notes.append(f"{venue.name}: could not {what} ({err})")
            return []

    def _parse_link(self, line: str):
        for v in self.venues.values():
            ref = v.parse_url(line)
            if ref:
                return v, ref
        return None

    # -- what the user typed ---------------------------------------------------------
    def resolve(self, line: str) -> Resolution:
        line = line.strip()
        if line.lower().startswith(("http://", "https://")):
            hit = self._parse_link(line)
            if not hit:
                names = ", ".join(self.venues) or "none"
                return Resolution(line, "unreadable", [], [f"We only read links from: {names}. "
                                                           "Paste a link from one of those, or type the event in words."])
            venue, ref = hit
            markets = self._safe(venue, "open that link", venue.lookup, ref)
            linked = [m for m in markets if m.meta.get("linked", True)][:MAX_ANCHORS]
            res = Resolution(line, "link", [Match(m, 1.0, "link") for m in linked])
            if not linked:
                res.notes.append(f"No open Yes/No market found at that {venue.name} link.")
            return res

        res = Resolution(line, "text")
        missed: list[str] = []
        for venue in self.venues.values():
            found = self._safe(venue, "search", venue.search, line, 6)
            scored = sorted(((similarity(line, m.question), m) for m in found), key=lambda x: -x[0])
            good = [(s, m) for s, m in scored if s >= MIN_TEXT_SCORE][:1]
            for s, m in good:
                res.matches.append(Match(m, s, "search"))
            if not good:
                missed.append(venue.name)
        if not res.matches:
            res.notes.append("No close match on " + ", ".join(missed) + ".")
        return res

    # -- finding what is related -------------------------------------------------------
    def cross_candidates(self, anchor: Market, per_venue: int = 2) -> list[Match]:
        """The same (or a very similar) event on the OTHER venues."""
        out: list[Match] = []
        for venue in self.venues.values():
            if venue.name == anchor.venue:
                continue
            found = self._safe(venue, "search", venue.search, anchor.question, 6)
            scored = sorted(((similarity(anchor.question, m.question), m) for m in found), key=lambda x: -x[0])
            good = [(sc, m) for sc, m in scored if sc >= MIN_CROSS_SCORE][:per_venue]
            out.extend(Match(m, sc, "search") for sc, m in good)
        return out

    def siblings(self, market: Market) -> list[Market]:
        venue = self.venues.get(market.venue)
        return self._safe(venue, "list the rest of the event", venue.siblings, market) if venue else []

    def hydrate(self, market: Market) -> Market:
        venue = self.venues.get(market.venue)
        if venue and market.tradeable and not market.has_book:
            self._safe(venue, "read the order book", venue.hydrate, market)
        return market


def build_registry(client: ReadOnlyClient, kalshi_member: bool = False, loopback: bool = True) -> Registry:
    """The real venues. Kalshi is added only for a member running the app on their own computer."""
    venues: list = [Polymarket(client), Manifold(client)]
    skipped: dict[str, str] = {}
    if kalshi_member and loopback:
        client.kalshi_enabled = True
        venues.insert(1, Kalshi(client))
    elif kalshi_member:
        skipped["Kalshi"] = ("Kalshi's terms bar sharing its data with other people, so it is turned off "
                             "whenever the app can be reached from another computer.")
    else:
        skipped["Kalshi"] = ("Off. Kalshi's Developer Agreement limits its API to a member's own trading. "
                             "If you are a Kalshi member, start the app with --kalshi-member.")
    return Registry(venues, skipped)
