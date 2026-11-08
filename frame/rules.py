"""Suggestion rules: which pairs the frame is allowed to SUGGEST. Applied before any LLM call, and
every refusal is written to the suggestion log with its reason.

  R0  Not the same market twice, and neither leg already effectively settled (displayed price ≥ 0.98 or ≤ 0.02)
      or already closed.
  R1  Manifold is play money: never a leg.
  R2  Crypto pairs only with crypto, and only the same coin (BTC with BTC, never BTC with ETH).
  R3  Every pair must be the same type: the two markets must share at least one category (an event tagged
      "politics" and "trade-war" counts as both politics and economics).
  R4  A category must be known on both sides; an unknown category is not assumed to match.
  R5  Both markets must close soon (default 45 days): arbitrage only works near the deadline,
      when prices are pinned by resolution and the capital comes back quickly.
  R6  For pairs claimed to be the SAME question (cross-venue / cross-event), the two must close within a few
      days of each other (default 14): the same question over a different timeframe or year is a different
      bet, not a price gap. Nested deadlines inside one event ("by Nov 8" vs "by Nov 9") are NOT refused by R6:
      they are a logical link whose only arbitrage is a price inversion, and the core screens them (no LLM).

Pairs the USER types in are not refused (they asked for that exact check), but the rule result is
recorded on the case and shown, so a human sees the warning.

suggest-rules/3 (changes from /2 after the rules review): R0 added; R5 refuses already-closed markets; R5/R6
measure exact time (the old whole-day rounding made R6 depend on argument order); R3 matches on any shared
category; R6 no longer applies to nested ladders inside one event.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .categories import all_categories, crypto_asset
from .schemas import Market

RULES_VERSION = "suggest-rules/3"

# R5/R6 defaults (Config overrides them). A real arbitrage needs the prices to be pinned by an
# imminent resolution; months apart, a "gap" is just two different bets and the money is tied up.
MAX_DAYS_TO_CLOSE = 45
MAX_CLOSE_GAP_DAYS = 14
SETTLED_HI, SETTLED_LO = 0.98, 0.02


@dataclass(frozen=True)
class RuleResult:
    ok: bool
    rule: str = ""
    reason: str = ""


def _close(m: Market) -> datetime | None:
    if not m.close_time_utc:
        return None
    try:
        d = datetime.fromisoformat(m.close_time_utc.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _days(seconds: float) -> float:
    return seconds / 86400.0


def _basic(a: Market, b: Market) -> RuleResult:
    if a.venue == b.venue and a.id == b.id:
        return RuleResult(False, "R0", "The same market on both sides.")
    for m in (a, b):
        if m.prob is not None and (m.prob >= SETTLED_HI or m.prob <= SETTLED_LO):
            return RuleResult(False, "R0", f"'{m.title[:60]}' is effectively settled (displayed price {m.prob:.2f}).")
    return RuleResult(True)


def _time_rule(a: Market, b: Market, now: datetime | None, horizon: int, gap: int, nested: bool) -> RuleResult:
    da, db = _close(a), _close(b)
    if da is None or db is None:
        return RuleResult(False, "R5", "A closing date is missing, so it cannot be shown to be near its deadline.")
    now = now or datetime.now(timezone.utc)
    if min(da, db) <= now:
        return RuleResult(False, "R5", "One side has already closed.")
    far = _days(max((da - now).total_seconds(), (db - now).total_seconds()))
    if far > horizon:
        return RuleResult(False, "R5", f"Too far from its deadline: one side closes in {far:.0f} days "
                                       f"(limit {horizon}). Prices are only pinned close to resolution.")
    apart = _days(abs((da - db).total_seconds()))
    if not nested and apart > gap:
        return RuleResult(False, "R6", f"The two close {apart:.0f} days apart (limit {gap}): same question, "
                                       "different timeframe, not a locked-in gap.")
    return RuleResult(True)


def check_pair(a: Market, b: Market, now: datetime | None = None, horizon_days: int = MAX_DAYS_TO_CLOSE,
               gap_days: int = MAX_CLOSE_GAP_DAYS, nested: bool = False) -> RuleResult:
    """nested=True for a date/threshold ladder inside one event (R6 does not apply to it)."""
    for r in (_basic(a, b), _category_rules(a, b)):
        if not r.ok:
            return r
    return _time_rule(a, b, now, horizon_days, gap_days, nested)


def _cats(m: Market) -> set[str]:
    got = all_categories(m.tags) if m.tags else set()
    return got | ({m.category} if m.category else set())


def _category_rules(a: Market, b: Market) -> RuleResult:
    if "manifold" in (a.venue, b.venue):
        return RuleResult(False, "R1", "Manifold is play money and is never a leg.")
    ca, cb = a.category, b.category
    if "crypto" in (ca, cb):
        if ca != cb and not ({"crypto"} & _cats(a) & _cats(b)):
            return RuleResult(False, "R2", f"Crypto can only pair with crypto ({ca} vs {cb}).")
        xa, xb = crypto_asset(a.title, a.tags), crypto_asset(b.title, b.tags)
        if xa is None or xb is None:
            return RuleResult(False, "R2", "Crypto pair, but the coin could not be identified on both sides.")
        if xa != xb:
            return RuleResult(False, "R2", f"Crypto pairs must be the same coin ({xa} vs {xb}).")
        return RuleResult(True)
    if ca is None or cb is None:
        return RuleResult(False, "R4", f"Category unknown ({ca} vs {cb}); not assumed to match.")
    if not (_cats(a) & _cats(b)):
        return RuleResult(False, "R3", f"Different types of market ({ca} vs {cb}).")
    return RuleResult(True)
