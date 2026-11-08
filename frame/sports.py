"""Sports support (focus: the Premier League).

Why sports: games resolve within hours (the shortest lock-up there is), and the research on Polymarket sports
(Cheng et al., arXiv:2605.00864, NBA) found 279 of 290 executable combinatorial arbitrage episodes happened
IN-GAME, mostly in the final minutes, with a median life of 16 seconds. So:
  * games are pulled separately (adapter._sports_events) and watched every minute while live (watch.py);
  * links INSIDE one game are derived by code, not the LLM: an exact score fixes the full-time result
    ("Exact score 0-1" implies "Liverpool win"), so P(exact score) can never exceed P(that result);
  * live state comes first from Polymarket's own game events (live, score, period, elapsed: same allowlisted
    host as everything else). Two optional extra feeds, both OFF unless set in .env:
      FOOTBALL_DATA_API_KEY=...   football-data.org (official; the free tier has DELAYED scores, live needs a paid plan)
      FRAME_SPORTS_ESPN=1         ESPN's public scoreboard JSON (live, but unofficial: no published terms; personal use)
Honest limit: polling every 60 s cannot capture 16-second episodes. This finds and explains them; capturing them
would need the venues' WebSocket feeds.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .schemas import Market

GAME_LENGTH = timedelta(hours=2, minutes=15)      # kickoff to final whistle, with stoppage and half-time

# Sports you can tick in the app (Polymarket tag slug -> label). Games of ticked sports are pulled separately.
LEAGUES = {
    "premier-league": "Premier League", "champions-league": "Champions League", "la-liga": "La Liga",
    "bundesliga": "Bundesliga", "serie-a": "Serie A", "nba": "NBA", "nfl": "NFL", "mlb": "MLB", "nhl": "NHL",
    "ufc": "UFC", "tennis": "Tennis", "f1": "Formula 1", "cricket": "Cricket",
}
_SPORT_TAGS = {"premier-league": {"premier-league", "epl"}, "champions-league": {"champions-league", "ucl"}}


def sport_of(m: Market) -> set[str]:
    """Which of LEAGUES a market belongs to (from its league or its venue tags). Empty = can't tell."""
    tags = {(t or "").lower() for t in (m.tags or [])} | ({m.league} if m.league else set())
    return {k for k in LEAGUES if tags & _SPORT_TAGS.get(k, {k})}

_EXACT = re.compile(r"exact score:\s*(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+?)\?*$", re.I)
_WIN = re.compile(r"will (.+?) win\b", re.I)
_DRAW = re.compile(r"end in a draw", re.I)


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def in_play(m: Market, now: datetime | None = None) -> bool:
    k = _dt(m.start_time_utc)
    now = now or datetime.now(timezone.utc)
    return bool(k and k <= now <= k + GAME_LENGTH)


def minutes_to_kickoff(m: Market, now: datetime | None = None) -> float | None:
    k = _dt(m.start_time_utc)
    return None if k is None else (k - (now or datetime.now(timezone.utc))).total_seconds() / 60


def _norm(team: str) -> str:
    return re.sub(r"\b(fc|afc|cf)\b", "", team.lower()).strip(" .")


def game_links(markets: list[Market]) -> list[tuple[Market, Market, str]]:
    """Deterministic logical links inside one game: (exact-score market, full-time result market, why).
    Relation: the exact score IMPLIES the result, so the core prices them as a_implies_b."""
    by_game: dict[str, list[Market]] = {}
    for m in markets:
        if m.game_id:
            by_game.setdefault(m.game_id, []).append(m)
    out = []
    for ms in by_game.values():
        results: dict[str, Market] = {}
        for m in ms:
            if m.sport_type != "moneyline":
                continue
            if _DRAW.search(m.title):
                results["draw"] = m
            elif (w := _WIN.search(m.title)):
                results[_norm(w.group(1))] = m
        for m in ms:
            if m.sport_type != "soccer_exact_score" or not (e := _EXACT.search(m.title)):
                continue
            home, hg, ag, away = _norm(e.group(1)), int(e.group(2)), int(e.group(3)), _norm(e.group(4))
            key = "draw" if hg == ag else (home if hg > ag else away)
            if key in results:
                out.append((m, results[key], f"An exact score of {hg}-{ag} means "
                                             f"{'a draw' if key == 'draw' else results[key].title.split('?')[0].replace('Will ', '') }."))
    return out


