"""prediction.com (formerly Prediction Hunt) as an EXTERNAL SCANNER to check, via its official API.

Their scanner lists cross-venue "arbitrage" (Polymarket, Kalshi, Novig, PredictIt, ...). We do not trust it: each
opportunity becomes a pair in OUR pipeline (rules, Gemini rule comparison, your confirmation, then the core prices
it on live books), and the app shows their claimed ROI next to our core's verdict. That is the false-arbitrage test.

Access and terms (prediction.com/terms, /api/docs; checked 2026-11-08):
  * API only: their Terms forbid scraping the site outside the API, and reselling or redistributing their data.
    Use is limited to personal / internal purposes. Keep your key private.
  * GET /v2/arb (cross-venue opportunities) needs the **Dev tier ($49/month)**; the free tier (1,000 requests a
    month) does not include it. Set PREDICTION_API_KEY in .env. Without a key, use the manual route instead: open
    prediction.com/arbitrage in your browser and paste an opportunity's two "Open market" links into Check a pair.
Client rules: GET only, host prediction.com, paths under /api/v2/, at most 1 request a second, 60 s cache.

    python -m frame.predictioncom            # list their current opportunities and whether our venues can read both legs
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "https://prediction.com/api/v2"
OUR_VENUES = {"polymarket", "kalshi", "novig"}


class Client:
    def __init__(self, key: str | None = None):
        self.key = key or os.getenv("PREDICTION_API_KEY")
        self._cache: dict[str, tuple[float, object]] = {}
        self._last = 0.0
        self._lock = threading.Lock()

    def get(self, path: str, params: dict | None = None):
        if not self.key:
            raise PermissionError("No PREDICTION_API_KEY in .env (and /v2/arb needs their Dev tier).")
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        parts = urllib.parse.urlsplit(url)
        if parts.hostname != "prediction.com" or not parts.path.startswith("/api/v2/") or ".." in parts.path:
            raise PermissionError(f"not on the allowlist: {url}")
        with self._lock:
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < 60:
                return hit[1]
            wait = self._last + 1.0 - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(url, headers={"Accept": "application/json", "X-API-Key": self.key,
                                                       "User-Agent": "false-arb-checker"}, method="GET")
            with urllib.request.urlopen(req, timeout=20) as r:      # noqa: S310 (allowlisted above)
                data = json.loads(r.read(5_000_000))
            self._cache[url] = (time.time(), data)
            return data

    def arb(self, min_roi: float = 0.0, platforms: str | None = None, limit: int = 20) -> dict:
        p = {"limit": min(limit, 100)}
        if min_roi:
            p["min_roi"] = min_roi
        if platforms:
            p["platforms"] = platforms
        return self.get("/arb", p)


def _pick(markets, leg: dict):
    """The market a leg refers to, among the markets its link resolved to."""
    mid = str(leg.get("market_id") or "")
    for m in markets:
        if mid and (m.id == mid or m.id.lower() == mid.lower()):
            return m
    return markets[0] if len(markets) == 1 else None


def resolve_legs(adapter, opp: dict):
    """-> (market_a, market_b, note). Both legs must be on venues we read (Polymarket, Kalshi if member, Novig)."""
    legs = opp.get("legs") or []
    if len(legs) != 2:
        return None, None, "not a two-leg opportunity"
    for leg in legs:                                   # cheap check first: no lookups if a venue is not ours
        venue = (leg.get("platform") or "").lower()
        if venue not in OUR_VENUES:
            return None, None, f"{venue} is not a venue the checker reads"
    got = []
    for leg in legs:
        venue = (leg.get("platform") or "").lower()
        ms = adapter.resolve(leg.get("source_url") or "")
        m = _pick(ms, leg)
        if m is None:
            return None, None, f"could not find {venue} market {leg.get('market_id')} from its link"
        got.append(m)
    return got[0], got[1], ""


def claim(opp: dict) -> dict:
    """What prediction.com claims, kept next to our verdict (their numbers, labelled as theirs)."""
    return {"source": "prediction.com", "roi_pct": opp.get("roi_pct"), "max_wager_usd": opp.get("max_wager_usd"),
            "total_cost": opp.get("total_cost"), "detected_at": opp.get("detected_at"),
            "legs": [{k: l.get(k) for k in ("side", "platform", "price", "liquidity_usd", "fee_usd")} for l in opp.get("legs") or []]}


def main() -> None:
    from frame.adapter import ArbcheckAdapter
    from frame.config import get_config
    cfg = get_config()
    c = Client()
    try:
        data = c.arb(limit=20)
    except PermissionError as e:
        print(e)
        print("Manual route (free): open https://prediction.com/arbitrage, copy an opportunity's two 'Open market' links,"
              " and paste them into 'Check a pair' in the app.")
        return
    except Exception as e:
        print(f"prediction.com API error: {e} (is your key on the Dev tier? /v2/arb needs it)")
        return
    ad = ArbcheckAdapter(demo=False, kalshi_member=cfg.kalshi_member)
    print(f"prediction.com: {data.get('count')} opportunities as of {data.get('as_of')} (delay {data.get('delay_seconds')} s)")
    for o in data.get("opportunities") or []:
        a, b, note = resolve_legs(ad, o)
        legs = " + ".join(f"{l.get('platform')} {l.get('side')} @ {l.get('price')}" for l in o.get("legs") or [])
        print(f"  {o.get('roi_pct'):>6}%  {(o.get('group_title') or '')[:55]:<55} {legs}")
        print("          " + (f"both legs readable -> check it in the app" if a else f"skipped: {note}"))


if __name__ == "__main__":
    main()
