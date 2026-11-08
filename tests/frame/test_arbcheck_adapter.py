"""Frame <-> real arbcheck core, on arbcheck's invented demo universe (offline, no LLM)."""
import pytest

from frame.adapter import ArbcheckAdapter, tighten
from frame.llm import FakeLLM
from frame.pipeline import build
from frame.schemas import Market, Relation, RuleDifference


@pytest.fixture(scope="module")
def ad():
    return ArbcheckAdapter(demo=True)


@pytest.fixture(scope="module")
def ms(ad):
    return {m.id: m for m in ad.list_open_markets(["polymarket", "kalshi", "manifold"])}


def rel(ms, a, b, kind, diffs=(), conf="high", verified=True, edited=False, rules_verified=True):
    return Relation(pair_id="t", relation=kind, legs=[ms[a], ms[b]], confirmed_by="t", proposed=kind,
                    confidence=conf, edited=edited, evidence_a=[ms[a].rules_text[:40]],
                    evidence_b=[ms[b].rules_text[:40]], evidence_verified=verified,
                    rule_differences=list(diffs), rules_verified=rules_verified)


@pytest.mark.parametrize("a,b,kind,verdict", [
    ("orion-dec", "orion-mar", "a_implies_b", "worth_a_look"),       # real arbitrage
    ("helix-dec", "helix-mar", "a_implies_b", "blocked"),            # rules differ (core heuristic flags)
    ("kestrel-dec", "kestrel-mar", "a_implies_b", "nothing_to_do"),  # consistent prices
    ("marlow-dec", "marlow-mar", "a_implies_b", "nothing_to_do"),    # spreads/depth eat it
    ("polar-dec", "polar-mar", "a_implies_b", "look_closely"),       # time-zone difference
    ("vega-5500", "vega-5000", "a_implies_b", "worth_a_look"),       # threshold link
    ("zephyr-poly", "KXZEPHYR-26DEC", "same_event", "look_closely"), # cross-venue: venue_oracle flag
])
def test_core_demo_outcomes(ad, ms, a, b, kind, verdict):
    r = ad.price(rel(ms, a, b, kind), 300)
    assert r.verdict == verdict and r.core_version.startswith("arbcheck")


def test_cross_venue_always_carries_venue_oracle(ad, ms):
    r = ad.price(rel(ms, "zephyr-poly", "KXZEPHYR-26DEC", "same_event"), 300)
    assert "venue_oracle:medium" in r.flags


def test_llm_blocker_becomes_high_flag_and_blocks(ad, ms):
    d = RuleDifference(topic="data_source", quote_a="", quote_b="", severity="blocker", explanation="x")
    r = ad.price(rel(ms, "orion-dec", "orion-mar", "a_implies_b", [d]), 300)
    assert r.verdict == "blocked" and "source:high" in r.flags


def test_unverified_rule_quote_blocks(ad, ms):
    r = ad.price(rel(ms, "orion-dec", "orion-mar", "a_implies_b", rules_verified=False), 300)
    assert r.verdict == "blocked"


def test_low_confidence_blocked_by_core_guard(ad, ms):
    r = ad.price(rel(ms, "orion-dec", "orion-mar", "a_implies_b", conf="low"), 300)
    assert r.verdict == "blocked"


def test_budget_is_core_exposure_cap(ad, ms):
    r = ad.price(rel(ms, "orion-dec", "orion-mar", "a_implies_b"), 50)
    assert r.approved_cost <= 50 + 1e-6 and r.clipped


def test_tighten_never_loosens(ad, ms):
    r = ad.price(rel(ms, "orion-dec", "orion-mar", "a_implies_b"), 300)
    assert tighten(r, "blocked", "x").verdict == "blocked"
    b = r.model_copy(update={"verdict": "blocked"})
    assert tighten(b, "look_closely", "x").verdict == "blocked"


def test_resolve_link_through_core_registry(ad):
    got = ad.resolve("https://polymarket.example/demo/zephyr-rate-cut")
    assert [m.id for m in got] == ["zephyr-poly"]
    assert ad.resolve("https://evil.example/x") == []


def test_manual_legs_priced(ad):
    a = Market(venue="manual", id="m1", title="Will X happen by June 30, 2027?", rules_text="X by June.",
               close_time_utc="2027-06-30T23:59:00Z", yes_bid=0.30, yes_ask=0.32, depth=200)
    b = Market(venue="manual", id="m2", title="Will X happen by December 31, 2027?", rules_text="X by December.",
               close_time_utc="2027-12-31T23:59:00Z", yes_bid=0.20, yes_ask=0.22, depth=200)
    r = ad.price(Relation(pair_id="m", relation="a_implies_b", legs=[a, b], confirmed_by="t", confidence="high",
                          edited=True), 300)
    assert r.kind == "a_implies_b" and r.naive_edge > 0
    with pytest.raises(ValueError):
        ad.price(Relation(pair_id="m", relation="a_implies_b", legs=[a.model_copy(update={"depth": None}), b],
                          confirmed_by="t"), 300)


def test_pipeline_end_to_end_with_real_core(cfg, monkeypatch):
    from dataclasses import replace
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")

    def responder(call, user, schema, key):
        if call == "judge_pair":
            if "Orion Tech" in user and "December 31, 2026" in user and "March 31, 2027" in user:
                return {"relation": "a_implies_b", "confidence": "high", "reasoning": "nested deadlines",
                        "evidence_a": ["by December 31, 2026, 11:59 PM ET"], "evidence_b": ["by March 31, 2027, 11:59 PM ET"]}
            return {"relation": "unrelated", "confidence": "high", "reasoning": "no", "evidence_a": [], "evidence_b": []}
        if call == "compare_rules":
            return {"overall": "compatible", "differences": []}
        return None  # narrate -> deterministic template

    p = build(demo=True, cfg=replace(cfg, core="arbcheck", max_days_to_close=3650, max_close_gap_days=3650), llm=FakeLLM(responder))
    p.scan()
    q = p.review_queue()
    assert len(q) == 1 and q[0].judged.a.id == "orion-dec"
    p.confirm(q[0].id, "yin")
    core = p.price(q[0].id, 300)
    assert core.verdict == "worth_a_look" and q[0].narrated.source == "template" and q[0].narrated.grounded
    assert p.audit.verify() == (True, None)