# ------------------------------------------------------------------ optional external score feeds

_ALLOWED = {"api.football-data.org": ("/v4/competitions/",), "site.api.espn.com": ("/apis/site/v2/sports/soccer/",)}
_GAP = {"api.football-data.org": 6.5, "site.api.espn.com": 5.0}   # football-data free tier: 10 requests a minute


class _Client:
    """GET-only, allowlisted, rate-spaced, 30 s cache: the same rules as the core's ReadOnlyClient."""

    def __init__(self):
        self._last: dict[str, float] = {}
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, url: str, headers: dict | None = None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not any(parts.path.startswith(p) for p in _ALLOWED.get(parts.hostname or "", ())):
            raise PermissionError(f"not on the sports allowlist: {url}")
        with self._lock:
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < 30:
                return hit[1]
            wait = self._last.get(parts.hostname, 0) + _GAP[parts.hostname] - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last[parts.hostname] = time.time()
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "false-arb-checker",
                                                       **(headers or {})}, method="GET")
            with urllib.request.urlopen(req, timeout=10) as r:      # noqa: S310 (allowlisted above)
                data = json.loads(r.read(2_000_000).decode("utf-8"))
            self._cache[url] = (time.time(), data)
            return data


_client = _Client()


def external_scores() -> tuple[list[dict], list[str]]:
    """Premier League games from the optional feeds. Returns (games, notes). Empty when no feed is switched on."""
    games, notes = [], []
    key = os.getenv("FOOTBALL_DATA_API_KEY")
    if key:
        try:
            d = _client.get("https://api.football-data.org/v4/competitions/PL/matches?status=LIVE,IN_PLAY,PAUSED,SCHEDULED"
                            f"&dateFrom={datetime.now(timezone.utc):%Y-%m-%d}&dateTo={datetime.now(timezone.utc) + timedelta(days=2):%Y-%m-%d}",
                            {"X-Auth-Token": key})
            for g in d.get("matches", []):
                ft = (g.get("score") or {}).get("fullTime") or {}
                games.append({"title": f"{g['homeTeam']['name']} vs. {g['awayTeam']['name']}", "start": g.get("utcDate"),
                              "status": g.get("status"), "score": f"{ft.get('home')}-{ft.get('away')}" if ft.get("home") is not None else None,
                              "minute": g.get("minute"), "events": [], "source": "football-data.org (free tier: delayed)"})
        except Exception as e:
            notes.append(f"football-data.org: {type(e).__name__}: {e}")
    if os.getenv("FRAME_SPORTS_ESPN") == "1":
        try:
            d = _client.get("https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard")
            for ev in d.get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
                h, a = teams.get("home", {}), teams.get("away", {})
                st = (ev.get("status") or {}).get("type", {})
                details = [f"{x.get('clock', {}).get('displayValue', '')} {x.get('type', {}).get('text', '')}: "
                           f"{', '.join(p.get('athlete', {}).get('displayName', '') for p in x.get('athletesInvolved', []))}"
                           for x in comp.get("details", [])][-5:]
                games.append({"title": f"{h.get('team', {}).get('displayName')} vs. {a.get('team', {}).get('displayName')}",
                              "start": ev.get("date"), "status": st.get("description"),
                              "score": f"{h.get('score')}-{a.get('score')}", "minute": (ev.get("status") or {}).get("displayClock"),
                              "events": details, "source": "ESPN scoreboard (unofficial)"})
        except Exception as e:
            notes.append(f"ESPN: {type(e).__name__}: {e}")
    return games, notes
