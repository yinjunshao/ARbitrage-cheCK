import pytest

from frame import candidates as cand
from frame.pipeline import TransitionError, build


@pytest.fixture
def pipe(cfg, monkeypatch):
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")
    return build(replay=True, cfg=cfg)


def test_candidates_cross_venue_and_within_event(markets):
    ms = [m for m in markets.values() if m.category != "crypto"]
    pairs = {(a.id, b.id) for a, b, _ in cand.generate(ms, cand.HashingEmbedder(), 8, 7)}
    assert ("SAMPLE-pm-ukge-mar27", "SAMPLE-pm-ukge-jun27") in pairs  # within event
    assert ("SAMPLE-pm-fed-dec26", "SAMPLE-KXFED-26DEC") in pairs     # cross venue
    assert not any("KXEPL" in a + b for a, b in pairs)                # category/date blocking


def test_end_to_end_replay(pipe):
    cases = pipe.scan()
    queue = {c.judged.a.id: c for c in pipe.review_queue()}
    assert set(queue) == {"SAMPLE-pm-fed-dec26", "SAMPLE-pm-ukge-mar27"}
    assert not any("crypto" in c.judged.a.id + c.judged.b.id for c in cases)

    case = queue["SAMPLE-pm-fed-dec26"]
    with pytest.raises(TransitionError):
        pipe.price(case.id, 100)  # cannot price before a human confirms
    pipe.confirm(case.id, "yin")
    core = pipe.price(case.id, 100)
    assert core.core_version == "FAKE-0"
    assert case.narrated.grounded and case.narrated.source == "llm"
    assert case.narrated.narrative.recommendation in ("review", "kill")
    pipe.decide(case.id, "review", "yin")
    assert pipe.audit.verify() == (True, None)
    assert pipe.labels.all()[0]["confirmed"] is True


def test_reject_records_label(pipe):
    pipe.scan()
    c = pipe.review_queue()[0]
    pipe.reject(c.id, "yin", "different events")
    assert c.state == "rejected" and pipe.labels.all()[0]["confirmed"] is False


def test_cannot_approve_blocked(pipe):
    pipe.scan()
    c = pipe.review_queue()[0]
    pipe.confirm(c.id, "yin")
    pipe.price(c.id, 100)
    c.core = c.core.model_copy(update={"verdict": "blocked"})
    with pytest.raises(ValueError):
        pipe.decide(c.id, "approved", "yin")


def test_manual_pair_always_reviewed(pipe, markets):
    c = pipe.check_pair(markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXEPL-27"])
    assert c.state == "awaiting_confirm"


def test_fewshot_excludes_sealed_and_same_event(pipe, markets):
    from frame.fewshot import FewShot, event_key, split_for
    a, b = markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"]
    pipe.labels.add("p1", a, b, "same_event", True)
    fs = FewShot(pipe.labels, cand.HashingEmbedder())
    assert fs.retrieve(a, b) == []  # same event never retrieved for itself
    assert split_for(event_key(a, b)) in ("dev", "sealed")
