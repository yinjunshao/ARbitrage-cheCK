"""Arbitrage types, balanced judging across categories, and structure-only findings."""
from dataclasses import replace

import pytest

from frame import arbtypes
from frame.adapter import ArbcheckAdapter
from frame.llm import FakeLLM
from frame.pipeline import balanced, build
from frame.schemas import Market


def mk(i, cat, venue="polymarket", event=None):
    return Market(venue=venue, id=i, title=f"q{i}", category=cat, event_id=event or f"e{i}")


def test_classify_matches_the_cores_families():
    assert arbtypes.classify("same_event", ["polymarket", "kalshi"]) == arbtypes.CROSS_PLATFORM
    assert arbtypes.classify("a_implies_b", ["polymarket", "polymarket"]) == arbtypes.LOGICAL
    assert arbtypes.classify("a_implies_b", ["polymarket", "kalshi"]) == arbtypes.CROSS_PLATFORM   # the core's rule
    assert arbtypes.classify("at_most_one", ["polymarket"] * 4, 4) == arbtypes.MULTI_OUTCOME
    assert arbtypes.classify("complement", ["polymarket"], 1) == arbtypes.SAME_MARKET
    assert set(arbtypes.ORDER) == set(arbtypes.TYPES)


def test_judging_budget_is_shared_across_categories_not_taken_by_the_biggest():
    crypto = [(mk(f"c{i}", "crypto"), mk(f"d{i}", "crypto"), 0.99 - i * 0.001, "cross_event", f"c{i}") for i in range(200)]
    other = [(mk(f"s{i}", "sports"), mk(f"t{i}", "sports"), 0.5, "cross_event", f"s{i}") for i in range(5)] + \
            [(mk(f"p{i}", "politics"), mk(f"q{i}", "politics"), 0.5, "cross_event", f"p{i}") for i in range(5)]
    got = balanced(crypto + other, 12)
    cats = [t[0].category for t in got]
    assert len(got) == 12 and cats.count("sports") >= 4 and cats.count("politics") >= 4 and cats.count("crypto") <= 4


def test_cross_venue_pairs_come_first_inside_a_category():
    a = [(mk("1", "economics"), mk("2", "economics"), 0.9, "cross_event", "x1"),
         (mk("3", "economics"), mk("4", "economics", venue="kalshi"), 0.5, "cross_venue", "x2")]
    assert balanced(a, 1)[0][4] == "x2"


def test_unused_budget_is_not_wasted():
    few = [(mk("1", "sports"), mk("2", "sports"), 0.5, "cross_event", "x")]
    assert len(balanced(few, 30)) == 1


@pytest.fixture(scope="module")
def ad():
    return ArbcheckAdapter(demo=True)


def test_single_market_is_priced_by_the_core_as_same_market(ad):
    m = next(m for m in ad.list_open_markets(["polymarket"]))
    r = ad.price_single(m, 100)
    assert r.family == "same-market" and r.kind == "complement" and r.core_version.startswith("arbcheck")


def test_single_candidates_spread_over_categories(ad):
    ms = [mk(f"a{i}", "crypto") for i in range(20)] + [mk("s", "sports"), mk("p", "politics")]
    got = ArbcheckAdapter.single_candidates(ad, ms, 3)
    assert {m.category for m in got} == {"crypto", "sports", "politics"}


def test_scan_produces_typed_findings_without_any_llm_call(cfg, monkeypatch):
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")
    llm = FakeLLM(lambda **_: {"relation": "unrelated", "confidence": "high", "reasoning": "x",
                               "evidence_a": [], "evidence_b": []})
    p = build(demo=True, cfg=replace(cfg, core="arbcheck", max_days_to_close=3650, max_close_gap_days=3650,
                                     max_judge_per_scan=0, max_singles_per_scan=5), llm=llm)
    s = p.scan()
    assert s.singles == 5 and p.findings and all(f.core.family for f in p.findings.values())
    assert {f.arb_type for f in p.findings.values()} <= {arbtypes.SAME_MARKET, arbtypes.MULTI_OUTCOME}
    f = next(iter(p.findings.values()))
    assert f.narrated.source == "template" and f.narrated.grounded
    assert [c for c in llm.calls] == []        # structure-only findings never reach the LLM
