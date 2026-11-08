"""Nested-window links ACROSS events, derived by code (no LLM).

"Will Bitcoin reach $92,000 November 2-8?" implies "Will Bitcoin reach $92,000 in November?", which implies
"Will Bitcoin reach $92,000 by December 31, 2026?": hitting a price inside a window means hitting it inside every
window that contains it. Polymarket lists these as SEPARATE events (day / week / month / year), so neither the
core's extractor (same wording only) nor within-event pairing finds them, yet they are the textbook
'combinatorial' arbitrage (Saguillo et al.).

A link is made only when ALL of these hold, else no link:
  * same asset, same direction (reach/high vs dip/low), same threshold;
  * both windows can be dated: day ("on November 7"), week ("November 2-8"), month ("in November"), or
    "by <date>" when the window's START is known (the market's creation: a "by" market created after the
    hit would not count it, the Rubio-ladder problem);
  * window A lies inside window B (B starts no later and ends no earlier).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .schemas import Market

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
_T = re.compile(r"^will (?:the price of )?(?P<asset>.+?) (?P<dir>reach|dip to|hit \(high\)|hit \(low\)) "
                r"\$(?P<x>[\d,\.]+)\s*(?P<k>[kKmM])?(?P<rest>.*?)\??$", re.I)
_DAY = re.compile(r"^on (?P<m>[a-z]+) (?P<d>\d{1,2})$", re.I)
_WEEK = re.compile(r"^(?P<m>[a-z]+) (?P<d1>\d{1,2})\s*[-–]\s*(?P<d2>\d{1,2})$", re.I)
_MONTH = re.compile(r"^in (?P<m>[a-z]+)$", re.I)
_BY = re.compile(r"^(?:by|before) (?P<rest>.+)$", re.I)


def _close(m: Market) -> datetime | None:
    if not m.close_time_utc:
        return None
    d = datetime.fromisoformat(m.close_time_utc.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse(m: Market, starts: dict[str, str] | None = None) -> tuple | None:
    """-> (asset, direction, threshold, window_start, window_end) or None if the market can't be read safely."""
    t = _T.match(m.title.strip())
    end = _close(m)
    if not t or end is None:
        return None
    asset = re.sub(r"\(.*?\)", "", t["asset"]).strip().lower()
    direction = "up" if t["dir"].lower() in ("reach", "hit (high)") else "down"
    x = float(t["x"].replace(",", "")) * {"k": 1e3, "m": 1e6}.get((t["k"] or "").lower(), 1)
    rest = t["rest"].strip().rstrip("?").strip()
    year = end.year
    try:
        if (w := _DAY.match(rest)) and w["m"].lower() in MONTHS:
            start = datetime(year, MONTHS[w["m"].lower()], int(w["d"]), tzinfo=timezone.utc)
        elif (w := _WEEK.match(rest)) and w["m"].lower() in MONTHS:
            start = datetime(year, MONTHS[w["m"].lower()], int(w["d1"]), tzinfo=timezone.utc)
        elif (w := _MONTH.match(rest)) and w["m"].lower() in MONTHS:
            mo = MONTHS[w["m"].lower()]
            start = datetime(year if mo <= end.month else year - 1, mo, 1, tzinfo=timezone.utc)
        elif _BY.match(rest):
            s = (starts or {}).get(m.event_id or "")
            if not s:
                return None                  # unknown start: a 'by' window could miss an earlier hit
            start = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            return None
    except ValueError:
        return None
    # Day/week/month windows are written in US Eastern time; allow 12 h of slack on the start so a time-zone
    # offset never makes a window look wider than it is (being stricter only removes links).
    return asset, direction, x, start - timedelta(hours=12), end


def window_links(markets: list[Market], starts: dict[str, str] | None = None) -> list[tuple[Market, Market, str]]:
    """(narrower-window market, wider-window market, why) for every nested pair across DIFFERENT events."""
    parsed = [(m, p) for m in markets if (p := parse(m, starts))]
    groups: dict[tuple, list] = {}
    for m, p in parsed:
        groups.setdefault((p[0], p[1], p[2]), []).append((m, p))
    out = []
    for (asset, direction, x), ms in groups.items():
        for a, pa in ms:
            for b, pb in ms:
                if a is b or (a.event_id and a.event_id == b.event_id):
                    continue
                if pb[3] <= pa[3] and pa[4] <= pb[4]:          # A inside B: B starts no later, ends no earlier
                    verb = "reaching" if direction == "up" else "dipping to"
                    out.append((a, b, f"{asset.title()} {verb} ${x:,.0f} in the shorter window "
                                      f"({a.title.split('$')[-1].split(' ', 1)[-1].rstrip('?')}) means it did so in the "
                                      f"longer one ({b.title.split('$')[-1].split(' ', 1)[-1].rstrip('?')})."))
    return out
