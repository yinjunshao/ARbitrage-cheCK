"""How long do apparent arbitrages last? Snapshot store + survival analysis.

A collector polls order books every N seconds and records the executable edge of each
pair. An "episode" is an unbroken run of snapshots with a positive edge. Episodes still
open when collection stops are CENSORED: we only know they lasted at least that long.
Kaplan-Meier handles that correctly, so we never overstate (or understate) persistence.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


class SnapshotStore:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS snapshots "
                        "(ts TEXT, pair_id TEXT, edge REAL, shares REAL)")

    def record(self, pair_id: str, ts: datetime, edge: float, shares: float) -> None:
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                        (ts.isoformat(), pair_id, edge, shares))
        self.db.commit()

    def series(self, pair_id: str) -> list[tuple[datetime, float, float]]:
        rows = self.db.execute("SELECT ts, edge, shares FROM snapshots WHERE pair_id=? ORDER BY ts",
                               (pair_id,)).fetchall()
        return [(datetime.fromisoformat(t), e, s) for t, e, s in rows]


@dataclass
class Episode:
    start: datetime
    end: datetime
    duration_s: float
    ongoing: bool       # True = censored (still open when collection stopped)


def episodes(series, poll_s: float, threshold: float = 0.0) -> list[Episode]:
    """Group consecutive profitable snapshots into episodes.

    Resolution is limited by the polling interval, so each episode lasts at least one
    poll: duration = (last - first) + poll_s.
    """
    out: list[Episode] = []
    start = last = None
    for ts, edge, shares in series:
        live = shares > 0 and edge > threshold
        if live:
            if start is None:
                start = ts
            last = ts
        elif start is not None:
            out.append(Episode(start, last, (last - start).total_seconds() + poll_s, False))
            start = None
    if start is not None:
        out.append(Episode(start, last, (last - start).total_seconds() + poll_s, True))
    return out


def kaplan_meier(durations: list[float], observed: list[bool]) -> list[tuple[float, float]]:
    """Survival curve S(t) = share of episodes still alive after t seconds."""
    data = sorted(zip(durations, observed))
    n, i, s = len(data), 0, 1.0
    curve = [(0.0, 1.0)]
    while i < n:
        t, group, deaths = data[i][0], 0, 0
        while i < n and data[i][0] == t:
            group += 1
            deaths += 1 if data[i][1] else 0
            i += 1
        at_risk = n - (i - group)       # episodes whose duration is >= t
        if deaths:
            s *= 1 - deaths / at_risk
            curve.append((t, s))
    return curve


def median_survival(curve: list[tuple[float, float]]) -> float | None:
    for t, s in curve:
        if s <= 0.5:
            return t
    return None
