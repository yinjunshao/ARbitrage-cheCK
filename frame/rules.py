"""Suggestion rules: which pairs the frame is allowed to SUGGEST. Applied before any LLM call, and
every refusal is written to the suggestion log with its reason.

  R1  Manifold is play money: never a leg.
  R2  Crypto pairs only with crypto, and only the same coin (BTC with BTC, never BTC with ETH).
  R3  Every pair must be the same type: both markets in the same category.
  R4  A category must be known on both sides; an unknown category is not assumed to match.
  R5  Both markets must close soon (default 45 days): arbitrage only works near the deadline,
      when prices are pinned by resolution and the capital comes back quickly.
  R6  The two must close within a few days of each other (default 14): the same question over a
      different timeframe or year is a different bet, not a price gap.

Pairs the USER types in are not refused (they asked for that exact check), but the rule result is
recorded on the case and shown, so a human sees the warning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .categories import crypto_asset
from .schemas import Market

RULES_VERSION = "suggest-rules/2"

# R5/R6 defaults (Config overrides them). A real arbitrage needs the prices to be pinned by an
# imminent resolution; months apart, a "gap" is just two different bets and the money is tied up.
MAX_DAYS_TO_CLOSE = 45
MAX_CLOSE_GAP_DAYS = 14


@dataclass(frozen=True)
class RuleResult:
    ok: bool
    rule: str = ""
    reason: str = ""


def _close(m: Market) -> datetime | None:
    if not m.close_time_utc:
        return None
    try:
        d = datetime.fromisoformat(m.close_time_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _time_rule(a: Market, b: Market, now: datetime | None, horizon: int, gap: int) -> RuleResult:
    da, db = _close(a), _close(b)
    if da is None or db is None:
        return RuleResult(False, "R5", "A closing date is missing, so it cannot be shown to be near its deadline.")
    now = now or datetime.now(timezone.utc)
    far = max((da - now).days, (db - now).days)
    if far > horizon:
        return RuleResult(False, "R5", f"Too far from its deadline: one side closes in {far} days "
                                       f"(limit {horizon}). Prices are only pinned close to resolution.")
    apart = abs((da - db).days)
    if apart > gap:
        return RuleResult(False, "R6", f"The two close {apart} days apart (limit {gap}): same question, "
                                       "different timeframe, not a locked-in gap.")
    return RuleResult(True)


def check_pair(a: Market, b: Market, now: datetime | None = None,
               horizon_days: int = MAX_DAYS_TO_CLOSE, gap_days: int = MAX_CLOSE_GAP_DAYS) -> RuleResult:
    r = _category_rules(a, b)
    return r if not r.ok else _time_rule(a, b, now, horizon_days, gap_days)


def _category_rules(a: Market, b: Market) -> RuleResult:
    if "manifold" in (a.venue, b.venue):
        return RuleResult(False, "R1", "Manifold is play money and is never a leg.")
    ca, cb = a.category, b.category
    if "crypto" in (ca, cb):
        if ca != cb:
            return RuleResult(False, "R2", f"Crypto can only pair with crypto ({ca} vs {cb}).")
        xa, xb = crypto_asset(a.title, a.tags), crypto_asset(b.title, b.tags)
        if xa is None or xb is None:
            return RuleResult(False, "R2", "Crypto pair, but the coin could not be identified on both sides.")
        if xa != xb:
            return RuleResult(False, "R2", f"Crypto pairs must be the same coin ({xa} vs {xb}).")
        return RuleResult(True)
    if ca is None or cb is None:
        return RuleResult(False, "R4", f"Category unknown ({ca} vs {cb}); not assumed to match.")
    if ca != cb:
        return RuleResult(False, "R3", f"Different types of market ({ca} vs {cb}).")
    return RuleResult(True)
