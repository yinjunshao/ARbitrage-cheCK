"""Compliance layer. EVERY network request in this project goes through `ReadOnlyClient`.

What it guarantees (each point is unit-tested):

  1. READ-ONLY. It can only send GET. There is no code path to place, cancel or sign an
     order, move money, log in, or read anyone's account.
  2. ALLOWLISTED. Only the exact public data endpoints in `ALLOWED` are reachable.
     Anything else (an order endpoint, a portfolio endpoint, another host) is refused
     before a request is made.
  3. POLITE. A minimum gap between requests per host, a short in-memory cache, exponential
     backoff on 429 and 5xx (honouring Retry-After), a small retry limit, a response size
     cap, and a User-Agent that says what we are. We stay far below each venue's published
     limits and never try to get around them.
  4. NO SCRAPING. Only documented API endpoints are used, never venue web pages.
  5. KALSHI IS OPT-IN. Kalshi's Developer Agreement (v1.1) limits API use to a member's own
     trading on Kalshi, bars collecting, caching or storing its API data for any other
     purpose, and bars sharing it with third parties. So Kalshi requests are refused unless
     the person running the tool switched Kalshi on themselves (`--kalshi-member`), the app
     only listens on this computer, and Kalshi data is held in memory only.

This is engineering, not legal advice. The venues' own terms are the authority; the links
are shown in the app (`VENUE_TERMS`) and you should read them before you rely on this.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

USER_AGENT = "arbcheck/0.2 (read-only analysis tool; never places orders)"
MAX_BYTES = 5_000_000
MAX_RETRIES = 3

# host -> the only path prefixes we will ever request there. All are public data endpoints.
ALLOWED: dict[str, tuple[str, ...]] = {
    "gamma-api.polymarket.com": ("/markets", "/events", "/public-search"),
    "clob.polymarket.com": ("/book",),
    "polymarket.com": ("/api/geoblock",),            # "is trading available from my location?"
    "api.elections.kalshi.com": ("/trade-api/v2/markets", "/trade-api/v2/events"),
    "external-api.kalshi.com": ("/trade-api/v2/markets", "/trade-api/v2/events"),
    "api.manifold.markets": ("/v0/search-markets", "/v0/slug/", "/v0/market/"),
}
KALSHI_HOSTS = {"api.elections.kalshi.com", "external-api.kalshi.com"}

# Minimum seconds between requests to the same host. Published limits are far higher
# (Polymarket: hundreds per 10s; Manifold: 500 per minute), so these are deliberately slow.
MIN_INTERVAL: dict[str, float] = {
    "gamma-api.polymarket.com": 0.2, "clob.polymarket.com": 0.2, "polymarket.com": 1.0,
    "api.elections.kalshi.com": 0.5, "external-api.kalshi.com": 0.5, "api.manifold.markets": 0.5,
}


class Refused(Exception):
    """We would not send this request: not allowlisted, or Kalshi is switched off."""


class FetchError(Exception):
    """A request was allowed but failed (network error, bad status, bad JSON)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class VenueTerms:
    name: str
    tradeable: bool
    uses: str          # what we read
    our_rate: str      # how hard we hit it
    their_rule: str    # the venue's own published limit or rule
    restriction: str   # what the person using this should know
    terms_url: str
    docs_url: str


