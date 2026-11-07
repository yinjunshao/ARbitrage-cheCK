"""Fixed, code-orchestrated pipeline. The LLM is called at three points only.

discover -> candidates -> suggestion rules -> judge -> rule-diff -> [HUMAN CONFIRM] -> core.price -> narrate
         -> approve/review/kill
Each case moves through a small state machine; every transition is written to the audit chain, and every
suggestion (including the ones the rules refused) to the suggestion log.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from . import candidates as cand
from .adapter import CoreAdapter, get_adapter, tighten
from .audit import AuditLog
from .config import Config, get_config
from .discover import discover, load_fixture_markets
from .fewshot import FewShot, LabelStore
from .judge import Judge, pair_id
from .llm import LLMClient, make_llm
from .narrate import narrate
from .rulediff import compare_rules
from .rules import check_pair as rule_check
from .schemas import CoreResult, JudgedPair, Market, NarratedResult, Relation, RelationType, VerifiedRuleDiff
from .suggestions import SuggestionLog

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
_PRICE_FIELDS = ("yes_bid", "yes_ask", "no_bid", "no_ask", "depth")


class TransitionError(RuntimeError):
    pass


@dataclass
class Case:
    judged: JudgedPair
    state: State = "judged"
    similarity: float = 0.0
    how: str = "user"
    rule_note: str = ""                 # suggestion-rule warning on a user-entered pair
    rules: VerifiedRuleDiff | None = None
    relation: Relation | None = None
    core: CoreResult | None = None
    narrated: NarratedResult | None = None
    error: str | None = None
    history: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.judged.pair_id


@dataclass
class ScanSummary:
    scan: str
    markets: int = 0
    candidates: int = 0
    rule_refused: int = 0
    deferred: int = 0
    judged: int = 0
    surfaced: int = 0
    sets: int = 0
    set_hits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, cfg: Config, llm: LLMClient, adapter: CoreAdapter, audit: AuditLog,
                 labels: LabelStore, embedder: cand.Embedder):
        self.cfg, self.llm, self.adapter, self.audit, self.labels, self.embedder = cfg, llm, adapter, audit, labels, embedder
        self.suggestions = SuggestionLog(cfg.suggestions_path)
        self.fewshot = FewShot(labels, embedder, cfg.fewshot_k)
        self.judge = Judge(llm, cfg.judge_model, cfg.cache_dir, self.fewshot, cfg.judge_concurrency)
        self.cases: dict[str, Case] = {}
        self.last_scan: ScanSummary | None = None
        self.status = ""            # what a running scan is doing right now
        self.on_status = None       # optional callback(text)

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
    def scan(self, venues: list[str] = ("polymarket", "kalshi")) -> ScanSummary:
        sid = time.strftime("%Y%m%dT%H%M%S")
        t0 = time.time()
        self._say("Pulling open markets from the venues…")
        if hasattr(self.adapter, "progress"):
            self.adapter.progress = self._say
        markets = discover(self.adapter, list(venues), self.cfg.excluded_categories, self.cfg.kalshi_member)
        markets = [m for m in markets if m.venue != "manifold"]  # play money: signal only, never a leg
        s = ScanSummary(scan=sid, markets=len(markets), notes=list(getattr(self.adapter, "last_notes", [])))
        self.audit.append("discover", {"scan": sid, "venues": list(venues), "n_markets": len(markets),
                                       "core": self.adapter.name, "notes": s.notes})
        self.judge_candidates(markets, s)
        self._say(f"Pricing venue-declared outcome sets with the core…")
        self.price_venue_sets(markets, s)
        self.last_scan = s
        self.audit.append("scan_done", s.__dict__)
        self._say(f"Done in {time.time() - t0:.0f}s: {s.markets} markets, {s.surfaced} suggestions for review.")
        return s

    def _say(self, text: str) -> None:
        """Where a running scan is up to (shown live in the app and printed by the monitor)."""
        self.status = text
        cb = getattr(self, "on_status", None)
        if cb:
            cb(text)

    def judge_candidates(self, markets: list[Market], s: ScanSummary | None = None) -> list[Case]:
        s = s or ScanSummary(scan=time.strftime("%Y%m%dT%H%M%S"), markets=len(markets))
        link = getattr(self.adapter, "cheap_link", None)
        self._say(f"Finding candidate pairs among {len(markets):,} markets and applying the suggestion rules…")
        raw = cand.generate(markets, self.embedder, self.cfg.top_k, self.cfg.date_window_days,
                            self.cfg.min_similarity, link)
        allowed = []
        for a, b, sim, how in raw:
            pid = pair_id(a, b)
            r = rule_check(a, b)
            if not r.ok:
                s.rule_refused += 1
                self.suggestions.rule_refused(pid, a, b, r.rule, r.reason, how, s.scan)
                continue
            if pid in self.cases:
                continue
            self.suggestions.candidate(pid, a, b, how, sim, s.scan)
            allowed.append((a, b, sim, how, pid))
        s.candidates = len(allowed)
        # LLM budget: within-event (core-linked) first, then the most similar. Cached verdicts are free.
        allowed.sort(key=lambda t: (t[3] != "within_event", -t[2]))
        todo = allowed[: self.cfg.max_judge_per_scan]
        s.deferred = len(allowed) - len(todo)  # their `candidate` row stands until a later scan judges them
        self._say(f"Asking Gemini about {len(todo)} of {len(allowed):,} candidate pairs ({s.rule_refused:,} refused by rules)…")
        judged = self.judge.judge_many([(a, b) for a, b, *_ in todo])
        # Rule comparisons for the surfaced ones, in parallel (the slow part of a scan).
        from concurrent.futures import ThreadPoolExecutor
        need = [jp for jp in judged if self._surface(jp) and jp.pair_id not in self.cases]
        self._say(f"Gemini is comparing the resolution rules of {len(need)} related pairs…")
        with ThreadPoolExecutor(max_workers=self.cfg.judge_concurrency) as ex:
            diffs = dict(zip([jp.pair_id for jp in need], ex.map(
                lambda jp: compare_rules(self.llm, self.cfg.rules_model, jp.a, jp.b, self.adapter, jp.verdict.relation),
                need)))
        out = [self._register(jp, sim, how=how, rules=diffs.get(jp.pair_id))
               for jp, (_, _, sim, how, _) in zip(judged, todo)]
        s.judged = len(out)
        s.surfaced = sum(c.state == "awaiting_confirm" for c in out)
        return out

    def price_venue_sets(self, markets: list[Market], s: ScanSummary, budget: float = 300.0) -> None:
        """Venue-declared exclusive sets (Polymarket negRisk, Kalshi mutually_exclusive): the relation comes from
        the venue's own metadata, not the LLM, so the core prices them directly. Results go to the suggestion log."""
        fn = getattr(self.adapter, "exclusive_sets", None)
        if fn is None:
            return
        for legs in fn(markets, self.cfg.max_sets_per_scan):
            try:
                core = self.adapter.price_set(legs, budget)
            except Exception as e:
                s.notes.append(f"set {legs[0].event_id}: {type(e).__name__}: {e}")
                continue
            s.sets += 1
            if core.verdict in ("worth_a_look", "look_closely"):
                s.set_hits.append(f"{legs[0].event_id}: {core.verdict} | {core.headline}")
            self.suggestions.venue_set(f"set:{legs[0].venue}:{legs[0].event_id}", legs, core)

    def check_lines(self, line_a: str, line_b: str, max_pairs: int = 6) -> tuple[list[Case], list[str]]:
        """Links or words for two events -> markets via the core's registry -> judged pairs.
        A link to a multi-outcome event gives several markets: each outcome on A is paired with its
        closest outcome on B (not just the first of each)."""
        sides, notes = [], []
        for line in (line_a, line_b):
            ms = [m for m in self.adapter.resolve(line) if m.venue != "manifold"]
            notes += list(getattr(self.adapter, "last_notes", []))
            sides.append(ms)
        if not all(sides):
            return [], notes or ["Could not match both inputs to an open market."]
        A, B = sides
        pairs = _match_outcomes(A, B)[:max_pairs]
        self.audit.append("resolve", {"inputs": [line_a, line_b], "matched": [[f"{a.venue}:{a.id}", f"{b.venue}:{b.id}"]
                                                                            for _, a, b in pairs]})
        judged = self.judge.judge_many([(a, b) for _, a, b in pairs])
        cases = []
        for jp, (sim, a, b) in zip(judged, pairs):
            r = rule_check(a, b)
            self.suggestions.candidate(jp.pair_id, a, b, "user", sim, "user")
            case = self._register(jp, sim, force_review=True, how="user")
            if not r.ok:
                case.rule_note = f"{r.rule}: {r.reason}"
            cases.append(case)
        return cases, notes

    def check_pair(self, a: Market, b: Market) -> Case:
        """User-entered pair: always shown for review, even if the judge says unrelated."""
        r = rule_check(a, b)
        self.suggestions.candidate(pair_id(a, b), a, b, "user", 0.0, "user")
        case = self._register(self.judge.judge_pair(a, b), similarity=0.0, force_review=True, how="user")
        if not r.ok:
            case.rule_note = f"{r.rule}: {r.reason}"
        return case

    def _register(self, jp: JudgedPair, similarity: float, force_review: bool = False, how: str = "",
                  rules: VerifiedRuleDiff | None = None) -> Case:
        if jp.pair_id in self.cases:
            return self.cases[jp.pair_id]
        case = Case(judged=jp, similarity=similarity, how=how)
        self.cases[case.id] = case
        v = jp.verdict
        self.audit.append("judged", {"case": case.id, "verdict": v.model_dump(), "verified": jp.verified,
                                     "failed_spans": jp.failed_spans, "source": jp.source})
        self.suggestions.judged(case.id, v.relation, v.confidence, jp.verified, jp.source)
        if self._surface(jp) or force_review:
            case.rules = rules or compare_rules(self.llm, self.cfg.rules_model, jp.a, jp.b, self.adapter, v.relation)
            self.audit.append("rulediff", {"case": case.id, "overall": case.rules.diff.overall,
                                           "unverified": case.rules.unverified_quotes, "escalated": case.rules.escalated})
            self._move(case, "awaiting_confirm")
            self.suggestions.surfaced(case.id, True, case.rules.diff.overall)
        else:
            self._move(case, "filtered")
            self.suggestions.surfaced(case.id, False)
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
        v = case.judged.verdict
        rel = Relation(
            pair_id=case.id, relation=final, legs=[case.judged.a, case.judged.b], confirmed_by=user, note=note,
            proposed=proposed, confidence=v.confidence, edited=final != proposed,
            evidence_a=v.evidence_a, evidence_b=v.evidence_b, evidence_verified=case.judged.verified,
            rule_differences=case.rules.diff.differences if case.rules else [],
            rules_verified=not (case.rules and case.rules.unverified_quotes),
        )
        case.relation = rel
        self.labels.add(case.id, case.judged.a, case.judged.b, final, True, note, proposed)
        self._save_confirmed(rel)
        self._move(case, "confirmed", by=user, relation=final, proposed=proposed, edited=final != proposed)
        self.suggestions.human(case.id, "confirmed" if final == proposed else "edited", user, final, note)
        return rel

    def reject(self, case_id: str, user: str, note: str = "") -> None:
        case = self.cases[case_id]
        if case.state == "filtered":
            self._move(case, "awaiting_confirm", by=user)
        self.labels.add(case.id, case.judged.a, case.judged.b, case.judged.verdict.relation, False, note,
                        case.judged.verdict.relation)
        self._move(case, "rejected", by=user, note=note)
        self.suggestions.human(case.id, "rejected", user, None, note)

    # ------------------------------------------------------------ core + narrate
    def price(self, case_id: str, budget: float) -> CoreResult | None:
        """budget = the core guard's per-trade exposure cap in dollars."""
        case = self.cases[case_id]
        if case.state != "confirmed" or case.relation is None:
            raise TransitionError("Only a human-confirmed relation can be priced.")
        try:
            core = self.adapter.price(case.relation, budget)
        except Exception as e:
            case.error = f"{type(e).__name__}: {e}"
            self._move(case, "error", error=case.error)
            return None
        # Rule differences may only make a trade less likely (the core already blocks on high flags).
        if case.rules and case.rules.diff.overall == "incompatible":
            core = tighten(core, "blocked", "frame_rules_incompatible")
        case.core = core
        kalshi = any(l.venue == "kalshi" for l in case.relation.legs)
        self.audit.append("core_call", {"case": case_id, "budget": budget, "adapter": self.adapter.name,
                                        "verdict": core.verdict, "core_version": core.core_version,
                                        "result": None if kalshi else core.model_dump()})
        self.suggestions.priced(case_id, case.relation.legs, core)
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
        self.suggestions.human(case_id, decision, user, case.relation.relation if case.relation else None, note)

    # ------------------------------------------------------------ monitoring confirmed relations
    def _save_confirmed(self, rel: Relation) -> None:
        """Keep human-confirmed relations so the monitor can re-price them on fresh books.
        Kalshi prices are stripped (its terms bar storing its data)."""
        d = rel.model_dump(mode="json")
        for leg in d["legs"]:
            if leg["venue"] == "kalshi":
                for k in _PRICE_FIELDS:
                    leg[k] = None
        path = Path(self.cfg.confirmed_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(d) + "\n")

    def confirmed_relations(self) -> list[Relation]:
        path = Path(self.cfg.confirmed_path)
        if not path.exists():
            return []
        latest: dict[str, Relation] = {}
        for line in path.open(encoding="utf-8"):
            if line.strip():
                r = Relation.model_validate_json(line)
                latest[r.pair_id] = r
        return list(latest.values())

    def reprice_confirmed(self, budget: float = 300.0) -> list[tuple[Relation, CoreResult | None, str]]:
        """Re-price every confirmed relation whose markets are still open. No LLM; the core only."""
        out = []
        for rel in self.confirmed_relations():
            try:
                core = self.adapter.price(rel, budget)
                self.suggestions.priced(rel.pair_id, rel.legs, core, event="repriced")
                out.append((rel, core, ""))
            except Exception as e:
                out.append((rel, None, f"{type(e).__name__}: {e}"))
        return out


