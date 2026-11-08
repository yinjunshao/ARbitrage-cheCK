import json

import pytest

from polysim import BookEvent, Market, OrderBook, PriceLevel, ResolutionEvent, Token
from polysim.cli import main
from polysim.data import save_markets, write_events
from polysim.eval_demo import make_eval_dataset
from polysim.evaluation import (
    EvalConfig,
    MarketData,
    evaluate,
    format_report,
    parse_candidate,
    parse_candidates,
)

T = 1_000_000.0
FAST = EvalConfig(bootstrap=200)


def ladder(asks_a=((0.55, 500),), asks_b=((0.40, 500),), world=(0, 0), resolve=True, b_asks_after=None):
    """One A-implies-B pair; the basket is A-NO + B-YES (floor $1). Fee rate 0.04 on both."""
    kw = dict(fee_rate=0.04, min_order_size=1.0)
    mk = [Market("A", "A?", (Token("A-YES", "Yes"), Token("A-NO", "No")), **kw),
          Market("B", "B?", (Token("B-YES", "Yes"), Token("B-NO", "No")), **kw)]
    ev = []
    for k in range(10):
        ts = T - 1 + k
        ev.append(BookEvent(ts, OrderBook("A-NO", ts, [PriceLevel(asks_a[0][0] - 0.02, 400)],
                                          [PriceLevel(p, s) for p, s in asks_a])))
        b = asks_b if (b_asks_after is None or k < 2) else b_asks_after
        ev.append(BookEvent(ts, OrderBook("B-YES", ts, [PriceLevel(asks_b[0][0] - 0.02, 400)],
                                          [PriceLevel(p, s) for p, s in b])))
    if resolve:
        ev.append(ResolutionEvent(T + 86_400, "A", {"A-YES": float(world[0]), "A-NO": float(1 - world[0])}))
        ev.append(ResolutionEvent(T + 86_400, "B", {"B-YES": float(world[1]), "B-NO": float(1 - world[1])}))
    return mk, ev


def cand(**kw):
    d = {"id": "c1", "ts": T, "token_ids": ["A-NO", "B-YES"], "floor": 1.0, "rule_check": "match"}
    return parse_candidate({**d, **kw})


def run(mk, ev, *cands, cfg=FAST, **kw):
    return evaluate(list(cands), MarketData(ev, mk), cfg, **kw)


def test_a_real_arbitrage_is_taken_and_scores_its_guaranteed_profit():
    rep = run(*ladder(), cand())
    r = rep.results[0]
    assert r.taken and r.outcome == "win"
    # 100 baskets at 0.55 + 0.40, plus fees 0.04*p*(1-p): 0.0099 + 0.0096 per basket
    assert r.ours["pnl"] == pytest.approx(100 * (1 - 0.9695))
    assert r.naive["pnl"] == pytest.approx(100 * (1 - 0.9695))
    assert rep.stats["funnel"]["profitable_at_resolution"] == 1


def test_luck_above_the_floor_is_not_counted_as_arbitrage_profit():
    rep = run(*ladder(world=(0, 1)), cand())  # both legs win: $2 per basket
    o = rep.results[0].ours
    assert o["pnl_total"] == pytest.approx(200 - 96.95)
    assert o["windfall"] == pytest.approx(100) and o["pnl"] == pytest.approx(3.05)


def test_a_rule_mismatch_is_blocked_and_the_naive_loss_is_counted_as_avoided():
    rep = run(*ladder(world=(1, 0)), cand(rule_check="mismatch"))  # the "impossible" world
    r = rep.results[0]
    assert not r.taken and r.blocked_at == "rules" and r.outcome == "correct_block"
    assert r.naive["pnl"] == pytest.approx(-96.95)
    assert rep.stats["loss_avoided"] == pytest.approx(96.95)


def test_unclear_rules_are_blocked_and_a_good_trade_shows_up_as_a_miss():
    rep = run(*ladder(), cand(rule_check="unclear"))
    assert rep.results[0].outcome == "miss"
    assert rep.stats["profit_missed"] == pytest.approx(3.05)
    assert "miss" in format_report(rep)


def test_fees_and_spread_can_eat_an_edge_that_mids_show():
    # mids sum to 0.97 (looks like 3c free), asks sum to 0.99, fees ~2c more
    rep = run(*ladder(asks_a=((0.56, 500),), asks_b=((0.43, 500),)), cand())
    r = rep.results[0]
    assert r.stages["naive"] and not r.stages["fees"] and r.blocked_at == "fees"
    assert r.naive["pnl"] < 0 and r.outcome == "correct_block"


