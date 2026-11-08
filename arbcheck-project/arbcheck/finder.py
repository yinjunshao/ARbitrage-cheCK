"""The user-facing workflow: "I think these events are an arbitrage. Is it real?"

  1. READ the input. Links and plain-English events are matched to real markets, and we
     keep the matches so the UI can show exactly what we understood.
  2. CHECK THE USER'S IDEA. Every combination of the markets they gave us is tested as
     a structure: nested deadlines, price thresholds, the same event on two venues, a
     multi-outcome set, or one market's Yes and No. The verdict is the best of those.
  3. LOOK AROUND. Starting from the same markets we look for what else is linked to them:
     the rest of their event (siblings), the same event on other venues, and each market's
     own Yes+No. Edge is edge, so anything profitable surfaces, whether or not the user asked.
  4. Everything goes through the SAME pipeline: relation -> order-book maths -> guard.
     Nothing in here decides a trade; it only chooses what to look at.

Nothing here places orders, and nothing from Kalshi is stored (see compliance.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations

from .guard import Guard
from .models import Market
from .pipeline import Case, evaluate_case
from .relations import HeuristicExtractor, Relation, structural_relation
from .venues import Registry, Resolution

MAX_LINES = 4
MAX_LINE_CHARS = 300
MAX_ANCHORS_FOR_RELATED = 3
MAX_SIBLINGS = 9              # so an event of up to 10 markets is covered in full
MAX_PAIR_CASES = 6
_RANK = {"OK": 0, "WARN": 1, "BLOCKED": 2, "NO_ARB": 3}

COMPLEMENT_WHY = ("A market's Yes and No shares always pay $1 between them. If both can be bought for less "
                  "than $1 in total, the difference is locked in.")
EXCLUSIVE_WHY = ("The venue lists these as mutually exclusive outcomes of one event, so at most one of them "
                 "can resolve Yes. That means one share of No on every outcome pays at least the number of "
                 "outcomes minus one.")


@dataclass
class Signal:
    """A price on a play-money venue next to a real-money price for the same event."""
    anchor: Market
    other: Market
    gap: float               # other probability minus anchor probability


@dataclass
class Findings:
    inputs: list[Resolution]
    anchors: list[Market]
    yours: list[Case] = field(default_factory=list)
    related: list[Case] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    checked: int = 0
    notes: list[str] = field(default_factory=list)
    kill_switch: bool = False

    @property
    def best(self) -> Case | None:
        return min(self.yours, key=_rank_key) if self.yours else None

    @property
    def verdict(self) -> str:
        """real | false | blocked | none | unlinked | single | unmatched"""
        if not self.yours:
            return "unmatched"
        best = self.best
        s = best.decision.status
        if s in ("OK", "WARN"):
            return "real"
        if s == "BLOCKED":
            if self.kill_switch:
                return "blocked"
            return "false" if best.arb.executable else "blocked"
        if all(c.relation.kind == "none" for c in self.yours):
            return "unlinked"
        if all(c.relation.kind == "complement" for c in self.yours):
            return "single"
        return "none"


def _rank_key(c: Case):
    return (_RANK[c.decision.status], -c.decision.risk_adj_ann, -c.arb.naive_edge)


def _ordered(a: Market, b: Market) -> tuple[Market, Market]:
    return (a, b) if a.end <= b.end else (b, a)


def _key(markets: list[Market], kind: str) -> tuple:
    return (frozenset((m.venue, m.id) for m in markets), kind)


def find(lines: list[str], registry: Registry, extractor, guard: Guard, now: datetime,
         fee_rate: float = 0.0, log=None, max_hydrate: int = 24) -> Findings:
    guard.used = 0.0
    registry.notes.clear()
    cleaned = [ln.strip()[:MAX_LINE_CHARS] for ln in lines if ln.strip()][:MAX_LINES]
    resolutions = [registry.resolve(ln) for ln in cleaned]

    anchors: list[Market] = []
    seen_ids: set[tuple] = set()
    for res in resolutions:
        for m in res.matches:
            if (m.market.venue, m.market.id) not in seen_ids:
                seen_ids.add((m.market.venue, m.market.id))
                anchors.append(m.market)
    out = Findings(resolutions, anchors, kill_switch=guard.limits.kill_switch)

    budget = {"left": max_hydrate}

    def hydrated(m: Market) -> Market:
        if m.tradeable and not m.has_book and budget["left"] > 0:
            budget["left"] -= 1
            registry.hydrate(m)
        return m

    tradeable = [hydrated(m) for m in anchors if m.tradeable]
    tradeable = [m for m in tradeable if m.has_book]
    done: set[tuple] = set()

    def add(bucket: list[Case], markets: list[Market], rel: Relation, role: str, note: str) -> Case | None:
        key = _key(markets, rel.kind)
        if key in done:
            return None
        done.add(key)
        case = evaluate_case(markets, rel, guard, now, fee_rate, log, role, note)
        out.checked += 1
        bucket.append(case)
        return case

    def pair(bucket, x: Market, y: Market, role: str, note: str):
        a, b = _ordered(x, y)
        return add(bucket, [a, b], extractor.extract(a, b), role, note)

    def exclusive_group(ms: list[Market]) -> bool:
        return len(ms) >= 3 and all(m.meta.get("exclusive") and m.group and m.group == ms[0].group for m in ms)

    # ---- 2. the user's own idea ------------------------------------------------------
    by_group: dict[str, list[Market]] = {}
    for m in tradeable:
        if m.group:
            by_group.setdefault(m.group, []).append(m)
    set_groups = {g for g, ms in by_group.items() if exclusive_group(ms)}
    for g in set_groups:
        add(out.yours, by_group[g], structural_relation(
            "at_most_one", EXCLUSIVE_WHY, "metadata", 0.9, "venue-metadata"), "yours",
            "The outcomes of one event you linked to.")
    pairs = [(x, y) for x, y in combinations(tradeable, 2)
             if not (x.group and x.group == y.group and x.group in set_groups)]
    for x, y in pairs[:MAX_PAIR_CASES]:
        pair(out.yours, x, y, "yours", "Two of the markets you gave us.")
    if len(tradeable) == 1:
        add(out.yours, [tradeable[0]], structural_relation("complement", COMPLEMENT_WHY, "structure", 1.0),
            "yours", "The one market you gave us, on its own.")

    # ---- 3. what else is linked to what they gave us ----------------------------------
    for a in tradeable[:MAX_ANCHORS_FOR_RELATED]:
        sibs = [hydrated(s) for s in registry.siblings(a)[:MAX_SIBLINGS]]
        sibs = [s for s in sibs if s.has_book and (s.venue, s.id) not in seen_ids]
        group = [a] + sibs
        if exclusive_group(group):
            # One venue-declared set: price it as a set, not as a pile of unrelated pairs.
            if a.group not in set_groups:
                add(out.related, group, structural_relation(
                    "at_most_one", EXCLUSIVE_WHY, "metadata", 0.9, "venue-metadata"), "related",
                    "All the outcomes of the same event.")
        else:
            for s in sibs:
                pair(out.related, a, s, "related", "Another market in the same event.")
            # Pairs among the siblings themselves (for example three deadlines of one question)
            # are only worth pricing when a cheap check already sees a link.
            cheap = HeuristicExtractor()
            for x, y in combinations(sibs, 2):
                if cheap.extract(*_ordered(x, y)).kind != "none":
                    pair(out.related, x, y, "related", "Two other markets in the same event.")
        for c in registry.cross_candidates(a):
            m = c.market
            if m.tradeable:
                hydrated(m)
                if m.has_book:
                    pair(out.related, a, m, "related", f"The same event on {m.venue}.")
            elif m.prob is not None and a.mid_yes is not None:
                out.signals.append(Signal(a, m, m.prob - a.mid_yes))
        for m in [a] + sibs:
            if m.has_book:
                add(out.related, [m], structural_relation("complement", COMPLEMENT_WHY, "structure", 1.0),
                    "related", "Buying both sides of one market.")

    out.yours.sort(key=_rank_key)
    out.related.sort(key=_rank_key)
    out.notes = list(registry.notes)
    return out
