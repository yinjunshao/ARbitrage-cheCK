"""Glue: markets -> relation -> arbitrage maths -> guard -> a `Case` the UI can show.

A Case is one thing we checked: a set of one or more markets, the logical structure that
links them, the priced result and the guard's verdict. A pair of nested deadlines, the same
event on two venues, a multi-outcome set and a single market's YES+NO are all Cases.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations

from .engine import ArbResult, evaluate_structure
from .guard import Decision, DecisionLog, Guard
from .models import Market, question_stem
from .relations import Relation

FAMILY_LABEL = {
    "cross-platform": "Cross-platform",
    "logical": "Logical link",
    "multi-outcome": "Multi-outcome set",
    "same-market": "Same market",
}


@dataclass
class Case:
    pair_id: str
    markets: list[Market]
    relation: Relation
    arb: ArbResult
    decision: Decision
    role: str = "related"       # "yours" (built from what the user typed) | "related" (we found it)
    note: str = ""              # one plain-English line about why we looked at this

    @property
    def a(self) -> Market:
        return self.markets[0]

    @property
    def b(self) -> Market:
        return self.markets[1]

    @property
    def family(self) -> str:
        if self.relation.kind == "complement":
            return "same-market"
        if self.relation.kind in ("at_most_one", "partition"):
            return "multi-outcome"
        if len({m.venue for m in self.markets}) > 1:
            return "cross-platform"
        return "logical"

    @property
    def family_label(self) -> str:
        return FAMILY_LABEL[self.family]

    @property
    def names(self) -> list[str]:
        """How to refer to each market in plain words."""
        ms = self.markets
        if len(ms) == 2:
            if len({m.venue for m in ms}) == 2:
                return [ms[0].venue, ms[1].venue]
            if ms[0].end != ms[1].end and self.relation.kind in ("a_implies_b", "b_implies_a") \
                    and question_stem(ms[0].question) == question_stem(ms[1].question):
                first, second = ("Earlier contract", "Later contract")
                return [first, second] if ms[0].end < ms[1].end else [second, first]
            return ["Market A", "Market B"]
        return [f"{m.venue}: {m.short}" for m in ms]


# Kept so older code and tests that say "PairReport" still work.
PairReport = Case


def candidate_pairs(markets: list[Market]) -> list[tuple[Market, Market]]:
    """Cheap pre-filter so we never send every possible pair to the LLM.

    Markets with the same question stem and different deadlines are nested-deadline
    candidates. (Linking differently worded questions is done in finder.py.)
    """
    buckets: dict[str, list[Market]] = defaultdict(list)
    for m in markets:
        buckets[question_stem(m.question)].append(m)
    pairs = []
    for group in buckets.values():
        for x, y in combinations(sorted(group, key=lambda m: m.end), 2):
            if x.end != y.end:
                pairs.append((x, y))        # earlier deadline is always market A
    return pairs


def evaluate_case(markets: list[Market], rel: Relation, guard: Guard, now: datetime,
                  fee_rate: float = 0.0, log: DecisionLog | None = None,
                  role: str = "related", note: str = "") -> Case:
    """Price one structure and let the guard decide. The only place these steps are wired together."""
    arb = evaluate_structure(markets, rel.kind, now, fee_rate)
    dec = guard.decide(markets, rel, arb)
    pid = "|".join(m.id for m in markets)
    if log:
        log.write(pid, markets, rel, arb, dec, guard.limits)
    return Case(pid, markets, rel, arb, dec, role, note)


def scan(pairs: list[tuple[Market, Market]], extractor, guard: Guard, now: datetime,
         fee_rate: float = 0.0, log: DecisionLog | None = None) -> list[Case]:
    """Run every pair through the full pipeline. Pairs are processed in the order given,
    and the guard's total-exposure budget is spent in that order."""
    guard.used = 0.0
    cases: list[Case] = []
    for a, b in pairs:
        if a.end > b.end:
            a, b = b, a
        cases.append(evaluate_case([a, b], extractor.extract(a, b), guard, now, fee_rate, log))
    return cases


_RANK = {"OK": 0, "WARN": 1, "BLOCKED": 2, "NO_ARB": 3}


def sort_reports(reports: list[Case]) -> list[Case]:
    """Most useful first: real opportunities, then caught false arbitrages, then the rest."""
    return sorted(reports, key=lambda r: (_RANK[r.decision.status], -r.arb.naive_edge))
