"""Smart ordering: one score per item, with the reasons that produced it (shown in the app).

What makes something worth a person's attention FIRST, in order of weight:
  1. what the core said, if it has priced it (a real gap after costs beats everything);
  2. how soon it resolves (the money comes back sooner, and prices are pinned hardest near the deadline;
     a game in play is the extreme case);
  3. how trustworthy the link is (Gemini's confidence, verified quotes, no blocking rule differences);
  4. the kind of arbitrage (cross-platform gaps are the most common real ones; a date ladder only pays if
     the earlier date is priced above the later one, which is rare);
  5. a watch-list item that has come close to a gap before ("warm").
These are ORDERING weights, not prices: every number in a verdict still comes from the core.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import arbtypes, sports

VERDICT_PTS = {"worth_a_look": 100, "look_closely": 60, "blocked": 5, "nothing_to_do": 0}
CONF_PTS = {"high": 25, "medium": 12, "low": 0}
TYPE_PTS = {arbtypes.CROSS_PLATFORM: 15, arbtypes.MULTI_OUTCOME: 8, arbtypes.LOGICAL: 4, arbtypes.SAME_MARKET: 2}


def _hours_left(*closes: str | None) -> float | None:
    now = datetime.now(timezone.utc)
    hs = []
    for c in closes:
        if not c:
            continue
        try:
            d = datetime.fromisoformat(c.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            continue
        d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        hs.append((d - now).total_seconds() / 3600)
    return min(hs) if hs else None


def _urgency(hours: float | None, live: bool) -> tuple[float, str]:
    if live:
        return 30, "game in play"
    if hours is None:
        return 0, ""
    if hours < 6:
        return 25, "resolves within 6 h"
    if hours < 48:
        return 18, "resolves within 2 days"
    if hours < 7 * 24:
        return 10, "resolves this week"
    if hours < 30 * 24:
        return 4, "resolves this month"
    return 0, ""


def _core_pts(core) -> tuple[float, list[str]]:
    if core is None:
        return 0, []
    pts = VERDICT_PTS.get(core.verdict, 0)
    why = [f"core: {core.verdict.replace('_', ' ')}"]
    edge_c = core.avg_edge * 100                      # cents per share after costs
    pts += max(-10.0, min(30.0, edge_c * 5))
    if core.naive_edge > 0 >= core.avg_edge:
        pts += 3
        why.append("a price gap exists before costs")
    return pts, why


def score_case(c) -> tuple[float, list[str]]:
    """A judged pair (awaiting confirmation or priced)."""
    v = c.judged.verdict
    a, b = c.judged.a, c.judged.b
    pts, why = _core_pts(c.core)
    pts += CONF_PTS.get(v.confidence, 0)
    why.append(f"{v.confidence} confidence")
    if c.judged.verified:
        pts += 5
    else:
        pts -= 10
        why.append("quotes not verified")
    if c.rules:
        sev = {d.severity for d in c.rules.diff.differences}
        if c.rules.diff.overall == "incompatible" or "blocker" in sev:
            pts -= 30
            why.append("a rule difference blocks it")
        elif "material" in sev:
            pts -= 10
            why.append("material rule difference")
    t = c.arb_type
    pts += TYPE_PTS.get(t, 0)
    why.append(arbtypes.label(t))
    u, uw = _urgency(_hours_left(a.close_time_utc, b.close_time_utc), sports.in_play(a) or sports.in_play(b))
    pts += u
    if uw:
        why.append(uw)
    if c.how == "game_link" or c.how == "within_event":
        why.append("screened by the core first")
    pts += 5 * c.similarity
    return round(pts, 1), why


def score_finding(f) -> tuple[float, list[str]]:
    pts, why = _core_pts(f.core)
    pts += TYPE_PTS.get(f.arb_type, 0)
    u, uw = _urgency(_hours_left(*[l.close_time_utc for l in f.legs]), any(sports.in_play(l) for l in f.legs))
    pts += u
    if uw:
        why.append(uw)
    return round(pts, 1), why


def score_watch(it) -> tuple[float, list[str]]:
    pts, why = 0.0, []
    if it.last_edge is not None:
        pts += max(-10.0, min(30.0, it.last_edge * 100 * 5))
        if it.last_edge > -0.01:
            pts += 10
            why.append("within 1¢ of a gap last time")
    u, uw = _urgency(_hours_left(it.close), it.live)
    pts += u
    if uw:
        why.append(uw)
    return round(pts, 1), why
