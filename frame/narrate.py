"""LLM call 3: explain the core's result in plain English. Every number must trace to the core."""
from __future__ import annotations

import json

from .ground import check_numbers
from .llm import LLMClient, LLMError
from .schemas import CoreResult, NarratedResult, Narrative, Relation, VerifiedRuleDiff

SYSTEM = """You explain a prediction-market arbitrage check to a trader. You receive the numeric core's result
as JSON and verified rule quotes. You did not compute anything and must not compute anything.
Rules:
1. Every number you write must appear in the CORE RESULT JSON (you may show a fraction like 0.021 as 2.1%).
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


def _text(n: Narrative) -> str:
    return "\n".join([n.headline, n.why_gap, *n.what_could_go_wrong, *n.human_checks])


def template(core: CoreResult, rel: Relation, rules: VerifiedRuleDiff | None) -> Narrative:
    """Deterministic fallback. Uses core values verbatim."""
    pct = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
    risks = [f"Core flag: {f}" for f in core.flags]
    if rules:
        risks += [f"Rule difference ({d.topic}, {d.severity}): {d.explanation}" for d in rules.diff.differences]
    risks += [f"Guard check failed: {g.name} {g.detail}".strip() for g in core.guard_checks if not g.passed]
    return Narrative(
        headline=f"Core verdict: {core.verdict.replace('_', ' ')}. Net edge {pct(core.net_edge)} "
                 f"(gross {pct(core.gross_edge)}).",
        why_gap=f"Confirmed relation '{rel.relation}' across {', '.join(sorted({l.venue for l in rel.legs}))}; "
                f"the core priced the legs against their order books.",
        what_could_go_wrong=risks or ["No flags raised by the core."],
        human_checks=["Read both rule texts side by side.", "Confirm order books are fresh before acting."],
        recommendation=_MAX_REC[core.verdict],
    )


def narrate(llm: LLMClient, model: str, core: CoreResult, rel: Relation,
            rules: VerifiedRuleDiff | None = None) -> NarratedResult:
    quotes = [q for d in (rules.diff.differences if rules else []) for q in (d.quote_a, d.quote_b) if q
              and q not in (rules.unverified_quotes if rules else [])]
    # Market titles/rules are verbatim source text; numbers in them (dates, thresholds) are allowed too.
    sources = [core, *quotes, *[l.title for l in rel.legs], *[l.rules_text for l in rel.legs]]
    payload = {
        "relation": rel.relation,
        "legs": [{"venue": l.venue, "title": l.title} for l in rel.legs],
        "core_result": core.model_dump(),
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