def test_thin_books_are_blocked_at_depth():
    rep = run(*ladder(asks_a=((0.50, 10), (0.60, 500))), cand())
    r = rep.results[0]
    assert r.depth_shares == pytest.approx(10) and r.blocked_at == "depth"
    assert r.naive["pnl"] < 0  # the naive checker bought 90 more at 0.60


def test_sellers_vanishing_during_the_delay_is_a_false_alarm_with_leg_risk():
    rep = run(*ladder(b_asks_after=()), cand())
    r = rep.results[0]
    assert r.taken and r.outcome == "false_alarm"
    assert r.ours["status"] == "unwound" and r.ours["leg_risk_cost"] > 0
    assert r.ours["pnl"] == pytest.approx(-r.ours["leg_risk_cost"])


def test_unresolved_trades_stay_open_and_are_not_scored():
    rep = run(*ladder(resolve=False), cand())
    assert rep.results[0].outcome == "open"
    assert rep.stats["still_open"] == 1 and rep.stats["ours_trades"] == 0


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"floor": 3.0}, "impossible for 2 leg(s)"),
        ({"token_ids": ["A-NO", "A-NO"]}, "appears twice"),
        ({"token_ids": ["A-NO", "ZZZ"]}, "unknown token"),
        ({"ts": T + 600}, "stale"),
        ({"ts": T + 2 * 86_400}, "already resolved"),
        ({"ts": T - 100}, "no recorded book"),
    ],
)
def test_bad_or_manipulated_candidates_are_rejected_with_a_reason(change, reason):
    rep = run(*ladder(), cand(**change))
    assert rep.results == [] and reason in rep.rejected_inputs[0]["reason"]


def test_crossed_books_are_rejected():
    mk, ev = ladder()
    ev.append(BookEvent(T + 0.5, OrderBook("A-NO", T + 0.5, [PriceLevel(0.60, 10)], [PriceLevel(0.55, 10)])))
    ev.sort(key=lambda e: e.ts)
    rep = run(mk, ev, cand(ts=T + 0.5))  # flagged on the crossed snapshot
    assert "crossed book" in rep.rejected_inputs[0]["reason"]


def test_malformed_json_candidates_are_rejected_not_crashing():
    good, bad = parse_candidates([{"id": "x"}, {"id": "y", "ts": "soon", "token_ids": ["a"], "floor": 1},
                                  "not an object", {"id": "z", "ts": 1, "token_ids": ["a"], "floor": 1, "rule_check": "maybe"}])
    assert good == [] and len(bad) == 4
    assert "missing field" in bad[0]["reason"] and "rule_check" in bad[3]["reason"]


def test_demo_tunes_on_the_first_half_and_tests_once_on_the_second():
    mk, ev, raw, split = make_eval_dataset()
    cands, bad = parse_candidates(raw)
    rep = evaluate(cands, MarketData(ev, mk), FAST, split_ts=split, rejected_inputs=bad)
    assert rep.period == "held-out test"
    assert all(r.candidate.ts >= split for r in rep.results)
    assert all(r.candidate.ts < split for r in rep.tune_results)
    assert rep.chosen_min_edge in FAST.edge_grid
    f = rep.stats["funnel"]
    assert f["naive_flags"] >= f["after_fees"] >= f["after_depth"] >= f["after_rule_check"] >= f["profitable_at_resolution"]
    o = rep.stats["outcomes"]
    assert o["false_alarm"] > 0 and o["miss"] > 0 and o["correct_block"] > 0  # losses are shown, not hidden
    assert len(rep.rejected_inputs) == 6
    assert rep.intervals["ours_minus_naive_pnl"][0] > 0
    assert format_report(rep).startswith("PAPER EVALUATION")


def test_without_a_split_the_report_says_it_is_in_sample():
    rep = run(*ladder(), cand())
    assert rep.period.startswith("in-sample") and rep.tuning == []


def test_cli_evaluates_recorded_files(tmp_path, capsys):
    mk, ev, raw, split = make_eval_dataset(n=12)
    save_markets(tmp_path / "m.json", mk)
    write_events(tmp_path / "e.jsonl", ev)
    (tmp_path / "c.jsonl").write_text("\n".join(json.dumps(c) for c in raw) + "\nnot json\n")
    out = tmp_path / "r.json"
    code = main(["evaluate", "--candidates", str(tmp_path / "c.jsonl"), "--events", str(tmp_path / "e.jsonl"),
                 "--markets", str(tmp_path / "m.json"), "--split-ts", str(split), "--bootstrap", "100",
                 "--json", str(out)])
    assert code == 0
    report = json.loads(out.read_text())
    assert report["paper_trading"] is True and report["period"] == "held-out test"
    assert any("not valid JSON" in x["reason"] for x in report["rejected_inputs"])
    assert "recorded Polymarket data" in capsys.readouterr().out
