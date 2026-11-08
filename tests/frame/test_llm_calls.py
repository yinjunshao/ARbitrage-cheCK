from frame.adapter import FakeCoreAdapter
from frame.judge import Judge, verify_verdict
from frame.llm import FakeLLM
from frame.narrate import narrate
from frame.rulediff import compare_rules
from frame.schemas import MatchVerdict, Relation


def test_judge_verified_evidence(markets):
    a, b = markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"]
    llm = FakeLLM(lambda **_: {"relation": "same_event", "confidence": "high", "reasoning": "r",
                               "evidence_a": ["December 2026 FOMC meeting"], "evidence_b": ["December 2026 meeting"]})
    jp = Judge(llm, "m").judge_pair(a, b)
    assert jp.verified and jp.verdict.relation == "same_event"


def test_judge_paraphrased_evidence_downgraded(markets):
    a, b = markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"]
    v = MatchVerdict(relation="same_event", confidence="high", reasoning="r",
                     evidence_a=["the Fed cuts in December"], evidence_b=["December 2026 meeting"])
    jp = verify_verdict("p", a, b, v)
    assert not jp.verified and jp.verdict.relation == "unclear" and jp.verdict.confidence == "low"
    assert jp.failed_spans == ["the Fed cuts in December"]


def test_judge_related_without_evidence_downgraded(markets):
    a, b = markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"]
    v = MatchVerdict(relation="same_event", confidence="high", reasoning="r", evidence_a=[], evidence_b=[])
    assert verify_verdict("p", a, b, v).verdict.relation == "unclear"


def test_judge_llm_failure_is_unclear(markets):
    jp = Judge(FakeLLM(lambda **_: None), "m").judge_pair(markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"])
    assert jp.verdict.relation == "unclear" and jp.source == "fallback"


def test_rulediff_unverified_quote_escalates(markets):
    a, b = markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"]
    llm = FakeLLM(lambda **_: {"overall": "compatible", "differences": [
        {"topic": "data_source", "quote_a": "made-up clause", "quote_b": "Federal Reserve Board press release",
         "severity": "cosmetic", "explanation": "x"}]})
    r = compare_rules(llm, "m", a, b)
    assert r.unverified_quotes == ["made-up clause"] and r.diff.overall == "material_differences" and r.escalated


def test_rulediff_missing_rules_is_blocker_without_llm(markets):
    llm = FakeLLM(lambda **_: None)
    r = compare_rules(llm, "m", markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-pi-fed-dec"])
    assert r.diff.overall == "incompatible" and llm.calls == []


def _rel(markets):
    return Relation(pair_id="p", relation="same_event", confirmed_by="t",
                    legs=[markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"]])


def _narr(**kw):
    base = {"headline": "Net edge 2.1%", "why_gap": "w", "what_could_go_wrong": ["thin depth"],
            "human_checks": ["read rules"], "recommendation": "review"}
    return {**base, **kw}


def test_narrate_invented_number_retried_then_template(markets):
    rel = _rel(markets)
    core = FakeCoreAdapter().price(rel, 100)
    llm = FakeLLM(lambda **_: _narr(headline="Net edge 2.1%, roughly $37 profit"))
    out = narrate(llm, "m", core, rel)
    assert len(llm.calls) == 2 and "$37" in llm.calls[1]["user"]
    assert out.source == "template" and out.ungrounded_numbers == ["$37"] and out.grounded


def test_narrate_retry_succeeds(markets):
    rel = _rel(markets)
    core = FakeCoreAdapter().price(rel, 100)
    answers = iter([_narr(headline="$37 profit"), _narr()])
    out = narrate(FakeLLM(lambda **_: next(answers)), "m", core, rel)
    assert out.source == "llm_retry" and out.grounded


def test_narrate_cannot_loosen_core(markets):
    rel = _rel(markets)
    core = FakeCoreAdapter().price(rel, 100).model_copy(update={"verdict": "blocked"})
    out = narrate(FakeLLM(lambda **_: _narr(recommendation="approve")), "m", core, rel)
    assert out.narrative.recommendation == "kill"
