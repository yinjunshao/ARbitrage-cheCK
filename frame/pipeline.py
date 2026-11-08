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
from . import arbtypes, sports
from .narrate import narrate, structural_narrative
from .watch import WatchItem, WatchList, leg_dict
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
    external: dict | None = None        # another scanner's claim about this pair (e.g. prediction.com's ROI)
    priced_at: float = 0.0              # when the core last priced it (epoch seconds)

    @property
    def id(self) -> str:
        return self.judged.pair_id

    @property
    def arb_type(self) -> str:
        """Which kind of arbitrage this would be: the core's label once priced, else from the judged relation."""
        if self.core and self.core.family:
            return self.core.family
        rel = self.relation.relation if self.relation else self.judged.verdict.relation
        return arbtypes.classify(rel, [self.judged.a.venue, self.judged.b.venue])

    @property
    def category(self) -> str:
        return self.judged.a.category or self.judged.b.category or "other"


@dataclass
class Finding:
    """Something priced from structure alone (a venue-declared outcome set, or Yes + No of one market):
    no LLM judgement is involved, so there is nothing for a human to confirm before the core prices it."""
    id: str
    arb_type: str
    legs: list[Market]
    core: CoreResult
    narrated: NarratedResult
    category: str = "other"
    title: str = ""
    scan: str = ""
    at: float = field(default_factory=time.time)


