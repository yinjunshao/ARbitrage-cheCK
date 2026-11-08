"""Novig (US sports exchange) as a read-only venue.

Uses ONLY Novig's public, unauthenticated endpoints (docs.novig.com, OpenAPI 3.1 spec; checked 2026-11-08):
    GET /v3/public/catalog/events?league=EPL           events (description "Aston Villa @ Man Utd", startsTs)
    GET /v3/public/catalog/markets?league=EPL&marketType=MONEYLINE_3_WAY_WIN   Yes/No markets with best bids
    GET /v3/public/catalog/markets/{id}/book?depth=N   resting orders per outcome (best first)
Same rules as the core's ReadOnlyClient: GET only, allowlisted host and path prefix, rate-spaced, 60 s cache, 5 MB cap.
No keys, no orders: Novig's signed trading API is never used.

How a Novig market becomes the core's Market:
  * Novig markets are binary: two outcomes ("Yes"/"No", or two teams). Each outcome has resting BIDS; the ask on one
    outcome is 1 - the bid on the other (Novig's own definition), so: YES asks = 1 - NO bids, NO asks = 1 - YES bids.
  * A contract pays 1 cent, so qty / 100 = $1 shares.
  * Fee: Novig publishes a coefficient (0.06) "charged WHEN_LIVE". We always apply 0.06 x p x (1 - p): charging it
    pre-game too can only make a verdict MORE cautious. (The exact formula is our reading of the coefficient; verify.)
  * Rules text: the public API publishes none. The frame writes a plain summary of the market type and SAYS that it is
    a summary, so the rule comparison treats Novig's side as unquoted.
Terms: Novig's Terms of Use (Novig Sweeps, 21+, eligible US states) say nothing about public market data; we read it
for internal checking only and never redistribute it.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HOST = "api.novig.com"
BASE = f"https://{HOST}/v3/public"
GAP_S = 0.5
GAME = timedelta(hours=2, minutes=15)

# our league slugs (Polymarket tags, frame/sports.LEAGUES) -> Novig league names
LEAGUES = {"premier-league": "EPL", "champions-league": "Champions League", "la-liga": "La Liga",
           "bundesliga": "Bundesliga", "serie-a": "Serie A", "nba": "NBA", "nfl": "NFL", "mlb": "MLB", "nhl": "NHL",
           "ufc": "UFC"}
TO_SLUG = {v: k for k, v in LEAGUES.items()}
# match-result market types (props such as SHOTS_ON_TARGET are skipped)
RESULT_TYPES = ("MONEYLINE_3_WAY_WIN", "MONEYLINE_3_WAY_DRAW", "MONEY")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


class Client:
    def __init__(self):
        self._cache: dict[str, tuple[float, object]] = {}
        self._last = 0.0
        self._lock = threading.Lock()

    def get(self, path: str, params: dict | None = None):
        if not path.startswith("/") or ".." in path:
            raise PermissionError(f"refused path {path}")
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        parts = urllib.parse.urlsplit(url)
        if parts.hostname != HOST or not parts.path.startswith("/v3/public/"):
            raise PermissionError(f"not on the Novig allowlist: {url}")
        with self._lock:
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < 60:
                return hit[1]
            wait = self._last + GAP_S - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "false-arb-checker"},
                                         method="GET")
            with urllib.request.urlopen(req, timeout=15) as r:      # noqa: S310 (allowlisted above)
                body = r.read(5_000_001)
            if len(body) > 5_000_000:
                raise ValueError("response too large")
            data = json.loads(body)
            self._cache[url] = (time.time(), data)
            return data


def _paged(client: Client, path: str, params: dict, cap: int = 1000) -> list[dict]:
    out, after = [], None
    while len(out) < cap:
        p = dict(params, limit=200, **({"after": after} if after else {}))
        d = client.get(path, p)
        out += d.get("items") or []
        after = d.get("next")
        if not after or not d.get("items"):
            break
    return out


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def title_and_rules(m: dict, ev: dict | None) -> tuple[str, str]:
    """A readable question and an HONESTLY LABELLED summary of the rules (Novig's API publishes no rule text)."""
    game = (ev or {}).get("description") or ""
    day = datetime.fromtimestamp(m["startsTs"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    team = re.sub(r"\s+(MONEYLINE_3_WAY_WIN|MONEYLINE_3_WAY_DRAW|MONEY)$", "", m.get("description") or "").strip()
    if m["marketType"] == "MONEYLINE_3_WAY_WIN":
        q = f"Will {team} win on {day}? ({game})"
        rule = f"Resolves Yes if {team} wins {game} in regular time (3-way market: a draw resolves No)."
    elif m["marketType"] == "MONEYLINE_3_WAY_DRAW":
        q = f"Will {game or team} end in a draw on {day}?"
        rule = f"Resolves Yes if {game or team} ends in a draw in regular time."
    else:
        names = [o.get("name") for o in m.get("outcomes") or []]
        q = f"Will {names[0]} win on {day}? ({game})" if names else f"{team} on {day}"
        rule = f"Two-way market: resolves for the winner of {game or team}."
    fee = m.get("fee") or {}
    rule += (f" [Summary written by the checker from Novig's market type {m['marketType']}: Novig's public API "
             f"publishes no rule text. Voids: {m.get('voids')}. Fee coefficient {fee.get('coefficient')} "
             f"charged {fee.get('charged')}.]")
    return q, rule


def books(client: Client, m: dict, depth: int = 10) -> tuple[list, list, list, list]:
    """(yes_bids, yes_asks, no_bids, no_asks) as [(price, $1 shares)], from Novig's per-outcome resting bids."""
    b = client.get(f"/catalog/markets/{m['marketId']}/book", {"depth": depth})
    outs = m.get("outcomes") or []
    if len(outs) != 2:
        return [], [], [], []
    orders = b.get("orders") or {}

    def levels(oid):
        agg: dict[float, float] = {}
        for o in orders.get(oid) or []:
            p = float(o["price"])
            agg[p] = agg.get(p, 0.0) + float(o["qty"]) / 100.0      # 1-cent contracts -> $1 shares
        return sorted(agg.items(), key=lambda x: -x[0])

    yb, nb = levels(outs[0]["outcomeId"]), levels(outs[1]["outcomeId"])
    ya = sorted(((round(1 - p, 4), s) for p, s in nb), key=lambda x: x[0])
    na = sorted(((round(1 - p, 4), s) for p, s in yb), key=lambda x: x[0])
    return yb, ya, nb, na


def core_market(client: Client, m: dict, ev: dict | None, models, fees, hydrate: bool = True):
    """Novig market -> the core's Market (books read now if hydrate)."""
    q, rules = title_and_rules(m, ev)
    yb, ya, nb, na = books(client, m) if hydrate else ([], [], [], [])
    L = models.Level
    yes = models.Book(asks=[L(p, s) for p, s in ya], bids=[L(p, s) for p, s in yb])
    no = models.Book(asks=[L(p, s) for p, s in na], bids=[L(p, s) for p, s in nb])
    k = float((m.get("fee") or {}).get("coefficient") or 0.06)
    fee = fees.Fee(f"Novig taker fee: {k:g} × price × (1 − price) (Novig charges it in-play; applied always, "
                   f"cautious)", k=k)
    end = datetime.fromtimestamp(m["startsTs"] / 1000, timezone.utc) + GAME
    return models.Market(m["marketId"], q, rules, end, yes, no, venue="Novig", url="",
                         meta={"novig": True, "event": m.get("eventId"), "type": m["marketType"], "exclusive": False,
                               "exhaustive": False},
                         fee=fee, tradeable=True, group=m.get("eventId") or "")


def list_markets(client: Client, leagues: list[str], days: int = 3, progress=lambda s: None) -> list[tuple[dict, dict]]:
    """(market, event) for match-result markets of the given leagues (our slugs), starting within `days`."""
    now = datetime.now(timezone.utc)
    out = []
    for slug in leagues:
        lg = LEAGUES.get(slug)
        if not lg:
            continue
        evs = {e["eventId"]: e for e in _paged(client, "/catalog/events", {"league": lg})}
        for mt in RESULT_TYPES:
            for m in _paged(client, "/catalog/markets", {"league": lg, "marketType": mt}):
                start = datetime.fromtimestamp(m["startsTs"] / 1000, timezone.utc)
                if m.get("status") == "OPEN" and now - GAME <= start <= now + timedelta(days=days):
                    out.append((m, evs.get(m["eventId"])))
        progress(f"Novig ({lg}): {len(out)} match-result markets so far…")
    return out


def find(client: Client, text: str) -> list[tuple[dict, dict]]:
    """A pasted Novig link (or id): any UUID in it is tried as a market id, then as an event id."""
    out = []
    for uid in UUID.findall(text or ""):
        try:
            m = client.get(f"/catalog/markets/{uid}")
            ev = client.get(f"/catalog/events/{m['eventId']}")
            return [(m, ev)]
        except Exception:
            pass
        try:
            ev = client.get(f"/catalog/events/{uid}")
            ms = _paged(client, "/catalog/markets", {"event": uid})
            out += [(m, ev) for m in ms if m["marketType"] in RESULT_TYPES]
        except Exception:
            pass
    return out
