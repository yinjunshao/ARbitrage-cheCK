"""LLM call 3: explain the core's result in plain English. Every number must trace to the core."""
from __future__ import annotations

import json

from .ground import check_numbers
from .llm import LLMClient, LLMError
from .schemas import CoreResult, NarratedResult, Narrative, Relation, VerifiedRuleDiff

SYSTEM = """You explain a prediction-market arbitrage check to a trader. You receive the numeric core's result
as JSON and verified rule quotes. You did not compute anything and must not compute anything.
Units: prices and edges are dollars per share (0.021 = 2.1 cents); ret, ann_ret, risk_adj_ann are fractions
(0.142 = 14.2%); cost, fee_cost, net_profit, approved_* are dollars. naive_edge is what a price-only scanner sees;
avg_edge is after spreads, depth and fees; risk_adj_edge is after the rule-risk haircut.
Rules:
1. Every number you write must appear in the CORE RESULT JSON (you may show 0.021 as 2.1¢ or 2.1%).
   Do not add, subtract, multiply, annualise or round to new precision. If unsure, write the field name instead.
2. Do not write dates, counts or amounts that are not in the JSON. Prefer words over numerals.
3. Lead with the core verdict. Your recommendation can be the same as or MORE cautious than the core, never less:
   worth_a_look -> approve or review; look_closely -> review or kill; blocked / nothing_to_do -> kill.
4. what_could_go_wrong must mention every core flag and every rule difference.
5. human_checks: concrete things to read or confirm before approving."""

_MAX_REC = {"worth_a_look": "approve", "look_closely": "review", "blocked": "kill", "nothing_to_do": "kill"}
_REC_ORDER = ["approve", "review", "kill"]


def clamp_recommendation(n: Narrative, core: CoreResult) -> Narrative:
    cap = _MAX_REC[core.verdict]
    if _REC_ORDER.index(n.recommendation) < _REC_ORDER.index(cap):
        return n.model_copy(update={"recommendation": cap})
    return n


def _rounded(obj):
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: _rounded(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rounded(v) for v in obj]
    return obj


def _text(n: Narrative) -> str:
    return "\n".join([n.headline, n.why_gap, *n.what_could_go_wrong, *n.human_checks])


def template(core: CoreResult, rel: Relation, rules: VerifiedRuleDiff | None) -> Narrative:
    """Deterministic fallback. Uses core values verbatim."""
    cents = lambda x: f"{x * 100:.1f}¢"
    risks = [f"Core flag: {f}" for f in core.flags]
    if rules:
        risks += [f"Rule difference ({d.topic}, {d.severity}): {d.explanation}" for d in rules.diff.differences]
    risks += [f"Guard check {g.status}: {g.name}: {g.detail}" for g in core.checks if g.status in ("fail", "warn")]
    return Narrative(
        headline=f"Core verdict: {core.verdict.replace('_', ' ')}. {core.headline} Price-only gap "
                 f"{cents(core.naive_edge)}, after costs {cents(core.avg_edge)} per share.",
        why_gap=f"Confirmed relation '{rel.relation}' across {', '.join(sorted({l.venue for l in rel.legs}))}; "
                f"the core priced the legs against their order books.",
        what_could_go_wrong=risks or ["No flags raised by the core."],
        human_checks=["Read both rule texts side by side.", "Confirm order books are fresh before acting."],
        recommendation=_MAX_REC[core.verdict],
    )


def structural_narrative(core: CoreResult, what: str, risk: str, titles: list[str]) -> NarratedResult:
    """Findings from structure alone (venue-declared sets, Yes + No of one market) have no LLM judgement to
    explain, so the explanation is a template built from the core's own words and numbers: no LLM call."""
    cents = lambda x: f"{x * 100:.1f}¢"
    risks = [f"Core flag: {f}" for f in core.flags]
    risks += [f"Guard check {g.status}: {g.name}: {g.detail}" for g in core.checks if g.status in ("fail", "warn")]
    n = Narrative(
        headline=f"Core verdict: {core.verdict.replace('_', ' ')}. {core.headline} Price-only gap "
                 f"{cents(core.naive_edge)}, after costs {cents(core.avg_edge)} per share.",
        why_gap=what,
        what_could_go_wrong=[risk, *risks] if risk else (risks or ["No flags raised by the core."]),
        human_checks=["Read the rules of every outcome for a void or 'other' case.",
                      "Confirm order books are fresh before acting."],
        recommendation=_MAX_REC[core.verdict])
    return NarratedResult(narrative=n, grounded=not check_numbers(_text(n), core, *titles), source="template")


def narrate(llm: LLMClient, model: str, core: CoreResult, rel: Relation,
            rules: VerifiedRuleDiff | None = None) -> NarratedResult:
    quotes = [q for d in (rules.diff.differences if rules else []) for q in (d.quote_a, d.quote_b) if q
              and q not in (rules.unverified_quotes if rules else [])]
    # Market titles/rules are verbatim source text; numbers in them (dates, thresholds) are allowed too.
    sources = [core, *quotes, *[l.title for l in rel.legs], *[l.rules_text for l in rel.legs]]
    payload = {
        "relation": rel.relation,
        "legs": [{"venue": l.venue, "title": l.title} for l in rel.legs],
        # Rounded for readability only; grounding still checks against the core's exact values.
        "core_result": _rounded(core.model_dump()),
        "rule_differences": [d.model_dump() for d in rules.diff.differences] if rules else [],
    }
    user = "CORE RESULT JSON and context:\n" + json.dumps(payload, indent=2)
    bad: list[str] = []
    for attempt, src in enumerate(("llm", "llm_retry")):
        try:
            prompt = user if attempt == 0 else (
                user + f"\n\nYour previous answer contained numbers not in the core result: {bad}. "
                       "Rewrite without them.")
            n = llm.generate("narrate", SYSTEM, prompt, Narrative, model, key=rel.pair_id + (f"#retry" if attempt else ""))
        except LLMError:
            break
        bad = check_numbers(_text(n), *sources)
        if not bad:
            return NarratedResult(narrative=clamp_recommendation(n, core), grounded=True, source=src)
    fallback = template(core, rel, rules)
    return NarratedResult(narrative=fallback, grounded=not check_numbers(_text(fallback), *sources),
                          ungrounded_numbers=bad, source="template")
