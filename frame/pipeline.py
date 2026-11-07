"""Fixed, code-orchestrated pipeline. The LLM is called at three points only.

discover -> candidates -> judge -> rule-diff -> [HUMAN CONFIRM] -> core.price -> narrate -> approve/review/kill
Each case moves through a small state machine; every transition is written to the audit chain.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Literal

from . import candidates as cand
from .adapter import CoreAdapter, get_adapter
from .audit import AuditLog
from .config import Config, get_config
from .discover import discover, load_fixture_markets
from .fewshot import FewShot, LabelStore
from .judge import Judge
from .llm import LLMClient, make_llm
from .narrate import narrate
from .rulediff import compare_rules
from .schemas import CoreResult, JudgedPair, Market, NarratedResult, Relation, RelationType, VerifiedRuleDiff

State = Literal["judged", "filtered", "awaiting_confirm", "rejected", "confirmed", "priced", "narrated",
                "approved", "review", "killed", "error"]
_CONF = {"low": 0, "medium": 1, "high": 2}
_ALLOWED = {
    "judged": {"filtered", "awaiting_confirm"},
    "filtered": {"awaiting_confirm"},  # a human may pull a filtered pair back for review
    "awaiting_confirm": {"confirmed", "rejected"},
    "confirmed": {"priced", "error"},
    "priced": {"narrated"},
    "narrated": {"approved", "review", "killed"},
    "review": {"approved", "killed"},
}


class TransitionError(RuntimeError):
    pass


@dataclass
class Case:
    judged: JudgedPair
    state: State = "judged"
    similarity: float = 0.0
    rules: VerifiedRuleDiff | None = None
    relation: Relation | None = None
    core: CoreResult | None = None
    narrated: NarratedResult | None = None
    error: str | None = None
    history: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.judged.pair_id


class Pipeline:
    def __init__(self, cfg: Config, llm: LLMClient, adapter: CoreAdapter, audit: AuditLog,
                 labels: LabelStore, embedder: cand.Embedder):
        self.cfg, self.llm, self.adapter, self.audit, self.labels, self.embedder = cfg, llm, adapter, audit, labels, embedder
        self.fewshot = FewShot(labels, embedder, cfg.fewshot_k)
        self.judge = Judge(llm, cfg.judge_model, cfg.cache_dir, self.fewshot, cfg.judge_concurrency)
        self.cases: dict[str, Case] = {}

    # ------------------------------------------------------------ state
    def _move(self, case: Case, to: State, **info) -> None:
        if to not in _ALLOWED.get(case.state, set()):
            raise TransitionError(f"{case.id}: {case.state} -> {to} not allowed")
        self.audit.append("transition", {"case": case.id, "from": case.state, "to": to, **info})
        case.history.append(f"{case.state}->{to}")
        case.state = to

    def _surface(self, jp: JudgedPair) -> bool:
        v = jp.verdict
        return v.relation not in ("unrelated", "unclear") and _CONF[v.confidence] >= _CONF[self.cfg.min_surface_confidence]

    # ------------------------------------------------------------ discover + judge
    def scan(self, venues: list[str] = ("polymarket", "kalshi", "predictit")) -> list[Case]:
        markets = discover(self.adapter, list(venues), self.cfg.excluded_categories, self.cfg.kalshi_member)
        self.audit.append("discover", {"venues": list(venues), "n_markets": len(markets), "core": self.adapter.name})
        return self.judge_candidates(markets)

    def judge_candidates(self, markets: list[Market]) -> list[Case]:
        triples = cand.generate(markets, self.embedder, self.cfg.top_k, self.cfg.date_window_days)
        self.audit.append("candidates", {"n": len(triples), "embedder": self.embedder.name})
        judged = self.judge.judge_many([(a, b) for a, b, _ in triples])
        out = []
        for jp, (_, _, sim) in zip(judged, triples):
            out.append(self._register(jp, sim))
        return out

    def check_pair(self, a: Market, b: Market) -> Case:
        """User-entered pair: always shown for review, even if the judge says unrelated."""
        case = self._register(self.judge.judge_pair(a, b), similarity=0.0, force_review=True)
        return case

    def _register(self, jp: JudgedPair, similarity: float, force_review: bool = False) -> Case:
        if jp.pair_id in self.cases:
            return self.cases[jp.pair_id]
        case = Case(judged=jp, similarity=similarity)
        self.cases[case.id] = case
        self.audit.append("judged", {"case": case.id, "verdict": jp.verdict.model_dump(), "verified": jp.verified,
                                     "failed_spans": jp.failed_spans, "source": jp.source})
        if self._surface(jp) or force_review:
            case.rules = compare_rules(self.llm, self.cfg.rules_model, jp.a, jp.b, self.adapter, jp.verdict.relation)
            self.audit.append("rulediff", {"case": case.id, "overall": case.rules.diff.overall,
                                           "unverified": case.rules.unverified_quotes, "escalated": case.rules.escalated})
            self._move(case, "awaiting_confirm")
        else:
            self._move(case, "filtered")
        return case

    def review_queue(self) -> list[Case]:
        return [c for c in self.cases.values() if c.state == "awaiting_confirm"]

    # ------------------------------------------------------------ human
    def confirm(self, case_id: str, user: str, relation: RelationType | None = None, note: str = "") -> Relation:
        case = self.cases[case_id]
        if case.state == "filtered":
            self._move(case, "awaiting_confirm", by=user)
        proposed = case.judged.verdict.relation
        final = relation or proposed
        if final in ("unrelated", "unclear"):
            raise ValueError("A confirmed relation must be a logical link; reject the pair instead.")
        if case.rules is None or final != proposed:  # rules are judged relative to the relation
            case.rules = compare_rules(self.llm, self.cfg.rules_model, case.judged.a, case.judged.b, self.adapter, final)
        rel = Relation(pair_id=case.id, relation=final, legs=[case.judged.a, case.judged.b], confirmed_by=user, note=note)
        case.relation = rel
        self.labels.add(case.id, case.judged.a, case.judged.b, final, True, note, proposed)
        self._move(case, "confirmed", by=user, relation=final, proposed=proposed, edited=final != proposed)
        return rel

    def reject(self, case_id: str, user: str, note: str = "") -> None:
        case = self.cases[case_id]
        if case.state == "filtered":
            self._move(case, "awaiting_confirm", by=user)
        self.labels.add(case.id, case.judged.a, case.judged.b, case.judged.verdict.relation, False, note,
                        case.judged.verdict.relation)
        self._move(case, "rejected", by=user, note=note)

    # ------------------------------------------------------------ core + narrate
    def price(self, case_id: str, size: float) -> CoreResult | None:
        case = self.cases[case_id]
        if case.state != "confirmed" or case.relation is None:
            raise TransitionError("Only a human-confirmed relation can be priced.")
        try:
            core = self.adapter.price(case.relation, size)
        except Exception as e:
            case.error = f"{type(e).__name__}: {e}"
            self._move(case, "error", error=case.error)
            return None
        # Rule differences may only make a trade less likely.
        if case.rules and case.rules.diff.overall == "incompatible" and core.verdict in ("worth_a_look", "look_closely"):
            core = core.model_copy(update={"verdict": "blocked", "flags": core.flags + ["frame_rules_incompatible"]})
        case.core = core
        self.audit.append("core_call", {"case": case_id, "size": size, "adapter": self.adapter.name,
                                        "relation": case.relation.model_dump(mode="json"), "result": core.model_dump()})
        self._move(case, "priced", core_version=core.core_version, verdict=core.verdict)
        case.narrated = narrate(self.llm, self.cfg.narrate_model, core, case.relation, case.rules)
        self.audit.append("narrate", {"case": case_id, "source": case.narrated.source,
                                      "grounded": case.narrated.grounded, "ungrounded": case.narrated.ungrounded_numbers})
        self._move(case, "narrated")
        return core

    def decide(self, case_id: str, decision: Literal["approved", "review", "killed"], user: str, note: str = "") -> None:
        case = self.cases[case_id]
        if decision == "approved" and case.core and case.core.verdict in ("blocked", "nothing_to_do"):
            raise ValueError("The core blocked this; the frame cannot approve it.")
        self._move(case, decision, by=user, note=note, paper_only=True)


def build(replay: bool = False, cfg: Config | None = None, llm: LLMClient | None = None,
          adapter: CoreAdapter | None = None) -> Pipeline:
    cfg = cfg or get_config()
    if replay:  # fixtures are illustrative SAMPLE data, not Kalshi data, so the member gate does not apply
        cfg = replace(cfg, kalshi_member=True)
    audit = AuditLog(cfg.audit_path)
    fixtures = cfg.fixtures_dir / "markets.json"
    markets = load_fixture_markets(fixtures) if fixtures.exists() else []
    adapter = adapter or get_adapter("fake" if replay else cfg.core, markets)
    llm = llm or make_llm(cfg, audit, replay)
    embedder = cand.HashingEmbedder() if (replay or os.getenv("FRAME_EMBEDDER") == "hashing") else cand.default_embedder()
    return Pipeline(cfg, llm, adapter, audit, LabelStore(cfg.labels_path), embedder)
