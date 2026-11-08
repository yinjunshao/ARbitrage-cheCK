"""The board's rows come from the core's results, and the calculator agrees with the core on the same book."""
import pytest

from frame import board, calc
from frame.llm import FakeLLM
from frame.pipeline import build


def _pipeline(cfg, monkeypatch):
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")

    def responder(call, user, schema, key):
        if call == "judge_pair":
            if "Orion Tech" in user and "December 31, 2026" in user and "March 31, 2027" in user:
                return {"relation": "a_implies_b", "confidence": "high", "reasoning": "nested deadlines",
                        "evidence_a": ["by December 31, 2026, 11:59 PM ET"], "evidence_b": ["by March 31, 2027, 11:59 PM ET"]}
            return {"relation": "unrelated", "confidence": "high", "reasoning": "no", "evidence_a": [], "evidence_b": []}
        if call == "compare_rules":
            return {"overall": "compatible", "differences": []}
        return None
    from dataclasses import replace
    p = build(demo=True, llm=FakeLLM(responder),
              cfg=replace(cfg, core="arbcheck", max_days_to_close=3650, max_close_gap_days=3650, max_singles_per_scan=6))
    p.scan()
    for c in list(p.review_queue()):
        p.confirm(c.id, "t")
        p.price(c.id, 300)
    return p


def test_rows_carry_per_leg_quotes_and_the_four_stages_only_fall(cfg, monkeypatch):
    p = _pipeline(cfg, monkeypatch)
    rows = board.rows(p)
    assert {r.kind for r in rows} >= {"verified", "structure"}
    v = next(r for r in rows if r.kind == "verified" and r.real)
    assert len(v.legs) == 2 and all(q["price"] and q["venue"] for q in v.legs) and v.floor >= 1.0
    s = v.stages
    assert s["price_only"] >= s["at_asks"] >= s["after_fees"] - 1e-9        # the gap can only shrink as costs are applied
    assert v.score > 0 and v.why


def test_calculator_on_the_cores_own_book_matches_the_core(cfg, monkeypatch):
    """Same legs, same order-book levels, the core's floor: the calculator's profit at the core's size equals the core's
    (fees use the core's own coefficient)."""
    p = _pipeline(cfg, monkeypatch)
    v = next(r for r in board.rows(p) if r.kind == "verified" and r.real)
    core = p.cases[v.id].core
    # the demo market's fee coefficient, read from the core's fee name ("... 0.04 × price × (1 − price)")
    import re
    coeffs = [float(re.search(r"([0-9.]+) × price", name).group(1)) for name in core.fees.values()]
    legs = [calc.Leg("polymarket", q["side"], q["price"], c, [tuple(b) for b in lv])
            for q, c, lv in zip(v.legs, coeffs, v.levels)]
    curve = calc.size_curve(legs, [core.shares], payout=v.floor)
    assert curve[0]["contracts"] == pytest.approx(core.shares, rel=1e-6)
    assert curve[0]["profit"] == pytest.approx(core.net_profit, abs=0.05)      # fee rounding only


def test_saved_board_round_trips_and_never_stores_kalshi(cfg, monkeypatch, tmp_path):
    p = _pipeline(cfg, monkeypatch)
    live = board.rows(p)
    assert live
    # one Kalshi row must be dropped from the file; pretend the first row is a Kalshi one
    live_ids = {r.id for r in live}
    first = live[0]
    first.venues = ["kalshi"]
    p.screens.clear()
    path = tmp_path / "board.json"
    board.save(p, path)
    rows, saved_at = board.load(path)
    assert saved_at > 0 and rows and all(r.saved for r in rows)
    assert {r.id for r in rows} <= live_ids
    assert all("kalshi" not in r.venues for r in rows)
    a = next(r for r in rows if r.legs)                       # quotes and order-book levels survive the trip
    assert a.legs[0]["price"] and a.levels and a.stages["after_fees"] is not None


def test_a_missing_or_broken_saved_board_is_just_empty(tmp_path):
    assert board.load(tmp_path / "nope.json") == ([], 0.0)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert board.load(tmp_path / "bad.json") == ([], 0.0)


def test_restart_shows_saved_rows_until_the_first_live_scan_completes(cfg, monkeypatch):
    from dataclasses import replace
    p = _pipeline(cfg, monkeypatch)
    board.save(p, cfg.board_path)
    monkeypatch.setenv("FRAME_EMBEDDER", "hashing")
    p2 = build(demo=True, llm=FakeLLM(lambda **_: None), cfg=replace(cfg, core="arbcheck"))
    assert p2.snapshot_active and p2.last_scan_at > 0
    shown = board.rows(p2)
    assert shown and all(r.saved for r in shown)                # nothing priced yet this session: all saved rows
    p2.snapshot_active = False
    assert board.rows(p2) == []                                 # after a live scan the saved copy is gone


def test_board_sorts_put_real_arbitrage_first(cfg, monkeypatch):
    p = _pipeline(cfg, monkeypatch)
    rows = sorted(board.rows(p), key=board.SORTS["Best first (recommended)"])
    assert rows[0].real
    assert board.age_text(rows[0].at) in ("0s", "1s", "2s", "3s")
