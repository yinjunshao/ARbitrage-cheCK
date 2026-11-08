"""Risk controls. Limits live here, in code, outside the LLM.

Design rules
------------
1. ASYMMETRIC AUTHORITY: the LLM can only veto a trade or shrink its expected profit
   (mismatch flags). No code path ever increases size or approves a trade because of
   something the LLM said.
2. EVERY ACTION IS LOGGED: inputs (hashed), relation, numbers, decision, limits.
3. A KILL SWITCH blocks everything, instantly, regardless of how good a trade looks.
4. The tool is read-only: it never places orders. "Approved" means "worth a human's look".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .engine import LABELS, ArbResult, clip_to_budget, totals
from .models import Market
from .relations import Relation


@dataclass
class Limits:
    max_notional: float = 300.0        # dollars per trade
    max_total_notional: float = 1000.0 # dollars across all approved trades in one scan
    min_edge: float = 0.01             # dollars per share after costs AND rule-risk haircut
    min_shares: float = 20.0           # below this the book is too thin to bother
    min_confidence: float = 0.75       # extractor confidence needed to trust the relation
    min_annualised: float = 0.05       # capital is locked until resolution: beat ~savings rates
    haircuts: dict = field(default_factory=lambda: {"low": 0.005, "medium": 0.02})
    kill_switch: bool = False


@dataclass
class Check:
    name: str
    status: str    # pass | warn | fail | na
    detail: str


@dataclass
class Decision:
    status: str                  # OK | WARN | BLOCKED | NO_ARB
    headline: str                # one plain-English sentence for the user
    checks: list[Check]
    approved_shares: float = 0.0
    approved_cost: float = 0.0
    approved_profit: float = 0.0     # after rule-risk haircut
    haircut_per_share: float = 0.0
    risk_adj_edge: float = 0.0       # per share, after costs and haircut
    risk_adj_ann: float = 0.0
    clipped: bool = False
    fills: list = field(default_factory=list)   # the approved (clipped) fills, for the UI


def _cents(x: float) -> str:
    return f"{x * 100:.1f}¢"


def _logic_check(rel: Relation) -> Check:
    """How do we know these markets are linked the way we think? Text must be quoted verbatim;
    structural links (one market's Yes and No, or a venue's own 'mutually exclusive' label)
    are justified by construction, and say so."""
    if rel.basis == "text":
        return Check("Logic traced to rule text", "pass" if rel.verified else "fail",
                     "every quoted clause found verbatim in the rules" if rel.verified
                     else (rel.problems[0] if rel.problems else "unverified"))
    return Check("Logic traced to its source", "pass" if rel.verified else "fail",
                 rel.rationale if rel.verified else (rel.problems[0] if rel.problems else "unverified"))


class Guard:
    def __init__(self, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.used = 0.0     # dollars already approved in this scan

    def decide(self, markets: list[Market], rel: Relation, arb: ArbResult) -> Decision:
        L = self.limits
        gap = arb.executable
        high = [f for f in rel.flags if f.severity == "high"]
        soft = [f for f in rel.flags if f.severity != "high"]
        haircut = sum(L.haircuts.get(f.severity, 0.02) for f in soft)

        # Size: clip to the per-trade cap and to whatever total budget is left.
        budget = max(0.0, min(L.max_notional, L.max_total_notional - self.used))
        fills = clip_to_budget(arb.fills, budget, arb.fees) if gap else []
        shares, cost, profit = totals(fills, arb.floor, arb.fees)
        clipped = gap and shares < arb.shares - 1e-6
        adj_profit = profit - haircut * shares
        adj_edge = adj_profit / shares if shares else 0.0
        adj_ann = (adj_profit / cost) * 365 / arb.days if cost else 0.0

        na = "No executable gap, so nothing to check."
        checks = [
            Check("Kill switch", "fail" if L.kill_switch else "pass",
                  "ON: all trades blocked" if L.kill_switch else "off"),
            _logic_check(rel),
            Check("Extractor confidence", "pass" if rel.confidence >= L.min_confidence else "fail",
                  f"{rel.confidence:.0%} (needs {L.min_confidence:.0%})"),
            Check("Rules match", "fail" if high else ("warn" if soft else "pass"),
                  " ".join(f.note or f.kind for f in high + soft) if rel.flags else "no differences found"),
            Check("Profit survives spread & depth", "pass" if gap else "fail",
                  f"{_cents(arb.avg_edge)} per share on {arb.shares:,.0f} shares" if gap
                  else f"naive {_cents(arb.naive_edge)} but nothing executable"),
            Check("Enough depth", "na" if not gap else ("pass" if arb.shares >= L.min_shares else "fail"),
                  na if not gap else f"{arb.shares:,.0f} shares available (needs {L.min_shares:,.0f})"),
            Check("Survives rule-risk haircut", "na" if not gap else ("pass" if adj_edge >= L.min_edge else "fail"),
                  na if not gap else f"{_cents(adj_edge)} per share after {_cents(haircut)} haircut"),
            Check("Return beats hurdle", "na" if not gap else ("pass" if adj_ann >= L.min_annualised else "fail"),
                  na if not gap else f"{adj_ann:.0%} a year over {arb.days:.0f} days (needs {L.min_annualised:.0%})"),
            Check("Within exposure cap", "na" if not gap else
                  ("fail" if shares < L.min_shares else ("warn" if clipped else "pass")),
                  na if not gap else (f"only ${budget:,.0f} of budget left" if shares < L.min_shares
                                      else f"size clipped to ${cost:,.0f}" if clipped
                                      else f"${cost:,.0f} of ${budget:,.0f} budget")),
        ]

        # ---- decision, in strict priority order ----
        naive = arb.naive_edge
        if L.kill_switch:
            return Decision("BLOCKED", "Kill switch is on: all trades are blocked.", checks)
        if not gap:
            if rel.kind == "none":
                msg = "No logical link between these markets, so there is nothing to check."
            elif naive > 0.005:
                msg = (f"Prices look {_cents(naive)} apart, but spreads and thin order books "
                       f"remove the profit.")
            else:
                msg = "The prices are consistent with each other."
            return Decision("NO_ARB", msg, checks)
        if high:
            return Decision("BLOCKED", f"It looks like +{_cents(naive)} on prices, but the two rules "
                            f"don't match, so the profit is not locked in.", checks)
        if not rel.verified:
            return Decision("BLOCKED", "Couldn't trace the logic back to the rule text, so this "
                            "is blocked until a human checks it.", checks)
        if rel.confidence < L.min_confidence:
            return Decision("BLOCKED", "The relation between these markets is too uncertain to "
                            "trust. Blocked.", checks)
        if arb.shares < L.min_shares:
            return Decision("NO_ARB", "A real gap, but the order book is too thin to be worth it.", checks)
        if shares < L.min_shares:       # book is deep enough, but the exposure budget is not
            return Decision("NO_ARB", "Exposure budget already used up by other trades, so no "
                            "meaningful position is left.", checks)
        if adj_edge < L.min_edge:
            return Decision("NO_ARB", "The gap disappears once rule-risk is priced in.", checks)
        if adj_ann < L.min_annualised:
            return Decision("NO_ARB", f"The profit is real but small: {adj_ann:.0%} a year while your "
                            f"money is locked up for {arb.days:.0f} days.", checks)

        self.used += cost
        status = "WARN" if soft else "OK"
        tail = ("Check the rule differences below first." if soft
                else "Rules match and the gap survives costs.")
        return Decision(status, f"{tail} About ${adj_profit:,.0f} locked in on ${cost:,.0f}.",
                        checks, shares, cost, adj_profit, haircut, adj_edge, adj_ann, bool(clipped),
                        fills)


class DecisionLog:
    """Append-only JSONL audit trail: one line per pair per scan."""

    def __init__(self, path: str | None):
        self.path = path

    def write(self, pair_id: str, markets: list[Market], rel: Relation, arb: ArbResult,
              decision: Decision, limits: Limits) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pair_id": pair_id,
            # Hashes pin the exact rule text the decision was based on.
            "rules_sha256": {LABELS[i].lower(): hashlib.sha256(m.rules.encode()).hexdigest()
                             for i, m in enumerate(markets)},
            "extractor": rel.extractor,
            "relation": asdict(rel),
            "arb": {k: v for k, v in asdict(arb).items() if k not in ("fills", "scenarios")},
            "decision": asdict(decision),
            "limits": asdict(limits),
        }
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        return record
