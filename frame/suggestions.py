"""Suggestion log: every pair the frame considered, and what happened to it. Append-only and
hash-chained (same format as the audit log), at data/frame/suggestions.jsonl.

Events per pair (suggestion id = pair id):
  candidate   generated (how, similarity, categories)          rule_refused  blocked by a suggestion rule
  deferred    over this scan's LLM budget                      judged        LLM verdict (+ verified?)
  surfaced    shown to a human / filtered                       human         confirmed / rejected / edited
  priced      core verdict on fresh books                       repriced      monitor re-check of a confirmed pair

Kalshi's terms bar storing its data: for any pair with a Kalshi leg, prices and core numbers are
left out of the log (titles, ids, verdicts and reasons only).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .audit import AuditLog
from .rules import RULES_VERSION
from .schemas import CoreResult, Market


def _m(m: Market) -> dict:
    return {"venue": m.venue, "id": m.id, "title": m.title, "category": m.category, "event": m.event_id, "url": m.url}


def _kalshi(*ms: Market) -> bool:
    return any(m.venue == "kalshi" for m in ms)


class SuggestionLog:
    # Logged once per pair, not every scan (a 5-minute monitor would otherwise rewrite the same rows forever).
    _ONCE = ("candidate", "rule_refused", "deferred")

    def __init__(self, path: Path):
        self.log = AuditLog(path)
        self._seen = {(r["kind"], r["payload"]["pair"]) for r in self.log.records() if r["kind"] in self._ONCE}

    def _w(self, event: str, pid: str, **kw) -> None:
        if event in self._ONCE:
            if (event, pid) in self._seen:
                return
            self._seen.add((event, pid))
        self.log.append(event, {"pair": pid, **kw})

    def candidate(self, pid: str, a: Market, b: Market, how: str, similarity: float, scan: str) -> None:
        self._w("candidate", pid, a=_m(a), b=_m(b), how=how, similarity=round(similarity, 4), scan=scan)

    def rule_refused(self, pid: str, a: Market, b: Market, rule: str, reason: str, how: str, scan: str) -> None:
        self._w("rule_refused", pid, a=_m(a), b=_m(b), rule=rule, reason=reason, how=how,
                rules_version=RULES_VERSION, scan=scan)

    def deferred(self, pid: str, scan: str) -> None:
        self._w("deferred", pid, reason="over the LLM budget; judged in a later scan", scan=scan)

    def venue_set(self, sid: str, legs: list[Market], core: CoreResult) -> None:
        """A venue-declared exclusive set priced by the core (no LLM, no pair judgement needed)."""
        body = {"legs": [_m(m) for m in legs], "verdict": core.verdict, "status": core.status,
                "core_version": core.core_version}
        if not _kalshi(*legs):
            body.update(headline=core.headline, naive_edge=core.naive_edge, avg_edge=core.avg_edge)
        self.log.append("venue_set", {"pair": sid, **body})

    def judged(self, pid: str, relation: str, confidence: str, verified: bool, source: str) -> None:
        self._w("judged", pid, relation=relation, confidence=confidence, verified=verified, source=source)

    def surfaced(self, pid: str, shown: bool, rules_overall: str | None = None) -> None:
        self._w("surfaced" if shown else "filtered", pid, rules_overall=rules_overall)

    def human(self, pid: str, action: str, by: str, relation: str | None, note: str = "") -> None:
        self._w("human", pid, action=action, by=by, relation=relation, note=note)

    def priced(self, pid: str, legs: list[Market], core: CoreResult, event: str = "priced") -> None:
        body = {"verdict": core.verdict, "status": core.status, "headline": core.headline,
                "core_version": core.core_version, "flags": core.flags}
        if not _kalshi(*legs):
            body.update(naive_edge=core.naive_edge, avg_edge=core.avg_edge, risk_adj_edge=core.risk_adj_edge,
                        approved_profit=core.approved_profit, approved_cost=core.approved_cost)
        else:
            body.update(headline="(Kalshi leg: numbers not stored)", kalshi_redacted=True)
        self._w(event, pid, **body)

    # ---------------------------------------------------------------- reading
    def records(self) -> list[dict]:
        return self.log.records()

    def latest(self) -> list[dict]:
        """One row per pair: its markets and the most recent thing that happened to it."""
        rows: dict[str, dict] = {}
        for r in self.records():
            p = r["payload"]
            row = rows.setdefault(p["pair"], {"pair": p["pair"]})
            if "a" in p:
                row["a"], row["b"] = p["a"], p["b"]
            row["last"], row["ts"] = r["kind"], r["ts"]
            for k in ("relation", "confidence", "verdict", "reason", "rule", "how", "similarity", "action"):
                if k in p:
                    row[k] = p[k]
        return sorted(rows.values(), key=lambda x: -x["ts"])

    def counts(self) -> Counter:
        return Counter(r["kind"] for r in self.records())
