"""Suggestion rules: which pairs the frame is allowed to SUGGEST. Applied before any LLM call, and
every refusal is written to the suggestion log with its reason.

  R1  Manifold is play money: never a leg.
  R2  Crypto pairs only with crypto, and only the same coin (BTC with BTC, never BTC with ETH).
  R3  Every pair must be the same type: both markets in the same category.
  R4  A category must be known on both sides; an unknown category is not assumed to match.

Pairs the USER types in are not refused (they asked for that exact check), but the rule result is
recorded on the case and shown, so a human sees the warning.
"""
from __future__ import annotations

from dataclasses import dataclass

from .categories import crypto_asset
from .schemas import Market

RULES_VERSION = "suggest-rules/1"


@dataclass(frozen=True)
class RuleResult:
    ok: bool
    rule: str = ""
    reason: str = ""


def check_pair(a: Market, b: Market) -> RuleResult:
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
