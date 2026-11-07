"""Pydantic models for frame I/O.

LLM output schemas: MatchVerdict, RuleDiff, Narrative (these are sent to Gemini as JSON schema).
Frame-internal: Market, Relation, CoreResult and the wrapper records.

CoreResult mirrors the `Opportunity` fields from the architecture note (12). It has NOT been
checked against Debby's real core yet; see docs/CORE_MAP.md.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Venue = Literal["polymarket", "kalshi", "manifold", "predictit", "manual"]
RelationType = Literal[
    "same_event", "a_implies_b", "b_implies_a", "mutually_exclusive", "exhaustive_set", "unrelated", "unclear"
]
Confidence = Literal["low", "medium", "high"]
CoreVerdict = Literal["worth_a_look", "look_closely", "blocked", "nothing_to_do"]
Recommendation = Literal["approve", "review", "kill"]


class Market(BaseModel):
    venue: Venue
    id: str
    event_id: str | None = None
    title: str
    outcome_label: str = "Yes"
    rules_text: str = ""
    close_time_utc: str | None = None  # ISO 8601
    category: str | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    depth: float | None = None  # manual entry only: shares available at the quoted ask
    url: str | None = None


# ---------- LLM output schemas ----------

class MatchVerdict(BaseModel):
    relation: RelationType
    confidence: Confidence
    reasoning: str = Field(description="Short. Two or three sentences.")
    evidence_a: list[str] = Field(description="Verbatim spans copied exactly from market A")
    evidence_b: list[str] = Field(description="Verbatim spans copied exactly from market B")


class RuleDifference(BaseModel):
    topic: Literal["data_source", "yes_definition", "time_zone", "deadline", "tie_break", "other"]
    quote_a: str = Field(description="Verbatim span from rules A ('' if A is silent)")
    quote_b: str = Field(description="Verbatim span from rules B ('' if B is silent)")
    severity: Literal["blocker", "material", "cosmetic"]
    explanation: str


class RuleDiff(BaseModel):
    differences: list[RuleDifference]
    overall: Literal["compatible", "material_differences", "incompatible"]


class Narrative(BaseModel):
    headline: str
    why_gap: str = Field(description="Why the price gap exists")
    what_could_go_wrong: list[str]
    human_checks: list[str] = Field(description="What a human should check before approving")
    recommendation: Recommendation


# ---------- Frame records ----------

class JudgedPair(BaseModel):
    pair_id: str
    a: Market
    b: Market
    verdict: MatchVerdict
    verified: bool
    failed_spans: list[str] = []
    source: Literal["llm", "replay", "fallback"] = "llm"


class VerifiedRuleDiff(BaseModel):
    diff: RuleDiff
    unverified_quotes: list[str] = []
    escalated: bool = False  # overall was made more cautious by verification


class Relation(BaseModel):
    """Built by code AFTER a human confirms. This is the only thing the core sees
    (the adapter translates it into the core's own Relation)."""
    pair_id: str
    relation: RelationType
    legs: list[Market]
    confirmed_by: str
    note: str = ""
    proposed: RelationType | None = None        # what the LLM judge said
    confidence: Confidence | None = None        # the judge's confidence
    edited: bool = False                        # human changed the relation
    evidence_a: list[str] = []                  # verified spans backing the relation
    evidence_b: list[str] = []
    evidence_verified: bool = False
    rule_differences: list[RuleDifference] = []
    rules_verified: bool = True                 # every rule-diff quote found verbatim


class CoreCheck(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail", "na"]
    detail: str = ""


class CoreResult(BaseModel):
    """The core's priced verdict, flattened. Numbers here are the ONLY numbers narrate may use.

    Units (arbcheck): prices and edges are dollars per share (0.01 = 1 cent); ret/ann_ret are fractions.
    """
    verdict: CoreVerdict
    status: str                 # raw guard status: OK | WARN | BLOCKED | NO_ARB
    headline: str               # the guard's own sentence
    kind: str                   # core relation kind
    legs: list[str] = []        # e.g. ["A YES", "B NO"]
    floor: float = 0.0
    naive_edge: float = 0.0     # mid prices, no depth, no fees: what a price-only scanner sees
    top_edge: float = 0.0       # best asks, before fees
    avg_edge: float = 0.0       # over real fills, after fees
    shares: float = 0.0
    cost: float = 0.0
    fee_cost: float = 0.0
    net_profit: float = 0.0
    ret: float = 0.0
    ann_ret: float = 0.0
    days: float = 0.0
    approved_shares: float = 0.0
    approved_cost: float = 0.0
    approved_profit: float = 0.0
    haircut_per_share: float = 0.0
    risk_adj_edge: float = 0.0
    risk_adj_ann: float = 0.0
    clipped: bool = False
    checks: list[CoreCheck] = []
    flags: list[str] = []       # "kind:severity" of every mismatch flag the core saw
    fees: dict[str, str] = {}   # leg -> fee model
    scenarios: list[dict] = []  # per still-possible world: payout and profit per share
    core_version: str


class NarratedResult(BaseModel):
    narrative: Narrative
    grounded: bool
    ungrounded_numbers: list[str] = []
    source: Literal["llm", "llm_retry", "template"]
