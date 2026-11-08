"""The arbitrage board: every opportunity our own checker holds, as uniform rows.

Three kinds of row, never mixed up on screen:
  verified   a pair a human confirmed after Gemini compared the rules, priced by the core on live books
  screen     a pair matched by CODE (same match on two venues, ladders, exact score => result) and priced by the core
             as a SCREEN: real books, but the rule texts have not been compared, so it is not a verdict yet
  structure  a venue-declared outcome set, or Yes + No of one market: nothing to confirm, priced by the core
Another scanner's claim (prediction.com) rides along on a verified row and is shown beside ours, never merged in.
Every number is read from the core's result; nothing here is computed from prices.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import arbtypes, priority, sports


@dataclass
class Row:
    id: str
    kind: str                       # verified | screen | structure
    title: str
    category: str
    arb_type: str
    venues: list[str]
    verdict: str
    legs: list[dict]                # the core's per-leg quotes (venue, side, price, avg, fee, url, title, depth)
    levels: list[list[list[float]]]
    stages: dict                    # per share, dollars: price_only, at_asks, after_fees, after_risk
    roi: float                      # core: net profit / cost over what the book allows
    profit: float
    cost: float
    approved_profit: float
    approved_cost: float
    apy: float
    days: float
    flags: list[str]
    at: float                       # when priced (epoch seconds)
    league: str | None = None
    live: bool = False
    claim: dict | None = None
    href: str | None = None         # detail page, if there is one
    pair: tuple | None = None       # (market a, market b) for "send to checker"
    score: float = 0.0
    why: list[str] = field(default_factory=list)
    headline: str = ""
    n_outcomes: int = 1
    floor: float = 1.0              # the core's guaranteed payout per basket (e.g. $3 for NO on every outcome of 4)
    saved: bool = False             # True = restored from the last saved scan, not priced in this session

    @property
    def gap_before_costs(self) -> bool:
        return self.stages["price_only"] > 0

    @property
    def real(self) -> bool:
        return self.verdict in ("worth_a_look", "look_closely")


def _stages(core) -> dict:
    return {"price_only": core.naive_edge, "at_asks": core.top_edge, "after_fees": core.avg_edge,
            "after_risk": core.risk_adj_edge}


def _row(core, **kw) -> Row:
    legs = list(core.quotes or [])
    return Row(verdict=core.verdict, legs=legs, levels=list(core.levels or []), stages=_stages(core), roi=core.ret,
               profit=core.net_profit, cost=core.cost, approved_profit=core.approved_profit,
               approved_cost=core.approved_cost, apy=core.ann_ret, days=core.days, flags=list(core.flags),
               headline=core.headline, floor=core.floor or 1.0, **kw)


def rows(p) -> list[Row]:
    out: list[Row] = []
    seen: set[str] = set()
    for c in p.cases.values():                                   # verified: human-confirmed, priced
        if c.core is None:
            continue
        a, b = c.judged.a, c.judged.b
        r = _row(c.core, id=c.id, kind="verified", title=a.title if a.venue == b.venue else f"{a.title}  ↔  {b.title}",
                 category=c.category, arb_type=c.arb_type, venues=sorted({a.venue, b.venue}), at=getattr(c, "priced_at", time.time()),
                 league=(sorted(sports.sport_of(a) | sports.sport_of(b)) or [None])[0],
                 live=sports.in_play(a) or sports.in_play(b), claim=c.external, href=f"/result/{c.id}")
        out.append(r)
        seen.add(c.id)
    for s in p.screens.values():                                 # screens: matched by code, priced as a screen
        if s.id in seen:
            continue
        a, b = s.a, s.b
        r = _row(s.core, id=s.id, kind="screen", title=s.why or f"{a.title}  /  {b.title}", category=a.category or "other",
                 arb_type=arbtypes.classify("equivalent" if s.how == "cross_game" else "a_implies_b", [a.venue, b.venue]),
                 venues=sorted({a.venue, b.venue}), at=getattr(s, "at", time.time()),
                 league=(sorted(sports.sport_of(a) | sports.sport_of(b)) or [None])[0],
                 live=sports.in_play(a) or sports.in_play(b), pair=(a, b))
        r.title = f"{a.title.split(' (')[0]}  ↔  {b.title.split(' (')[0]}" if s.how == "cross_game" else r.title
        out.append(r)
    for f in p.findings.values():                                # structure: sets and Yes + No
        r = _row(f.core, id=f.id, kind="structure", title=f.title, category=f.category, arb_type=f.arb_type,
                 venues=sorted({m.venue for m in f.legs}), at=getattr(f, "at", time.time()),
                 league=(sorted(set().union(*[sports.sport_of(m) for m in f.legs])) or [None])[0],
                 live=any(sports.in_play(m) for m in f.legs), href=f"/finding/{f.id}")
        r.n_outcomes = len(f.legs)
        out.append(r)
    if getattr(p, "snapshot_active", False):
        # Right after a restart, until this session's first scan completes: show the saved rows that live pricing
        # has not replaced yet, marked as saved (with their real age).
        for r in getattr(p, "snapshot", []):
            if r.id not in seen and r.id not in {x.id for x in out}:
                out.append(r)
    for r in out:
        r.score, r.why = score(r)
    return out


def _live_rows(p) -> list[Row]:
    snap, p.snapshot_active = getattr(p, "snapshot_active", False), False
    try:
        return rows(p)
    finally:
        p.snapshot_active = snap


def save(p, path) -> None:
    """Write the board to disk (atomically). Kalshi rows are never written: its Developer Agreement bars storing its data.
    This is a local file for your own use; Polymarket and Novig data must not be redistributed."""
    import json
    from dataclasses import fields
    from pathlib import Path
    out = []
    for r in _live_rows(p):
        if "kalshi" in r.venues or any(q.get("venue") == "kalshi" for q in r.legs):
            continue
        d = {f.name: getattr(r, f.name) for f in fields(r) if f.name != "pair"}
        d["pair"] = [m.model_dump() for m in r.pair] if r.pair else None
        out.append(d)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved_at": time.time(), "rows": out}), encoding="utf-8")
    tmp.replace(path)


def load(path) -> tuple[list[Row], float]:
    """-> (rows, saved_at). A missing or unreadable file is simply an empty board, never an error."""
    import json
    from dataclasses import fields
    from pathlib import Path
    from .schemas import Market
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        names = {f.name for f in fields(Row)}
        rows_ = []
        for d in doc["rows"]:
            pair = d.pop("pair", None)
            r = Row(**{k: v for k, v in d.items() if k in names})
            r.pair = tuple(Market(**m) for m in pair) if pair else None
            r.saved = True
            rows_.append(r)
        return rows_, float(doc.get("saved_at", 0.0))
    except (OSError, ValueError, KeyError, TypeError):
        return [], 0.0


def score(r: Row) -> tuple[float, list[str]]:
    """Ordering score with its reasons (same idea as frame/priority.py, but for board rows)."""
    pts, why = priority.VERDICT_PTS.get(r.verdict, 0), [f"core: {r.verdict.replace('_', ' ')}"]
    pts += max(-10.0, min(30.0, r.stages["after_fees"] * 100 * 5))
    if r.kind == "screen" and r.real:
        pts -= 15
        why.append("screen only: rules not compared")
    if r.gap_before_costs and not r.real:
        pts += 3
        why.append("a price gap exists before costs")
    if r.live:
        pts += 30
        why.append("game in play")
    pts += priority.TYPE_PTS.get(r.arb_type, 0)
    return round(pts, 1), why


def age_text(at: float) -> str:
    s = max(0, int(time.time() - at))
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h"


SORTS = {
    "Best first (recommended)": lambda r: -r.score,
    "Highest verified return": lambda r: -(r.roi if r.real else -1),
    "Most profit ($)": lambda r: -r.profit,
    "Highest annualised": lambda r: -(r.apy if r.real else -1),
    "Most liquidity": lambda r: -r.cost,
    "Event soonest": lambda r: r.days,
    "Newest check": lambda r: -r.at,
}
