from dataclasses import replace

from frame.categories import canonical, crypto_asset
from frame.llm import FakeLLM
from frame.pipeline import build
from frame.rules import check_pair
from frame.schemas import Market


def mk(i, title, cat, venue="polymarket", tags=None, close="2026-12-31T00:00:00Z", event=None):
    return Market(venue=venue, id=i, title=title, category=cat, tags=tags or [], close_time_utc=close,
                  event_id=event, rules_text=title)


def test_canonical_categories_across_venues():
    assert canonical(["sports", "nba"]) == "sports"
    assert canonical("Economics") == "economics" and canonical(["fed-rates"]) == "economics"
    assert canonical(["politics", "bitcoin"]) == "crypto"           # crypto wins
    assert canonical("Science and Technology") == "science_tech"
    assert canonical(["earn-4"]) is None and canonical([]) is None


def test_crypto_asset():
    assert crypto_asset("Will Bitcoin hit $150k by December?") == "btc"
    assert crypto_asset("ETH above 5000?") == "eth"
    assert crypto_asset("Will BTC outperform ETH?") is None          # names two coins


def test_r2_crypto_only_with_crypto_same_coin():
    btc1 = mk("1", "Bitcoin above $120k on Dec 31?", "crypto")
    btc2 = mk("2", "Will BTC hit $150k by Dec 31?", "crypto", venue="kalshi")
    eth = mk("3", "Ethereum above $5k on Dec 31?", "crypto")
    fed = mk("4", "Fed cut in December?", "economics")
    assert check_pair(btc1, btc2).ok
    assert check_pair(btc1, eth).rule == "R2"
    assert check_pair(btc1, fed).rule == "R2" and check_pair(fed, btc1).rule == "R2"


def test_r3_same_type_r4_unknown_r1_manifold():
    fed = mk("4", "Fed cut in December?", "economics")
    elec = mk("5", "Who wins the election?", "politics")
    unk = mk("6", "Something?", None)
    mf = mk("7", "Fed cut in December?", "economics", venue="manifold")
    assert check_pair(fed, elec).rule == "R3"
    assert check_pair(fed, unk).rule == "R4"
    assert check_pair(fed, mf).rule == "R1"
    assert check_pair(fed, mk("8", "Fed cuts by 50bps in December?", "economics", venue="kalshi")).ok


def test_rule_refusals_logged_before_any_llm_call(cfg, monkeypatch):
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")
    llm = FakeLLM(lambda **_: {"relation": "unrelated", "confidence": "high", "reasoning": "x",
                               "evidence_a": [], "evidence_b": []})
    p = build(cfg=replace(cfg, core="fake", min_similarity=0.0), llm=llm)
    ms = [mk("b1", "Bitcoin above $120k on Dec 31?", "crypto"),
          mk("e1", "Bitcoin dominance and ETH above $5k on Dec 31?", "crypto", venue="kalshi"),
          mk("f1", "Fed cut by Dec 31?", "economics", venue="kalshi"),
          mk("f2", "Fed cut on Dec 31 meeting?", "economics")]
    s = p.judge_candidates(ms)
    refused = [r["payload"] for r in p.suggestions.records() if r["kind"] == "rule_refused"]
    assert refused and all(r["rule"] in ("R2", "R3") for r in refused)
    judged_pairs = {(c["user"].count("Bitcoin"), c["user"].count("Fed")) for c in llm.calls}
    assert all(not (b and f) for b, f in judged_pairs)     # no crypto x economics pair reached the LLM


def test_suggestion_log_chain_and_latest(cfg, monkeypatch, markets):
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")
    p = build(replay=True, cfg=cfg)
    p.scan()
    c = p.review_queue()[0]
    p.confirm(c.id, "yin")
    p.price(c.id, 100)
    assert p.suggestions.log.verify() == (True, None)
    row = next(r for r in p.suggestions.latest() if r["pair"] == c.id)
    assert row["last"] == "priced" and row["verdict"] in ("look_closely", "blocked")
    assert [r.pair_id for r in p.confirmed_relations()] == [c.id]


def test_outcome_matching_fed_polymarket_vs_kalshi():
    """Titles taken from the live Fed December 2026 events on Polymarket and Kalshi."""
    from frame.pipeline import _match_outcomes
    A = [mk(f"p{i}", t, "economics") for i, t in enumerate([
        "Will the Fed decrease interest rates by 50+ bps after the December 2026 meeting?",
        "Will the Fed decrease interest rates by 25 bps after the December 2026 meeting?",
        "Will there be no change in Fed interest rates after the December 2026 meeting?",
        "Will the Fed increase interest rates by 25 bps after the December 2026 meeting?",
        "Will the Fed increase interest rates by 50+ bps after the December 2026 meeting?"])]
    B = [mk(f"k{i}", t, "economics", venue="kalshi") for i, t in enumerate([
        "Fed decision in Dec 2026?: Cut >25bps", "Fed decision in Dec 2026?: Cut 25bps",
        "Fed decision in Dec 2026?: Fed maintains rate", "Fed decision in Dec 2026?: Hike 25bps",
        "Fed decision in Dec 2026?: Hike >25bps"])]
    got = {a.id: b.id for _, a, b in _match_outcomes(A, B)}
    assert got == {"p0": "k0", "p1": "k1", "p2": "k2", "p3": "k3", "p4": "k4"}


def test_audit_tail_cache_keeps_chain_valid(tmp_path):
    from frame.audit import AuditLog
    log = AuditLog(tmp_path / "a.jsonl")
    for i in range(50):
        log.append("x", {"i": i})
    assert AuditLog(tmp_path / "a.jsonl").verify() == (True, None)
    again = AuditLog(tmp_path / "a.jsonl")        # a new instance reads the tail from disk, then continues the chain
    again.append("x", {"i": 50})
    assert again.verify() == (True, None) and len(again.records()) == 51


def test_candidate_refusal_deferred_logged_once_per_pair(tmp_path):
    from frame.suggestions import SuggestionLog
    a, b = mk("1", "Fed cut?", "economics"), mk("2", "Fed cut 50bps?", "economics", venue="kalshi")
    log = SuggestionLog(tmp_path / "s.jsonl")
    for scan in ("s1", "s2", "s3"):
        log.candidate("p", a, b, "cross_venue", 0.8, scan)
        log.deferred("p", scan)
        log.rule_refused("q", a, b, "R3", "x", "cross_event", scan)
    assert log.counts() == {"candidate": 1, "deferred": 1, "rule_refused": 1}
    assert SuggestionLog(tmp_path / "s.jsonl")._seen == {("candidate", "p"), ("deferred", "p"), ("rule_refused", "q")}


def test_kalshi_numbers_not_stored(cfg, monkeypatch):
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")
    p = build(replay=True, cfg=cfg)
    p.scan()
    fed = next(c for c in p.review_queue() if c.judged.b.venue == "kalshi")
    p.confirm(fed.id, "yin")
    p.price(fed.id, 100)
    priced = [r["payload"] for r in p.suggestions.records() if r["kind"] == "priced"][-1]
    assert priced.get("kalshi_redacted") and "avg_edge" not in priced
    saved = cfg.confirmed_path.read_text()
    assert '"yes_ask": 0.58' not in saved   # the Kalshi fixture's ask
