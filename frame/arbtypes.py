"""The kinds of false/real arbitrage the app looks for, named the way the numeric core names them.

  same-market   Yes and No of ONE market cost less than $1 together          (note 10, type 1)
  multi-outcome the outcomes of one event: not all can win / one must win    (type 2)
  cross-platform the same (or logically linked) event on two venues          (type 3)
  logical       one outcome forces another on one venue: nested deadlines,
                thresholds ("by June" implies "by December")                 (type 4)

`classify` mirrors arbcheck's own `Case.family`, so a pair is labelled the same before and after pricing.
"""
from __future__ import annotations

SAME_MARKET, MULTI_OUTCOME, CROSS_PLATFORM, LOGICAL = "same-market", "multi-outcome", "cross-platform", "logical"

TYPES: dict[str, dict[str, str]] = {
    CROSS_PLATFORM: {
        "label": "Cross-platform",
        "what": "The same event (or a linked one) on two venues, priced differently.",
        "risk": "Each venue may settle it differently: source, wording, time zone.",
    },
    LOGICAL: {
        "label": "Logical link",
        "what": "One outcome forces another: nested deadlines or thresholds, so the implied side can't cost more.",
        "risk": "The implication may not be strict once the rules are read closely.",
    },
    MULTI_OUTCOME: {
        "label": "Multi-outcome set",
        "what": "Outcomes of one event that can't all happen (or must cover everything): the prices should add up to $1.",
        "risk": "A hidden 'other' outcome or a void clause breaks the sum.",
    },
    SAME_MARKET: {
        "label": "Same market (Yes + No)",
        "what": "Yes and No of one market together cost less than the $1 they pay.",
        "risk": "Rare and short-lived; fees and spreads usually eat it.",
    },
}
ORDER = [CROSS_PLATFORM, LOGICAL, MULTI_OUTCOME, SAME_MARKET]


def label(t: str) -> str:
    return TYPES.get(t, {}).get("label", t or "Unclassified")


def classify(relation: str, venues: list[str], n_legs: int = 2) -> str:
    """relation: the logical link (frame names or core kinds). venues: venue of each leg."""
    if n_legs == 1 or relation == "complement":
        return SAME_MARKET
    if relation in ("at_most_one", "partition") or n_legs > 2:
        return MULTI_OUTCOME
    return CROSS_PLATFORM if len(set(venues)) > 1 else LOGICAL