VENUE_TERMS: dict[str, VenueTerms] = {
    "Polymarket": VenueTerms(
        "Polymarket", True,
        "Public read-only data: market search and rule text (Gamma API), order books (CLOB API). "
        "No account, no keys, no orders.",
        "At most about 5 requests a second, with a 60-second in-memory cache.",
        "Documented limits, for example 300 requests per 10 seconds on /markets and 1,500 per 10 seconds on "
        "/book. Throttled, not rejected, when exceeded.",
        "Reading data is not geo-restricted, but placing orders is blocked in some countries. This tool never "
        "places orders; check you are allowed to trade before you do.",
        "https://polymarket.com/tos", "https://docs.polymarket.com/api-reference/rate-limits"),
    "Kalshi": VenueTerms(
        "Kalshi", True,
        "Public market-data endpoints only: events, markets and order books. No keys, no orders, "
        "no account data.",
        "At most about 2 requests a second, a 60-second in-memory cache, and nothing from Kalshi is "
        "written to disk.",
        "Kalshi sets and may change rate limits at any time; the Developer Agreement says not to get around "
        "them and to back off with limited retries.",
        "Kalshi's Developer Agreement limits API use to a member's own trading on Kalshi, bars collecting, "
        "caching or storing its API data for other purposes, and bars sharing it with third parties. So "
        "Kalshi is OFF unless you start the app yourself with --kalshi-member, it only runs on your own "
        "computer, and its data is never saved or shared. Do not post screenshots of Kalshi prices.",
        "https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf", "https://docs.kalshi.com"),
    "Manifold": VenueTerms(
        "Manifold", False,
        "Public API: market search and lookups. No keys.",
        "At most about 2 requests a second, with a 60-second in-memory cache.",
        "500 requests per minute per IP. Scraping other than through the API, and getting around limits, "
        "are prohibited; the API may not be used to train commercial AI models without a data license.",
        "Manifold uses play money (mana) with no cash value, so it is shown only as a price signal and is "
        "never used as a leg of a trade.",
        "https://docs.manifold.markets/terms", "https://docs.manifold.markets/api"),
}


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def _path_ok(path: str, prefixes: tuple[str, ...]) -> bool:
    for p in prefixes:
        if p.endswith("/"):
            if path.startswith(p) and len(path) > len(p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


def check_url(url: str, kalshi_enabled: bool) -> urllib.parse.SplitResult:
    """Raise `Refused` unless this exact request is one we are allowed to make."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        raise Refused(f"only plain https requests are allowed: {url}")
    host = parts.hostname or ""
    prefixes = ALLOWED.get(host)
    if prefixes is None:
        raise Refused(f"host not on the allowlist: {host}")
    if ".." in parts.path or "%2f" in parts.path.lower() or "//" in parts.path:
        raise Refused(f"odd path refused: {parts.path}")
    if not _path_ok(parts.path, prefixes):
        raise Refused(f"path not on the allowlist: {host}{parts.path}")
    if host in KALSHI_HOSTS and not kalshi_enabled:
        raise Refused("Kalshi is switched off (see its Developer Agreement; start with --kalshi-member to use it)")
    return parts


Transport = Callable[[str, dict, float], tuple[int, bytes, dict]]


def _urlopen(url: str, headers: dict, timeout: float) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 (allowlisted above)
            return resp.status, resp.read(MAX_BYTES + 1), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read(MAX_BYTES + 1) if err.fp else b"", dict(err.headers or {})
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise FetchError(f"network error: {err}") from err


class ReadOnlyClient:
    """The one way this project talks to the internet. See the module docstring."""

    def __init__(self, kalshi_enabled: bool = False, transport: Transport | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 cache_ttl: float = 60.0, min_interval: dict[str, float] | None = None):
        self.kalshi_enabled = kalshi_enabled
        self._transport = transport or _urlopen
        self._clock, self._sleep = clock, sleep
        self._ttl = cache_ttl
        self._interval = {**MIN_INTERVAL, **(min_interval or {})}
        self._cache: dict[str, tuple[float, Any]] = {}      # in memory only, never written to disk
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.requests_sent = 0
        self.cache_hits = 0

    def get(self, url: str, params: dict | None = None) -> Any:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        parts = check_url(url, self.kalshi_enabled)
        host = parts.hostname or ""
        with self._lock:                                   # one request at a time keeps rate-limiting honest
            now = self._clock()
            hit = self._cache.get(url)
            if hit and now - hit[0] < self._ttl:
                self.cache_hits += 1
                return hit[1]
            data = self._fetch(url, host)
            self._cache[url] = (self._clock(), data)
            return data

    def _wait_turn(self, host: str) -> None:
        gap = self._interval.get(host, 0.5)
        wait = self._last.get(host, -1e9) + gap - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last[host] = self._clock()

    def _fetch(self, url: str, host: str) -> Any:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        delay = 1.0
        for attempt in range(MAX_RETRIES + 1):
            self._wait_turn(host)
            self.requests_sent += 1
            status, body, resp_headers = self._transport(url, headers, 15.0)
            if status == 200:
                if len(body) > MAX_BYTES:
                    raise FetchError("response too large")
                try:
                    return json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as err:
                    raise FetchError("response was not valid JSON") from err
            if status in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                retry_after = resp_headers.get("Retry-After") or resp_headers.get("retry-after")
                try:
                    pause = min(float(retry_after), 10.0) if retry_after else delay
                except ValueError:
                    pause = delay
                self._sleep(pause)
                delay *= 2
                continue
            raise FetchError(f"{host} answered HTTP {status}", status)
        raise FetchError("too many retries")                # unreachable, keeps type checkers calm
