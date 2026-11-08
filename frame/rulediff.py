"""LLM call 2: verbatim-quoted differences between two markets' resolution rules."""
from __future__ import annotations

from .ground import verify_span
from .judge import pair_id
from .llm import LLMClient, LLMError
from .schemas import Market, RuleDiff, RuleDifference, VerifiedRuleDiff

SYSTEM = """You compare the resolution rules of two prediction-market contracts that may describe the same event.
List every difference that could make one resolve differently from the other, under these topics:
data_source, yes_definition, time_zone, deadline, tie_break, other.
For each difference:
- quote_a / quote_b: copy the relevant clause character for character from rules A / B; use "" if that side is silent.
- severity: blocker (they can resolve differently in a plausible world), material (edge case), cosmetic (wording only).
overall: "incompatible" if any blocker, "material_differences" if any material, else "compatible".
Never compute prices or profits. Never paraphrase inside a quote."""

_ORDER = ["compatible", "material_differences", "incompatible"]


def _max(a: str, b: str) -> str:
    return a if _ORDER.index(a) >= _ORDER.index(b) else b


def verify_rulediff(diff: RuleDiff, a: Market, b: Market) -> VerifiedRuleDiff:
    unverified = []
    for d in diff.differences:
        for q, src in ((d.quote_a, a.rules_text), (d.quote_b, b.rules_text)):
            if q and not verify_span(q, src):
                unverified.append(q)
    overall = diff.overall
    # Code floor: severity labels set the minimum overall; unverified quotes add caution.
    sev = {d.severity for d in diff.differences}
    floor = "incompatible" if "blocker" in sev else "material_differences" if ("material" in sev or unverified) else "compatible"
    new_overall = _max(overall, floor)
    escalated = new_overall != overall
    return VerifiedRuleDiff(diff=diff.model_copy(update={"overall": new_overall}),
                            unverified_quotes=unverified, escalated=escalated)


_RELATION_NOTE = {
    "a_implies_b": "The proposed relation is A implies B (e.g. nested deadlines or thresholds). The deadline or "
                   "threshold difference that CREATES the implication is expected: do not list it as a blocker. "
                   "Flag only differences that could break the implication.",
    "b_implies_a": "The proposed relation is B implies A (e.g. nested deadlines or thresholds). The deadline or "
                   "threshold difference that CREATES the implication is expected: do not list it as a blocker. "
                   "Flag only differences that could break the implication.",
    "mutually_exclusive": "The proposed relation is mutually exclusive outcomes. Flag differences that could let both resolve Yes.",
    "exhaustive_set": "The proposed relation is an exhaustive set. Flag differences that could leave an outcome uncovered.",
}


def compare_rules(llm: LLMClient, model: str, a: Market, b: Market, core_adapter=None,
                  relation: str | None = None) -> VerifiedRuleDiff:
    if core_adapter is not None and (core := core_adapter.compare_rules(a, b)) is not None:
        return core  # reuse the core's extractor when it exists
    missing = [m for m in (a, b) if not m.rules_text.strip()]
    if missing:
        diff = RuleDiff(overall="incompatible", differences=[RuleDifference(
            topic="other", quote_a="", quote_b="", severity="blocker",
            explanation="Rules text missing for " + ", ".join(f"{m.venue}:{m.id}" for m in missing)
                        + ". Cannot confirm both resolve the same way.")])
        return VerifiedRuleDiff(diff=diff)
    user = f"RULES A ({a.venue}: {a.title})\n{a.rules_text}\n\nRULES B ({b.venue}: {b.title})\n{b.rules_text}\n"
    if relation in _RELATION_NOTE:
        user = _RELATION_NOTE[relation] + "\n\n" + user
    try:
        diff = llm.generate("compare_rules", SYSTEM, user, RuleDiff, model, key=pair_id(a, b))
    except LLMError as e:
        diff = RuleDiff(overall="material_differences", differences=[RuleDifference(
            topic="other", quote_a="", quote_b="", severity="material",
            explanation=f"Automatic rule comparison unavailable ({e}). Read both rules manually.")])
    return verify_rulediff(diff, a, b)