@dataclass
class Screen:
    """A code-derived logical pair (date/threshold ladder, or exact score => result) priced by the core as a
    screen, no LLM. Shown as 'watching'; escalated to Gemini + a human only if the screen shows a gap."""
    id: str
    a: Market
    b: Market
    core: CoreResult
    how: str
    why: str = ""
    scan: str = ""
    at: float = field(default_factory=time.time)   # when it was last priced


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
    singles: int = 0
    screened: int = 0       # code-derived logical pairs priced by the core as a screen (no LLM)
    escalated: int = 0      # ... of which showed a gap and were sent to Gemini
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
        self.findings: dict[str, Finding] = {}
        self.screens: dict[str, Screen] = {}
        self.watch = WatchList(cfg.watch_path)
        self.alerts: list[tuple[float, WatchItem, CoreResult]] = []   # watch re-checks that showed a gap
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
    def scan(self, venues: list[str] | None = None, categories: list[str] | None = None,
             leagues: list[str] | None = None) -> ScanSummary:
        """venues / categories / leagues: what to scan (the app's checkboxes). None = the config defaults.
        categories 'all' or None = every category. Sports markets are kept only for the ticked leagues
        (a sports market whose league can't be told, e.g. Kalshi's, is kept)."""
        sid = time.strftime("%Y%m%dT%H%M%S")
        t0 = time.time()
        venues = list(venues or self.cfg.scan_venues)
        cats = set(categories or self.cfg.scan_categories)
        leagues = list(self.cfg.sports_leagues if leagues is None else leagues)
        all_cats = not cats or "all" in cats
        if hasattr(self.adapter, "sports_leagues"):
            self.adapter.sports_leagues = leagues if (all_cats or "sports" in cats) else []
        self._say(f"Pulling open markets from {', '.join(venues)}…")
        if hasattr(self.adapter, "progress"):
            self.adapter.progress = self._say
        markets = discover(self.adapter, venues, self.cfg.excluded_categories, self.cfg.kalshi_member)
        markets = [m for m in markets if m.venue != "manifold"]  # play money: signal only, never a leg
        if not all_cats:
            markets = [m for m in markets if (m.category or "other") in cats]
        if leagues is not None and (all_cats or "sports" in cats):
            markets = [m for m in markets if m.category != "sports" or not sports.sport_of(m)
                       or sports.sport_of(m) & set(leagues)]
        s = ScanSummary(scan=sid, markets=len(markets), notes=list(getattr(self.adapter, "last_notes", [])))
        self.audit.append("discover", {"scan": sid, "venues": list(venues), "categories": sorted(cats),
                                       "leagues": leagues, "n_markets": len(markets),
                                       "core": self.adapter.name, "notes": s.notes})
        self.judge_candidates(markets, s)
        self._say("Pricing venue-declared outcome sets with the core…")
        self.price_venue_sets(markets, s)
        self._say("Checking Yes + No of single markets with the core…")
        self.price_singles(markets, s)
        self.last_scan = s
        self.audit.append("scan_done", s.__dict__)
        self._say(f"Done in {time.time() - t0:.0f}s: {s.markets} markets, {s.surfaced} suggestions for review.")
        return s

    def _rule(self, a: Market, b: Market, nested: bool = False):
        """The suggestion rules (R0-R6) with this run's time limits. nested: a ladder inside one event."""
        now_fn = getattr(self.adapter, "now", None)      # the core's clock (frozen in test mode)
        return rule_check(a, b, now=now_fn() if now_fn else None, nested=nested,
                          horizon_days=self.cfg.max_days_to_close, gap_days=self.cfg.max_close_gap_days)

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
        # The same match on two venues (e.g. Polymarket and Novig), matched by code: added as cross-venue candidates,
        # so they are judged first (still Gemini + a human before any pricing).
        have = {pair_id(a, b) for a, b, *_ in raw}
        raw += [(a, b, 0.99, "cross_venue") for a, b, _ in sports.cross_venue_games(markets) if pair_id(a, b) not in have]
        allowed = []
        for a, b, sim, how in raw:
            pid = pair_id(a, b)
            r = self._rule(a, b, nested=how == "within_event")
            if not r.ok:
                s.rule_refused += 1
                self.suggestions.rule_refused(pid, a, b, r.rule, r.reason, how, s.scan)
                continue
            if pid in self.cases:
                continue
            self.suggestions.candidate(pid, a, b, how, sim, s.scan)
            allowed.append((a, b, sim, how, pid))
        # Code-derived logical pairs (date/threshold ladders inside one event; exact score => result inside one
        # game) are SCREENED by the core first, with no LLM: a ladder like "by Nov 8" vs "by Nov 9" is only an
        # arbitrage if the earlier date is priced ABOVE the later one, which is rare. Only pairs whose screen shows
        # a gap go on to Gemini and a human; the rest join the watch list and are re-screened as the date nears.
        escalated: list = []
        if getattr(self.adapter, "screen_pair", None) is not None:
            # Pairs already on the watch list are re-checked by the watch loop, so the per-scan screening budget
            # goes to NEW ones (otherwise the same soonest 40 would be screened every scan and the rest never).
            ladders = [(*t, None) for t in allowed if t[3] == "within_event" and t[4] not in self.watch.items]
            allowed = [t for t in allowed if t[3] != "within_event"]
            links = []
            from .windows import window_links
            derived = sports.game_links(markets) + window_links(markets, getattr(self.adapter, "event_starts", {}))
            for a, b, why in derived:
                pid = pair_id(a, b)
                if pid not in self.cases and pid not in self.watch.items and self._rule(a, b, nested=True).ok:
                    links.append((a, b, 1.0, "game_link", pair_id(a, b), why))
            # The same match on two venues: screened every scan (cheap) so the board always has fresh prices for it.
            for a, b, why in sports.cross_venue_games(markets):
                if self._rule(a, b).ok:
                    links.append((a, b, 1.0, "cross_game", pair_id(a, b), why))
            escalated = self._screen_many(ladders + links, s)
        s.candidates = len(allowed) + len(escalated)
        # LLM budget, shared out evenly across categories so one busy category (crypto has the most markets)
        # can't use it all. Inside a category: cross-venue pairs first, then within-event, then by similarity.
        # Screened pairs that showed a gap go first.
        todo = escalated[: self.cfg.max_judge_per_scan]
        todo += balanced(allowed, self.cfg.max_judge_per_scan - len(todo))
        allowed = escalated + allowed
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

    def _screen_many(self, items: list, s: ScanSummary) -> list:
        """items: (a, b, sim, how, pid, why). Screens up to max_screens_per_scan, games in play first, then the
        soonest to close. Returns the candidate tuples whose screen showed a gap (to be judged by Gemini)."""
        def urgency(t):
            a, b = t[0], t[1]
            live = sports.in_play(a) or sports.in_play(b)
            # cross-venue game pairs first (the board's main content), then games in play, then the soonest to close
            return (t[3] != "cross_game", not live, min(a.close_time_utc or "9", b.close_time_utc or "9"))
        out = []
        for a, b, sim, how, pid, why in sorted(items, key=urgency)[: self.cfg.max_screens_per_scan]:
            try:
                core = self.adapter.screen_pair(a, b, why=why, kind="equivalent" if how == "cross_game" else None)
            except Exception as e:
                s.notes.append(f"screen {a.id}/{b.id}: {type(e).__name__}: {e}")
                continue
            s.screened += 1
            self.screens[pid] = Screen(pid, a, b, core, how, why or "", s.scan)
            self.suggestions.priced(pid, [a, b], core, event="screened")
            self._watch(pid, "cross_game" if how == "cross_game" else ("game_link" if why else "ladder"),
                        arbtypes.CROSS_PLATFORM if how == "cross_game" else arbtypes.LOGICAL,
                        why or f"{a.title[:60]} / {b.title[:60]}", [a, b], core, why or "")
            if how != "cross_game" and (core.verdict in ("worth_a_look", "look_closely")
                                        or (core.naive_edge > 0 and core.verdict != "blocked")):
                s.escalated += 1                    # cross-venue game pairs already go to Gemini as cross_venue candidates
                out.append((a, b, max(sim, 0.99), how, pid))
        return out

    # ------------------------------------------------------------ the smart watch loop
    def _watch(self, key: str, kind: str, arb_type: str, title: str, legs: list[Market], core: CoreResult | None,
               why: str = "") -> None:
        closes = [m.close_time_utc for m in legs if m.close_time_utc]
        item = WatchItem(key=key, kind=kind, arb_type=arb_type, title=title, legs=[leg_dict(m) for m in legs],
                         close=min(closes) if closes else None, why=why, category=legs[0].category or "other",
                         live=any(sports.in_play(m) for m in legs))
        if core is not None:
            item.last_verdict = core.verdict
            item.last_edge = None if item.kalshi else core.naive_edge
        self.watch.add(item)

    def watch_tick(self, budget: float = 300.0, limit: int = 15) -> list[tuple[WatchItem, CoreResult | None, str]]:
        """Re-check the watch-list items that are due, with the core only (no LLM). Returns (item, core, error).
        A re-check that now shows a gap is logged as `watch_hit`, kept in self.alerts, and a screened pair that
        turns positive is sent to the Gemini queue at the next scan."""
        out = []
        due = self.watch.due(limit=limit)
        for n, it in enumerate(due, 1):
            self._say(f"Watching: re-checking {n} of {len(due)} due items with the core… ({it.title[:50]})")
            legs = [Market(**{k: v for k, v in l.items() if k in Market.model_fields}) for l in it.legs]
            it.live = any(sports.in_play(m) for m in legs)
            try:
                if it.kind == "set":
                    core = self.adapter.price_set(legs, budget)
                elif it.kind == "single":
                    core = self.adapter.price_single(legs[0], budget)
                elif it.kind in ("ladder", "game_link", "cross_game"):
                    core = self.adapter.screen_pair(legs[0], legs[1], budget, why=it.why or None,
                                                    kind="equivalent" if it.kind == "cross_game" else None)
                elif it.kind == "confirmed":
                    rel = next((r for r in self.confirmed_relations() if r.pair_id == it.key), None)
                    if rel is None:
                        self.watch.items.pop(it.key, None)
                        continue
                    core = self.adapter.price(rel, budget)
                else:
                    continue
            except Exception as e:
                self.watch.record(it, "error", None)
                out.append((it, None, f"{type(e).__name__}: {e}"))
                continue
            before = it.last_verdict
            self.watch.record(it, core.verdict, core.naive_edge)
            hit = core.verdict in ("worth_a_look", "look_closely") or (core.naive_edge > 0 and core.verdict != "blocked")
            if hit or core.verdict != before:
                self.suggestions.priced(it.key, legs, core, event="watch_hit" if hit else "watch_change")
            if hit:
                self.alerts.insert(0, (time.time(), it, core))
                del self.alerts[50:]
            if it.key in self.findings:
                self.findings[it.key].core, self.findings[it.key].at = core, time.time()
            if it.key in self.screens:
                self.screens[it.key].core, self.screens[it.key].at = core, time.time()
            out.append((it, core, ""))
        self.watch.save()
        if due:
            self._say(f"Watching {len(self.watch.items)} items; {len(due)} re-checked just now.")
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
            fid = f"set:{legs[0].venue}:{legs[0].event_id}"
            self.suggestions.venue_set(fid, legs, core)
            self._add_finding(fid, legs, core, s.scan, f"{len(legs)} outcomes of one event: " + legs[0].title[:80])

    def price_singles(self, markets: list[Market], s: ScanSummary, budget: float = 300.0) -> None:
        """Yes + No of one market under $1. Needs real books, so only a few markets per scan (spread over categories)."""
        pick, price = getattr(self.adapter, "single_candidates", None), getattr(self.adapter, "price_single", None)
        if pick is None or price is None or self.cfg.max_singles_per_scan <= 0:
            return
        for m in pick(markets, self.cfg.max_singles_per_scan):
            try:
                core = price(m, budget)
            except Exception as e:
                s.notes.append(f"single {m.venue}:{m.id}: {type(e).__name__}: {e}")
                continue
            s.singles += 1
            fid = f"single:{m.venue}:{m.id}"
            self.suggestions.venue_set(fid, [m], core)
            self._add_finding(fid, [m], core, s.scan, m.title[:90])

    def _add_finding(self, fid: str, legs: list[Market], core: CoreResult, scan: str, title: str) -> None:
        t = arbtypes.classify("complement" if len(legs) == 1 else "at_most_one", [l.venue for l in legs], len(legs))
        what = ("The venue lists these as mutually exclusive outcomes of one event, so the prices should add up to $1; "
                "the core priced the set against the order books." if t == arbtypes.MULTI_OUTCOME else
                "Yes and No of one market pay $1 between them; the core priced buying both against the order books.")
        n = structural_narrative(core, what, arbtypes.TYPES[t]["risk"], [l.title for l in legs])
        self.findings[fid] = Finding(fid, t, legs, core, n, legs[0].category or "other", title, scan)
        self._watch(fid, "single" if len(legs) == 1 else "set", t, title, legs, core)

    def check_lines(self, line_a: str, line_b: str, max_pairs: int = 6) -> tuple[list[Case], list[str]]:
        """Links or words for two events -> markets via the core's registry -> judged pairs.
        A link to a multi-outcome event gives several markets: each outcome on A is paired with its
        closest outcome on B (not just the first of each)."""
        sides, notes = [], []
        for i, line in enumerate((line_a, line_b)):
            self._say(f"Reading event {'AB'[i]} from the venue…")
            ms = [m for m in self.adapter.resolve(line) if m.venue != "manifold"]
            notes += list(getattr(self.adapter, "last_notes", []))
            sides.append(ms)
        if not all(sides):
            return [], notes or ["Could not match both inputs to an open market."]
        A, B = sides
        pairs = _match_outcomes(A, B)[:max_pairs]
        self.audit.append("resolve", {"inputs": [line_a, line_b], "matched": [[f"{a.venue}:{a.id}", f"{b.venue}:{b.id}"]
                                                                            for _, a, b in pairs]})
        self._say(f"Asking Gemini about {len(pairs)} matched pair(s) and comparing their rules…")
        judged = self.judge.judge_many([(a, b) for _, a, b in pairs])
        cases = []
        for jp, (sim, a, b) in zip(judged, pairs):
            r = self._rule(a, b, nested=bool(a.event_id and a.event_id == b.event_id))
            self.suggestions.candidate(jp.pair_id, a, b, "user", sim, "user")
            case = self._register(jp, sim, force_review=True, how="user")
            if not r.ok:
                case.rule_note = f"{r.rule}: {r.reason}"
            cases.append(case)
        return cases, notes

    def check_external(self, limit: int = 20) -> tuple[list[Case], list[str]]:
        """prediction.com's arbitrage list (needs PREDICTION_API_KEY on their Dev tier) -> pairs in OUR pipeline.
        Each pair goes through the rules and Gemini, waits for your confirmation, then the core prices it; their
        claimed ROI rides along on the case so the app can show theirs next to ours."""
        from . import predictioncom as pc
        self._say("Asking prediction.com for its current arbitrage list…")
        data = pc.Client().arb(limit=limit)
        cases, notes = [], []
        opps = data.get("opportunities") or []
        for n, o in enumerate(opps, 1):
            self._say(f"Checking prediction.com opportunity {n} of {len(opps)}: {(o.get('group_title') or '')[:50]}…")
            a, b, note = pc.resolve_legs(self.adapter, o)
            if a is None:
                notes.append(f"{(o.get('group_title') or '?')[:60]}: skipped ({note})")
                continue
            case = self.check_pair(a, b)
            case.external = pc.claim(o)
            self.suggestions.log.append("external_claim", {"pair": case.id, **case.external})
            cases.append(case)
        return cases, notes

    def check_pair(self, a: Market, b: Market) -> Case:
        """User-entered pair: always shown for review, even if the judge says unrelated."""
        self._say("Asking Gemini about this pair and comparing its rules…")
        r = self._rule(a, b, nested=bool(a.event_id and a.event_id == b.event_id))
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
        self._say("Core: reading live order books for each leg (slowest step)…")
        try:
            core = self.adapter.price(case.relation, budget)
        except Exception as e:
            case.error = f"{type(e).__name__}: {e}"
            self._move(case, "error", error=case.error)
            return None
        self._say("Core: books walked, fees and guard checks done. Writing the explanation…")
        # Rule differences may only make a trade less likely (the core already blocks on high flags).
        if case.rules and case.rules.diff.overall == "incompatible":
            core = tighten(core, "blocked", "frame_rules_incompatible")
        case.core, case.priced_at = core, time.time()
        kalshi = any(l.venue == "kalshi" for l in case.relation.legs)
        self.audit.append("core_call", {"case": case_id, "budget": budget, "adapter": self.adapter.name,
                                        "verdict": core.verdict, "core_version": core.core_version,
                                        "result": None if kalshi else core.model_dump()})
        self.suggestions.priced(case_id, case.relation.legs, core)
        self._watch(case_id, "confirmed", case.arb_type, case.judged.a.title[:80], case.relation.legs, core)
        self.watch.save()
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


_HOW_RANK = {"cross_venue": 0, "within_event": 1, "cross_event": 2}


def balanced(allowed: list, n: int) -> list:
    """Pick up to n candidate tuples (a, b, sim, how, pid), taking turns across categories so no single
    category fills the whole judging budget. Within a category: cross-venue first, then within-event,
    then most similar."""
    groups: dict[str, list] = {}
    for t in sorted(allowed, key=lambda t: (_HOW_RANK.get(t[3], 3), -t[2])):
        groups.setdefault(t[0].category or "other", []).append(t)
    out: list = []
    while len(out) < n and any(groups.values()):
        for cat in sorted(groups, key=lambda c: -len(groups[c])):   # biggest queue first within each round
            if groups[cat] and len(out) < n:
                out.append(groups[cat].pop(0))
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
