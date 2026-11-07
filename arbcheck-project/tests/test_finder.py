"""The 'is my idea an arbitrage?' workflow, run against the synthetic multi-venue universe."""
from arbcheck.demo_data import DEMO_NOW, build_demo_registry
from arbcheck.finder import MAX_LINES, find
from arbcheck.guard import Guard, Limits
from arbcheck.relations import HeuristicExtractor

ORION = ["Will Orion Tech announce a stock split by December 31, 2026?",
         "Will Orion Tech announce a stock split by March 31, 2027?"]
VEGA = ["Will the Vega 500 close above 5,500 on December 31, 2026?",
        "Will the Vega 500 close above 5,000 on December 31, 2026?"]
HELIX = ["Will Helix Motors recall the H3 model by December 31, 2026?",
         "Will Helix Motors recall the H3 model by March 31, 2027?"]
KESTREL = ["Will Kestrel Airlines announce a new route to Lisbon by December 31, 2026?",
           "Will Kestrel Airlines announce a new route to Lisbon by March 31, 2027?"]


def run(lines, limits=None, fee_rate=0.0):
    return find(lines, build_demo_registry(), HeuristicExtractor(), Guard(limits), DEMO_NOW, fee_rate)


def test_real_nested_deadline_arbitrage_is_confirmed():
    f = run(ORION)
    assert f.verdict == "real" and f.best.decision.status == "OK"
    assert f.best.relation.kind == "a_implies_b" and f.best.arb.net_profit > 0


def test_false_arbitrage_is_caught_not_confirmed():
    f = run(HELIX)
    assert f.verdict == "false"
    assert f.best.decision.status == "BLOCKED" and f.best.arb.naive_edge > 0.05    # it LOOKED like an arbitrage
    assert {x.kind for x in f.best.relation.flags} >= {"source", "definition"}


def test_consistent_prices_are_not_an_arbitrage():
    f = run(KESTREL)
    assert f.verdict == "none" and f.best.decision.status == "NO_ARB"


def test_threshold_logic_is_found():
    f = run(VEGA)
    assert f.verdict == "real" and f.best.relation.kind == "a_implies_b"


def test_one_market_is_checked_on_its_own_and_related_finds_are_offered():
    f = run(["Will Harbor FC win the Northvale Cup?"])
    assert f.verdict == "single"
    sets = [c for c in f.related if c.relation.kind == "at_most_one"]
    assert sets and sets[0].decision.status in ("OK", "WARN") and len(sets[0].markets) == 4
    assert sets[0].relation.basis == "metadata"


def test_exclusive_outcomes_the_user_gives_are_priced_as_a_set():
    f = run(["Will Harbor FC win the Northvale Cup?", "Will Redmoor United win the Northvale Cup?",
             "Will Stonebridge win the Northvale Cup?"])
    assert f.best.relation.kind == "at_most_one" and len(f.best.markets) == 3
    assert not any(c.relation.kind == "none" for c in f.yours)         # no pile of meaningless pairs


def test_complement_gap_on_one_market():
    f = run(["Will Quill Games release Starfall before February 28, 2027?"])
    assert f.verdict == "real" and f.best.relation.kind == "complement"


def test_cross_platform_gap_is_surfaced_but_carries_an_oracle_warning():
    f = run(["Will Zephyr Bank cut interest rates by December 31, 2026?"])
    cross = [c for c in f.yours if c.family == "cross-platform"]
    assert cross and cross[0].decision.status == "WARN"
    assert any(x.kind == "venue_oracle" for x in cross[0].relation.flags)


def test_cross_platform_false_arbitrage_when_definitions_differ():
    f = run(["Will Atlas Corp's CEO leave by March 31, 2027?"])
    assert f.verdict == "false" and f.best.family == "cross-platform"
    assert any(x.kind == "source" and x.severity == "high" for x in f.best.relation.flags)


def test_play_money_is_a_signal_never_a_leg():
    f = run(["Will Zephyr Bank cut interest rates by December 31, 2026?"])
    assert f.signals and all(s.other.venue == "Manifold" for s in f.signals)
    for c in f.yours + f.related:
        assert all(m.tradeable for m in c.markets)


def test_related_finds_come_from_the_users_single_market():
    f = run([HELIX[0]])
    assert any(c.decision.status == "BLOCKED" and len(c.markets) == 2 for c in f.related)


def test_inputs_are_limited_and_truncated():
    f = run(["x" * 5000] + ["banana"] * 10)
    assert len(f.inputs) <= MAX_LINES and all(len(r.line) <= 300 for r in f.inputs)


def test_nonsense_and_foreign_links_give_an_honest_unmatched():
    f = run(["banana smoothie recipe", "https://example.com/a"])
    assert f.verdict == "unmatched" and f.checked == 0
    assert f.inputs[1].kind == "unreadable"


def test_kill_switch_blocks_everything_and_says_so():
    f = run(ORION, Limits(kill_switch=True))
    assert f.verdict == "blocked"
    assert all(c.decision.status in ("BLOCKED", "NO_ARB") for c in f.yours + f.related)


def test_demo_links_resolve_without_search():
    f = run(["https://polymarket.example/demo/vega-5500", "https://polymarket.example/demo/vega-5000"])
    assert [r.kind for r in f.inputs] == ["link", "link"] and f.verdict == "real"


def test_repeated_runs_do_not_leak_exposure_between_checks():
    reg, g = build_demo_registry(), Guard()
    a = find(ORION, reg, HeuristicExtractor(), g, DEMO_NOW)
    b = find(ORION, reg, HeuristicExtractor(), g, DEMO_NOW)
    assert a.best.decision.approved_cost == b.best.decision.approved_cost
