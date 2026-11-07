"""The ONLY file that imports the numeric core (`arbcheck`, in arbcheck-project/). Everything else
in the frame talks to the `CoreAdapter` protocol, so the core can change behind this file.

Integration point (read from the real code, see docs/CORE_MAP.md):

    arbcheck.pipeline.evaluate_case(markets, relation, guard, now, log=...) -> Case
        Case.arb       ArbResult  (worlds, best basket, order-book walk, per-venue fees, annualised return)
        Case.decision  Decision   (guard: nine checks, OK | WARN | BLOCKED | NO_ARB)

The frame builds the core's `Relation` from what a HUMAN confirmed, and can only add caution:
  * the LLM's rule differences become MismatchFlags (high flags make the guard block);
  * the core's own HeuristicExtractor flags are unioned in (e.g. cross-venue "venue_oracle" risk);
  * an unverified quote marks the relation unverified, which the guard blocks.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .config import DATA, ROOT
from .schemas import CoreCheck, CoreResult, Market, Relation, VerifiedRuleDiff

CORE_DIR = ROOT / "arbcheck-project"


class CoreNotAvailable(RuntimeError):
    pass


class CoreAdapter(Protocol):
    name: str
    demo: bool

    def price(self, relation: Relation, budget: float) -> CoreResult: ...
    def list_open_markets(self, venues: list[str]) -> list[Market]: ...
    def resolve(self, line: str) -> list[Market]: ...
    def compare_rules(self, a: Market, b: Market) -> VerifiedRuleDiff | None: ...


# ------------------------------------------------------------------ mappings (frame <-> arbcheck)

# Frame relation names (what the judge and the human use) -> arbcheck engine kinds.
KIND = {
    "same_event": "equivalent",
    "a_implies_b": "a_implies_b",
    "b_implies_a": "b_implies_a",
    "mutually_exclusive": "mutually_exclusive",
    "exhaustive_set": "partition",      # frame defines it as "exactly one is Yes"
    "unrelated": "none",
    "unclear": "none",
}
TOPIC = {"data_source": "source", "yes_definition": "definition", "time_zone": "timezone",
         "deadline": "deadline_semantics", "tie_break": "tie_handling", "other": "definition"}
SEVERITY = {"blocker": "high", "material": "medium", "cosmetic": "low"}
VERDICT = {"OK": "worth_a_look", "WARN": "look_closely", "BLOCKED": "blocked", "NO_ARB": "nothing_to_do"}
# The core guard needs confidence >= 0.75 by default. A human-edited relation is asserted at the minimum.
CONFIDENCE = {"low": 0.5, "medium": 0.75, "high": 0.9}
HUMAN_ASSERTED = 0.75


def tighten(core: CoreResult, verdict: str, flag: str) -> CoreResult:
    """The frame may only escalate caution, never loosen. nothing_to_do (no gap) is left alone."""
    rank = {"worth_a_look": 0, "look_closely": 1, "blocked": 2}
    if core.verdict in rank and rank[verdict] > rank[core.verdict]:
        return core.model_copy(update={"verdict": verdict, "flags": core.flags + [flag]})
    return core


# ------------------------------------------------------------------ arbcheck adapter

def _import_core():
    if str(CORE_DIR) not in sys.path:
        sys.path.insert(0, str(CORE_DIR))
    try:
        import arbcheck  # noqa: F401
    except ImportError as e:
        raise CoreNotAvailable(f"arbcheck not importable from {CORE_DIR}: {e}") from e
    return arbcheck


class ArbcheckAdapter:
    """Wraps Debby's `arbcheck` core. demo=True uses its invented market universe (offline)."""
    name = "arbcheck"

    def __init__(self, demo: bool = True, kalshi_member: bool = False, log_path: Path | None = None,
                 live_limit: int = 200):
        ac = _import_core()
        from arbcheck import demo_data, fees, guard, models, pipeline, relations, venues
        self.ac, self.models, self.fees, self.guard_mod = ac, models, fees, guard
        self.relations, self.pipeline, self.demo_data = relations, pipeline, demo_data
        self.version = f"arbcheck {ac.__version__}"
        self.demo, self.kalshi_member, self.live_limit = demo, kalshi_member, live_limit
        self.log_path = Path(log_path) if log_path else DATA / "core_decisions.jsonl"
        self._core: dict[str, object] = {}   # "venue:id" -> arbcheck Market (keeps the full order books)
        self._tags: dict[str, list[str]] = {}
        self._event_tags: dict[str, list[str]] = {}
        self.max_events, self.max_per_event = 200, 40
        self.last_notes: list[str] = []
        self.progress = lambda text: None   # the pipeline sets this to report listing progress
        if demo:
            universe = demo_data.demo_markets()
            self.registry = venues.Registry([demo_data.DemoVenue(n, universe) for n in ("Polymarket", "Kalshi", "Manifold")])
            self._universe = universe
        else:
            from arbcheck.compliance import ReadOnlyClient
            self.client = ReadOnlyClient()
            self.registry = venues.build_registry(self.client, kalshi_member, loopback=True)
            self._universe = None

    # -- time ----------------------------------------------------------------------
    def now(self) -> datetime:
        return self.demo_data.DEMO_NOW if self.demo else datetime.now(timezone.utc)

    # -- arbcheck Market <-> frame Market --------------------------------------------
    def _key(self, venue: str, mid: str) -> str:
        return f"{venue.lower()}:{mid}"

    def to_frame(self, cm, tags: list[str] | None = None) -> Market:
        """arbcheck Market -> frame Market. `tags` = the venue's own event tags / category, if known."""
        from .categories import canonical
        self._core[self._key(cm.venue, cm.id)] = cm
        tags = tags if tags is not None else self._tags.get(self._key(cm.venue, cm.id), [])
        self._tags[self._key(cm.venue, cm.id)] = tags
        return Market(
            venue=cm.venue.lower() if cm.venue.lower() in ("polymarket", "kalshi", "manifold") else "manual",
            id=cm.id, event_id=cm.group or None, title=cm.question, rules_text=cm.rules,
            close_time_utc=cm.end.isoformat(), category=canonical(tags), tags=list(tags),
            yes_bid=cm.yes.best_bid, yes_ask=cm.yes.best_ask, no_bid=cm.no.best_bid, no_ask=cm.no.best_ask,
            url=cm.url or None,
        )

    def cheap_link(self, a: Market, b: Market) -> bool:
        """The core's deterministic check (nested deadlines, thresholds, same question). No LLM.
        Used to pick which pairs INSIDE one event are worth sending to the judge."""
        ca, cb = self._core.get(self._key(a.venue, a.id)), self._core.get(self._key(b.venue, b.id))
        if ca is None or cb is None:
            return False
        # A venue-declared multi-outcome set (Polymarket negRisk, Kalshi mutually_exclusive) is priced as a SET
        # by price_set(); its outcomes are not sent to the judge in pairs.
        if ca.meta.get("exclusive") and cb.meta.get("exclusive") and ca.group and ca.group == cb.group:
            return False
        x, y = (ca, cb) if ca.end <= cb.end else (cb, ca)
        # Only nested deadlines / thresholds. The heuristic calls "Will X win?" vs "Will Y win?" equivalent
        # (only the name differs), which is wrong for outcomes of one event, so "equivalent" is not a link here.
        return self.relations.HeuristicExtractor().extract(x, y).kind in ("a_implies_b", "b_implies_a")

    def exclusive_sets(self, markets: list[Market], max_sets: int, max_size: int = 12) -> list[list[Market]]:
        """Venue-declared mutually exclusive events (no LLM needed), largest volume first, small enough to price."""
        groups: dict[tuple[str, str], list[Market]] = {}
        for m in markets:
            cm = self._core.get(self._key(m.venue, m.id))
            if cm is not None and cm.meta.get("exclusive") and m.event_id:
                groups.setdefault((m.venue, m.event_id), []).append(m)
        sets = [g for g in groups.values() if 3 <= len(g) <= max_size]
        return sets[:max_sets]

    def price_set(self, legs: list[Market], budget: float) -> CoreResult:
        """Price a venue-declared exclusive set with the core's structural relation (at most one Yes)."""
        cms = [self._to_core(m) for m in legs]
        rel = self.relations.structural_relation(
            "at_most_one", "The venue lists these as mutually exclusive outcomes of one event.", "metadata", 0.9,
            "venue-metadata")
        g = self.guard_mod.Guard(self.guard_mod.Limits(max_notional=budget, max_total_notional=max(budget, 1000.0)))
        kalshi_live = not self.demo and any(m.venue == "Kalshi" for m in cms)
        log = None if kalshi_live else self.guard_mod.DecisionLog(str(self.log_path))
        case = self.pipeline.evaluate_case(cms, rel, g, self.now(), log=log, role="related", note="venue set")
        return self._from_case(case)

    def _to_core(self, m: Market):
        """The core Market for a frame leg: the registered one, else built from a manual entry."""
        cm = self._core.get(self._key(m.venue, m.id))
        if cm is None and not self.demo and m.url and m.venue != "manual":
            self.resolve(m.url)            # e.g. a confirmed relation re-priced after a restart
            cm = self._core.get(self._key(m.venue, m.id))
            if cm is None:
                raise ValueError(f"{m.venue} market {m.id} is no longer open or could not be found.")
        if cm is not None:
            if not self.demo and cm.tradeable:
                # Always fresh order books when pricing (the core's client caches for 60 s).
                self.registry.venues[cm.venue].hydrate(cm)
            return cm
        if m.yes_ask is None or m.close_time_utc is None or not m.depth:
            raise ValueError(f"Manual leg '{m.title}' needs a Yes ask, a Yes bid, a depth and a close time.")
        bid = m.yes_bid if m.yes_bid is not None else max(0.0, m.yes_ask - 0.01)
        yes, no = self.demo_data.binary([(bid, m.depth)], [(m.yes_ask, m.depth)])
        end = datetime.fromisoformat(m.close_time_utc.replace("Z", "+00:00"))
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        fee = {"polymarket": self.fees.polymarket_fee(None), "kalshi": self.fees.KALSHI_FEE}.get(
            m.venue, self.fees.Fee("Manual entry: assumed 0.07 × price × (1 − price)", k=0.07))
        return self.models.Market(m.id, m.title, m.rules_text or m.title, end, yes, no,
                                  venue=m.venue.capitalize(), url=m.url or "", fee=fee)

    # -- discovery + input ------------------------------------------------------------
    def list_open_markets(self, venues: list[str]) -> list[Market]:
        """Open markets with their venue categories. Live data goes only through arbcheck's
        ReadOnlyClient (allowlisted GETs, rate-spaced, cached) and arbcheck's own parsers."""
        wanted = {v.lower() for v in venues}
        if self.demo:  # test-only invented universe: one shared category so the type rule passes
            return [self.to_frame(m, ["other"]) for m in self._universe if m.venue.lower() in wanted]
        self.last_notes = []
        out: list[Market] = []
        if "polymarket" in wanted:
            out += self._polymarket_events()
        if "kalshi" in wanted:
            if self.kalshi_member:
                out += self._kalshi_events()
            else:
                self.last_notes.append("Kalshi is off (its Developer Agreement). If you are a Kalshi member, set "
                                       "FRAME_KALSHI_MEMBER=1 in .env and restart.")
        return out

    def _polymarket_events(self) -> list[Market]:
        from arbcheck import polymarket
        out, offset, page = [], 0, 25
        while offset < self.max_events:
            try:
                events = self.client.get(f"{polymarket.GAMMA}/events", {
                    "closed": "false", "active": "true", "limit": page, "offset": offset,
                    "order": "volume24hr", "ascending": "false"})
            except Exception as e:  # one venue failing never stops the scan
                if "too large" in str(e) and page > 1:   # arbcheck caps responses at 5 MB: smaller pages
                    page //= 2
                    continue
                self.last_notes.append(f"Polymarket events (offset {offset}): {e}")
                break
            if not isinstance(events, list) or not events:
                break
            for ev in events:
                tags = [t.get("slug") or t.get("label") for t in (ev.get("tags") or []) if isinstance(t, dict)]
                self._event_tags[ev.get("slug", "")] = tags
                for cm in polymarket.stubs_from_event(ev)[: self.max_per_event]:
                    out.append(self.to_frame(cm, tags))
            offset += len(events)
            self.progress(f"Polymarket: {offset} of {self.max_events} events read, {len(out):,} markets so far…")
        return out

    def _kalshi_events(self) -> list[Market]:
        from arbcheck import kalshi
        out, cursor, seen = [], "", 0
        while seen < self.max_events:
            params = {"status": "open", "with_nested_markets": "true", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            try:
                data = self.client.get(f"{kalshi.BASE}/events", params)
            except Exception as e:
                self.last_notes.append(f"Kalshi events: {e}")
                break
            for ev in data.get("events") or []:
                tags = [ev["category"]] if ev.get("category") else []
                self._event_tags[ev.get("event_ticker", "")] = tags
                for raw in (ev.get("markets") or [])[: self.max_per_event]:
                    cm = kalshi.market_from(raw, ev)
                    if cm:
                        out.append(self.to_frame(cm, tags))
                seen += 1
            self.progress(f"Kalshi: {seen} of {self.max_events} events read, {len(out):,} markets so far…")
            cursor = data.get("cursor") or ""
            if not cursor:
                break
        return out

    def _event_tags_for(self, cm) -> list[str]:
        """Category for a market found by link/search: look its event up once (cached)."""
        ev = cm.meta.get("event_slug") or cm.group or ""
        if not ev or self.demo:
            return []
        if ev not in self._event_tags:
            try:
                if cm.venue == "Polymarket":
                    from arbcheck import polymarket
                    evs = self.client.get(f"{polymarket.GAMMA}/events", {"slug": ev})
                    e = evs[0] if isinstance(evs, list) and evs else {}
                    self._event_tags[ev] = [t.get("slug") or t.get("label") for t in (e.get("tags") or [])]
                elif cm.venue == "Kalshi":
                    from arbcheck import kalshi
                    e = (self.client.get(f"{kalshi.BASE}/events/{ev}") or {}).get("event") or {}
                    self._event_tags[ev] = [e["category"]] if e.get("category") else []
                else:
                    self._event_tags[ev] = []
            except Exception:
                self._event_tags[ev] = []
        return self._event_tags[ev]

    def resolve(self, line: str) -> list[Market]:
        """A link or words -> matched markets, via the core's Registry (never fetches arbitrary URLs)."""
        res = self.registry.resolve(line)
        self.last_notes = list(res.notes) + list(self.registry.notes)
        if res.kind == "unreadable" and "kalshi.com" in line.lower() and not self.kalshi_member:
            self.last_notes = ["That is a Kalshi link, and Kalshi is switched off. Kalshi's Developer Agreement limits "
                               "its data to a member's own trading, so it only runs if you start with --kalshi-member "
                               "(run.bat kalshi)."]
        return [self.to_frame(m.market, self._event_tags_for(m.market)) for m in res.matches]

    def compare_rules(self, a: Market, b: Market) -> VerifiedRuleDiff | None:
        # arbcheck's LLMExtractor is Anthropic-shaped and returns relation + flags in one call; the frame's
        # Gemini rulediff covers the same ground with per-topic verified quotes, so it is used instead.
        return None

    # -- pricing ------------------------------------------------------------------------
    def core_relation(self, rel: Relation, a, b):
        R = self.relations
        flags = [R.MismatchFlag(TOPIC[d.topic], SEVERITY[d.severity], d.quote_a, d.quote_b, d.explanation)
                 for d in rel.rule_differences]
        kind = KIND[rel.relation]
        # Union the core's own deterministic flags (kinds the LLM did not already raise). Its heuristic
        # compares same-event / nested wording, so it only applies to those kinds; the cross-venue
        # "who settles it" flag (venue_oracle) applies to every cross-venue pair, as in the core.
        # NOTE: uses HeuristicExtractor._flags (private): see docs/CORE_REQUESTS.md.
        if b is not None:
            have = {f.kind for f in flags}
            sa = R._first_sentence(a.rules) or a.question
            sb = R._first_sentence(b.rules) or b.question
            core_flags = R.HeuristicExtractor()._flags(a, b, kind, sa, sb)
            if kind not in ("equivalent", "a_implies_b", "b_implies_a"):
                core_flags = [f for f in core_flags if f.kind == "venue_oracle"]
            flags += [f for f in core_flags if f.kind not in have]
        human = rel.edited or not rel.evidence_verified
        conf = HUMAN_ASSERTED if rel.edited else CONFIDENCE.get(rel.confidence or "low", 0.5)
        problems = [] if rel.rules_verified else ["a quoted rule clause was not found verbatim"]
        why = (f"Confirmed by {rel.confirmed_by}" + (f" (changed from {rel.proposed})" if rel.edited else "")
               + (f": {rel.note}" if rel.note else "."))
        return R.Relation(kind, conf, flags, why, "frame:gemini+human",
                          evidence_a=(rel.evidence_a or [""])[0], evidence_b=(rel.evidence_b or [""])[0],
                          verified=rel.rules_verified, problems=problems, basis="human" if human else "text")

    def price(self, relation: Relation, budget: float) -> CoreResult:
        legs = [self._to_core(m) for m in relation.legs]
        a, b = legs[0], (legs[1] if len(legs) > 1 else None)
        crel = self.core_relation(relation, a, b)
        limits = self.guard_mod.Limits(max_notional=budget, max_total_notional=max(budget, 1000.0))
        g = self.guard_mod.Guard(limits)
        # Kalshi's terms bar storing its data: no core decision log when a live Kalshi leg is involved.
        kalshi_live = not self.demo and any(m.venue == "Kalshi" for m in legs)
        log = None if kalshi_live else self.guard_mod.DecisionLog(str(self.log_path))
        if log:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        case = self.pipeline.evaluate_case(legs, crel, g, self.now(), log=log, role="yours",
                                           note=f"frame pair {relation.pair_id}")
        return self._from_case(case)

    def _from_case(self, case) -> CoreResult:
        arb, d = case.arb, case.decision
        legs = [f"{lab} {side}" for lab, side in (arb.legs or ())]
        return CoreResult(
            verdict=VERDICT[d.status], status=d.status, headline=d.headline, kind=arb.kind, legs=legs,
            floor=arb.floor, naive_edge=arb.naive_edge, top_edge=arb.top_edge, avg_edge=arb.avg_edge,
            shares=arb.shares, cost=arb.cost, fee_cost=arb.fee_cost, net_profit=arb.net_profit,
            ret=arb.ret, ann_ret=arb.ann_ret, days=arb.days,
            approved_shares=d.approved_shares, approved_cost=d.approved_cost, approved_profit=d.approved_profit,
            haircut_per_share=d.haircut_per_share, risk_adj_edge=d.risk_adj_edge, risk_adj_ann=d.risk_adj_ann,
            clipped=d.clipped,
            checks=[CoreCheck(name=c.name, status=c.status, detail=c.detail) for c in d.checks],
            flags=[f"{f.kind}:{f.severity}" for f in case.relation.flags],
            fees={leg: f.name for leg, f in zip(legs, arb.fees)},
            scenarios=[{"world": list(s["world"]), "payout": s["payout"], "profit": s["profit"]} for s in arb.scenarios],
            core_version=self.version,
        )


# ------------------------------------------------------------------ fake core (unit tests only)

class FakeCoreAdapter:
    """Fixed placeholder results so frame unit tests never depend on the core.
    NOT computed from prices; `core_version` FAKE-0 and the `fake_core` flag say so."""
    name = "fake"
    demo = True

    def __init__(self, markets: list[Market] | None = None):
        self._markets = markets or []

    def price(self, relation: Relation, budget: float) -> CoreResult:
        checks = [CoreCheck(name="Rules match", status="warn", detail="placeholder"),
                  CoreCheck(name="Enough depth", status="pass", detail="placeholder")]
        if relation.relation in ("unrelated", "unclear"):
            return CoreResult(verdict="nothing_to_do", status="NO_ARB", headline="No logical link.", kind="none",
                              flags=["fake_core"], checks=checks, core_version="FAKE-0")
        return CoreResult(
            verdict="look_closely", status="WARN", headline="Check the rule differences below first.",
            kind=KIND[relation.relation], legs=["A YES", "B NO"], floor=1.0, naive_edge=0.042, top_edge=0.03,
            avg_edge=0.021, shares=850.0, cost=300.0, fee_cost=4.5, net_profit=6.3, ret=0.021, ann_ret=0.142,
            days=54.0, flags=["fake_core", "definition:medium"], checks=checks, core_version="FAKE-0",
        )

    def list_open_markets(self, venues: list[str]) -> list[Market]:
        return [m for m in self._markets if m.venue in venues]

    def resolve(self, line: str) -> list[Market]:
        return [m for m in self._markets if m.url and m.url.rstrip("/") == line.strip().rstrip("/")]

    def compare_rules(self, a: Market, b: Market) -> VerifiedRuleDiff | None:
        return None


def get_adapter(kind: str, demo: bool = True, kalshi_member: bool = False,
                markets: list[Market] | None = None) -> CoreAdapter:
    if kind == "fake":
        return FakeCoreAdapter(markets)
    return ArbcheckAdapter(demo=demo, kalshi_member=kalshi_member)