_STOP = {"will", "the", "a", "an", "of", "in", "on", "by", "after", "before", "be", "there", "to", "at", "for", "is"}


_SYN = {"hike": "up", "hikes": "up", "raise": "up", "raises": "up", "increase": "up", "increases": "up", "higher": "up",
        "cut": "down", "cuts": "down", "decrease": "down", "decreases": "down", "lower": "down", "reduce": "down",
        "maintain": "hold", "maintains": "hold", "unchanged": "hold", "hold": "hold", "holds": "hold", "pause": "hold",
        "no": "hold"}


def _tokens(title: str) -> set[str]:
    import re
    low = title.lower()
    words = re.findall(r"[a-z]+|\d+", low)  # "25bps" -> 25, bps
    toks = {_SYN.get(t, t) for t in words if t not in _STOP}
    if re.search(r">\s*\d|\d\s*\+|more than|at least|or more", low):  # ">25bps", "50+ bps": an open-ended bucket
        toks.add("<more>")
    return toks


def _match_outcomes(A: list[Market], B: list[Market]) -> list[tuple[float, Market, Market]]:
    """One-to-one matching of outcomes by title word overlap (greedy, best first). The judge then decides
    whether each matched pair is really linked, so a weak match costs one LLM call, not a wrong price."""
    if len(A) == 1 or len(B) == 1:
        return [(1.0, a, b) for a in A for b in B]
    scored = sorted(((len(_tokens(a.title) & _tokens(b.title)) / max(1, len(_tokens(a.title) | _tokens(b.title))), i, j)
                     for i, a in enumerate(A) for j, b in enumerate(B)), reverse=True)
    used_a, used_b, out = set(), set(), []
    for s, i, j in scored:
        if i not in used_a and j not in used_b:
            used_a.add(i), used_b.add(j)
            out.append((s, A[i], B[j]))
    return out


def build(replay: bool = False, demo: bool = False, cfg: Config | None = None, llm: LLMClient | None = None,
          adapter: CoreAdapter | None = None) -> Pipeline:
    """Default: LIVE venues through arbcheck's read-only client + live Gemini.
    demo/replay exist only for the test suite (arbcheck's invented markets, recorded LLM outputs)."""
    cfg = cfg or get_config()
    demo = demo or replay
    if demo:  # invented markets, not Kalshi data, so the member gate does not apply
        cfg = replace(cfg, kalshi_member=True)
    audit = AuditLog(cfg.audit_path)
    if adapter is None:
        fixtures = cfg.fixtures_dir / "markets.json"
        markets = load_fixture_markets(fixtures) if cfg.core == "fake" and fixtures.exists() else None
        adapter = get_adapter(cfg.core, demo=demo, kalshi_member=cfg.kalshi_member, markets=markets)
    llm = llm or make_llm(cfg, audit, replay)
    embedder = cand.HashingEmbedder() if (replay or os.getenv("FRAME_EMBEDDER") == "hashing") else cand.default_embedder()
    return Pipeline(cfg, llm, adapter, audit, LabelStore(cfg.labels_path), embedder)
