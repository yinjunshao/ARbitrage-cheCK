"""The smart watch list: things already checked that showed no arbitrage YET, re-checked by the core
(no LLM) on a schedule that tightens as they approach resolution.

Why tighten near the deadline: prices of logically linked markets are pinned hardest close to resolution,
and the research on Polymarket sports found executable gaps concentrated in the final minutes of live play.
Far from the deadline a re-check every few hours is plenty.

    hours to close   > 7 days   2-7 days   1-2 days   2-24 h    < 2 h or game in play
    re-check every     6 h        1 h        20 min     5 min     1 min
Then: a last check within 1 cent of an arbitrage halves the interval again (it is "warm").
Items whose markets have closed drop off.

Stored in data/frame/watchlist.json: ids, titles, links and times only. Prices/edges of Kalshi legs are never
written (its Developer Agreement), Polymarket edges are.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HOUR = 3600.0


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def interval_s(close_iso: str | None, now: float, live: bool = False, last_edge: float | None = None) -> float:
    close = _ts(close_iso)
    left = (close - now) if close else 30 * 24 * HOUR
    if live or left < 2 * HOUR:
        base = 60.0
    elif left < 24 * HOUR:
        base = 5 * 60.0
    elif left < 48 * HOUR:
        base = 20 * 60.0
    elif left < 7 * 24 * HOUR:
        base = HOUR
    else:
        base = 6 * HOUR
    if last_edge is not None and last_edge > -0.01:      # within a cent of an arbitrage: look twice as often
        base = max(30.0, base / 2)
    return base


@dataclass
class WatchItem:
    key: str                      # finding id / pair id
    kind: str                     # set | single | ladder | game_link | confirmed
    arb_type: str
    title: str
    legs: list[dict]              # venue, id, url, title, close_time_utc, start_time_utc, event_id, category, game_id
    close: str | None             # earliest close of the legs
    why: str = ""                 # code-derived relation text (game links)
    category: str = "other"
    added: float = field(default_factory=time.time)
    next_due: float = 0.0
    last_checked: float = 0.0
    checks: int = 0
    last_verdict: str = ""
    last_edge: float | None = None      # price-only gap per share (None for Kalshi legs: never stored)
    best_edge: float | None = None
    live: bool = False

    @property
    def kalshi(self) -> bool:
        return any(l.get("venue") == "kalshi" for l in self.legs)


class WatchList:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: dict[str, WatchItem] = {}
        if self.path.exists():
            try:
                for d in json.loads(self.path.read_text(encoding="utf-8")):
                    self.items[d["key"]] = WatchItem(**d)
            except (ValueError, TypeError, KeyError):
                pass                                   # a bad file never stops the app; it is rebuilt

    def add(self, item: WatchItem, now: float | None = None) -> None:
        now = now or time.time()
        old = self.items.get(item.key)
        if old:                                        # keep history, refresh what the scan knows
            item.checks, item.best_edge, item.last_checked = old.checks, old.best_edge, old.last_checked
            item.added = old.added
        item.next_due = now + interval_s(item.close, now, item.live, item.last_edge)
        self.items[item.key] = item

    def due(self, now: float | None = None, limit: int = 20) -> list[WatchItem]:
        now = now or time.time()
        self.drop_closed(now)
        ready = sorted((i for i in self.items.values() if i.next_due <= now), key=lambda i: i.next_due)
        return ready[:limit]

    def drop_closed(self, now: float) -> None:
        for k in [k for k, i in self.items.items() if (_ts(i.close) or now + 1) < now]:
            del self.items[k]

    def record(self, item: WatchItem, verdict: str, edge: float | None, now: float | None = None) -> None:
        now = now or time.time()
        item.last_checked, item.checks, item.last_verdict = now, item.checks + 1, verdict
        item.last_edge = None if item.kalshi else edge
        if item.last_edge is not None:
            item.best_edge = item.last_edge if item.best_edge is None else max(item.best_edge, item.last_edge)
        item.next_due = now + interval_s(item.close, now, item.live, item.last_edge)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for i in self.items.values():
            d = asdict(i)
            if i.kalshi:
                d["last_edge"] = d["best_edge"] = None
            rows.append(d)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        tmp.replace(self.path)


def leg_dict(m) -> dict:
    return {k: getattr(m, k) for k in ("venue", "id", "url", "title", "close_time_utc", "start_time_utc", "event_id",
                                       "category", "game_id", "league", "sport_type")}
