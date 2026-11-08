import json
from types import SimpleNamespace

import pytest

from polysim import BookEvent, Market, ResolutionEvent, Token
from polysim.cli import main
from polysim.data import save_markets, write_events
from polysim.desk import format_report, live_snapshot, paper_trade
from polysim.risk import RiskLimits
from polysim.tickets import Ticket, TicketLeg, load_tickets, parse_ticket, save_tickets, ticket_from_core
from polysim_helpers import make_book

A_YES, A_NO, B_YES, B_NO = "A-YES", "A-NO", "B-YES", "B-NO"
A = Market("A", "Will X happen by Nov 30?", (Token(A_YES, "Yes"), Token(A_NO, "No")), min_order_size=1.0)
B = Market("B", "Will X happen by Dec 31?", (Token(B_YES, "Yes"), Token(B_NO, "No")), min_order_size=1.0)


def book(token, ts, asks=((0.42, 200),), bids=((0.40, 200),)):
    return BookEvent(ts, make_book(ts, bids=bids, asks=asks, token=token))


def ticket(tid="d-1", ts=10.0, shares=50, a_max=0.60, b_max=0.45, expected_cost=46.5, **kw):
    """A implies B, so A-NO + B-YES pays at least $1 a share."""
    legs = (TicketLeg(A_NO, "NO", a_max, "A", "A NO"), TicketLeg(B_YES, "YES", b_max, "B", "B YES"))
    return Ticket(tid, ts, legs, shares, 1.0, expected_cost, 50 - expected_cost, "a_implies_b", **kw)


def ladder_events(ts=10.0):
    return [book(A_NO, ts, asks=((0.55, 100),)), book(B_YES, ts, asks=((0.38, 100),))]


# --------------------------------------------------------------------------- tickets


def test_parse_ticket_refuses_what_the_desk_cannot_trade_safely():
    good = {"ticket_id": "t", "ts": 1, "shares": 5, "legs": [{"token_id": "x", "outcome": "yes", "max_price": 0.4}]}
    assert parse_ticket(good).legs[0].outcome == "YES"
    bad = [
        ({**good, "ticket_id": ""}, "ticket_id is missing"),
        ({**good, "shares": 0}, "shares must be positive"),
        ({**good, "legs": []}, "no legs"),
        ({**good, "legs": [{"token_id": "x", "outcome": "maybe"}]}, "outcome must be YES or NO"),
        ({**good, "legs": [{"token_id": "x", "outcome": "NO", "max_price": 1.2}]}, "not a price"),
        ({**good, "legs": [{"token_id": "x", "outcome": "NO"}, {"token_id": "x", "outcome": "YES"}]}, "same token"),
        ({**good, "floor": 0}, "floor must be positive"),
    ]
    for d, msg in bad:
        with pytest.raises(ValueError, match=msg):
            parse_ticket(d)


def test_tickets_round_trip_and_bad_lines_are_reported(tmp_path):
    p = tmp_path / "t.jsonl"
    save_tickets([ticket("a"), ticket("b", ts=11)], p)
    with p.open("a") as fh:
        fh.write("{not json\n" + json.dumps({"ticket_id": "c", "ts": 1, "shares": 1, "legs": []}) + "\n")
    got, problems = load_tickets(p)
    assert [t.ticket_id for t in got] == ["a", "b"] and got[0] == ticket("a")
    assert len(problems) == 2 and "line 3: not JSON" in problems[0] and "c: no legs" in problems[1]


def core_objects(status="OK", shares=40.0):
    """The shapes of arbcheck's Market / ArbResult / Decision, without importing arbcheck."""
    ma = SimpleNamespace(id="A", meta={"yes_token": A_YES, "no_token": A_NO})
    mb = SimpleNamespace(id="B", meta={"yes_token": B_YES, "no_token": B_NO})
    arb = SimpleNamespace(legs=(("A", "NO"), ("B", "YES")), floor=1.0, kind="a_implies_b", fills=[])
    fills = [SimpleNamespace(prices=(0.55, 0.38), shares=30.0), SimpleNamespace(prices=(0.56, 0.39), shares=10.0)]
    dec = SimpleNamespace(status=status, approved_shares=shares, approved_cost=37.5, approved_profit=2.5,
                          fills=fills, headline="Worth a look", haircut_per_share=0.0)
    return [ma, mb], arb, dec


def test_ticket_from_core_uses_the_right_tokens_and_the_worst_approved_price():
    markets, arb, dec = core_objects()
    t = ticket_from_core(markets, arb, dec, ts=123.0, decision_id="case-7")
    assert [(leg.token_id, leg.outcome, leg.max_price, leg.label) for leg in t.legs] == [
        (A_NO, "NO", 0.56, "A NO"), (B_YES, "YES", 0.39, "B YES")]
    assert (t.ticket_id, t.ts, t.shares, t.floor, t.expected_cost, t.expected_profit) == ("case-7", 123.0, 40.0, 1.0, 37.5, 2.5)
    assert t.meta["core_status"] == "OK" and t.note == "Worth a look"


def test_ticket_from_core_refuses_anything_the_guard_did_not_approve():
    for status in ("BLOCKED", "NO_ARB"):
        with pytest.raises(ValueError, match="did not approve"):
            ticket_from_core(*core_objects(status=status), ts=0)
    with pytest.raises(ValueError, match="0 shares"):
        ticket_from_core(*core_objects(shares=0), ts=0)
    markets, arb, dec = core_objects()
    markets[1].meta = {}
    with pytest.raises(ValueError, match="no YES token id"):
        ticket_from_core(markets, arb, dec, ts=0)


# --------------------------------------------------------------------------- the desk


def test_filled_ticket_reports_slippage_locked_profit_and_the_resolved_payout():
    events = ladder_events() + [ResolutionEvent(50, "A", {A_YES: 1.0, A_NO: 0.0}),
                                ResolutionEvent(60, "B", {B_YES: 1.0, B_NO: 0.0})]
    rep = paper_trade([ticket()], events, [A, B], latency=0.0)
    o = rep.outcomes[0]
    assert o.status == "filled" and o.started_ts == 10.0
    assert o.paper_cost == pytest.approx(50 * 0.55 + 50 * 0.38)
    assert o.slippage == pytest.approx(0.0) and o.locked_profit == pytest.approx(3.5)
    assert o.settled_payout == pytest.approx(50.0) and o.realised == pytest.approx(3.5) and o.floor_held
    assert all(order.decision_id == "d-1" for order in o.basket.orders)
    assert rep.result.audit_ok
    text = format_report(rep)
    assert "PAPER RESULT" in text and "floor held" in text and "filled 1" in text


def test_a_broken_relation_shows_up_as_a_broken_floor():
    """A resolves Yes but B resolves No: the world the relation said was impossible."""
    events = ladder_events() + [ResolutionEvent(50, "A", {A_YES: 1.0, A_NO: 0.0}),
                                ResolutionEvent(60, "B", {B_YES: 0.0, B_NO: 1.0})]
    o = paper_trade([ticket()], events, [A, B], latency=0.0).outcomes[0]
    assert o.settled_payout == 0.0 and o.floor_held is False and o.realised == pytest.approx(-46.5)


def test_the_desk_never_pays_more_than_the_checker_priced():
    rep = paper_trade([ticket(a_max=0.50)], ladder_events(), [A, B], latency=0.0)
    o = rep.outcomes[0]
    assert o.status == "unwound" and "leg 0 not filled" in o.reason and o.paper_cost == 0.0


def test_a_leg_that_vanishes_under_latency_is_unwound_and_costs_leg_risk():
    events = [
        book(A_NO, 10, asks=((0.55, 100),), bids=((0.50, 100),)),
        book(B_YES, 10, asks=((0.38, 100),)),
        book(A_NO, 11, asks=((0.55, 100),), bids=((0.50, 100),)),  # leg 0 arrives and fills
        book(B_YES, 11.5, asks=()),  # B's sellers leave
        book(A_NO, 12, asks=((0.55, 100),), bids=((0.50, 100),)),  # leg 1 arrives: nothing to buy
        book(A_NO, 13, asks=((0.55, 100),), bids=((0.50, 100),)),  # the unwind sells A-NO back
    ]
    o = paper_trade([ticket()], events, [A, B], latency=1.0).outcomes[0]
    assert o.status == "unwound" and o.leg_risk_cost == pytest.approx(2.5) and o.locked_profit is None


def test_stale_tickets_expire_and_late_books_are_waited_for():
    events = [book(A_NO, 0), book(B_YES, 0), book(A_NO, 100), book(B_YES, 101), book(A_NO, 1000), book(B_YES, 1000)]
    rep = paper_trade([ticket("waits", ts=50, b_max=0.45), ticket("stale", ts=200)], events, [A, B],
                      latency=0.0, max_delay=300, max_book_age=60)
    waits, stale = rep.outcomes
    assert waits.status == "filled" and waits.started_ts == 101  # B's book was 100 s old at t=100
    assert stale.status == "expired"


def test_risk_limits_and_duplicates_are_enforced_on_tickets():
    rep = paper_trade([ticket(), ticket()], ladder_events(), [A, B], latency=0.0,
                      risk_limits=RiskLimits(max_order_notional=10))
    assert [o.status for o in rep.outcomes] == ["rejected"] and "risk (leg 0)" in rep.outcomes[0].reason
    assert rep.problems == ["d-1: duplicate ticket id, only the first is traded"]


def test_tickets_after_the_data_ends_have_no_data():
    o = paper_trade([ticket(ts=99)], ladder_events(), [A, B]).outcomes[0]
    assert o.status == "no_data" and o.basket is None


# --------------------------------------------------------------------------- live + CLI


class FakeClient:
    def get_markets_by_condition_ids(self, ids):
        assert ids == ["A", "B"]
        return [A, B]

    def get_order_book(self, token):
        return make_book(0, asks=((0.55, 100),) if token == A_NO else ((0.38, 100),), token="")


def test_live_snapshot_fetches_only_the_leg_books_and_stamps_them_now():
    markets, events = live_snapshot(FakeClient(), [ticket(ts=990)], now=1000.0)
    assert {e.token_id for e in events} == {A_NO, B_YES} and all(e.ts == 1000.0 for e in events)
    rep = paper_trade([ticket(ts=990)], events, markets, latency=0.0)
    assert rep.outcomes[0].status == "filled"


def test_cli_paper_on_a_recording(tmp_path, capsys):
    write_events(tmp_path / "books.jsonl", ladder_events())
    save_markets(tmp_path / "markets.json", [A, B])
    save_tickets([ticket()], tmp_path / "tickets.jsonl")
    out_json = tmp_path / "report.json"
    rc = main(["paper", "--tickets", str(tmp_path / "tickets.jsonl"), "--events", str(tmp_path / "books.jsonl"),
               "--markets", str(tmp_path / "markets.json"), "--latency", "0", "--json", str(out_json),
               "--audit", str(tmp_path / "audit.jsonl")])
    assert rc == 0
    assert "PAPER RESULT on recorded Polymarket data" in capsys.readouterr().out
    rep = json.loads(out_json.read_text())
    assert rep["paper_trading"] is True and rep["counts"] == {"filled": 1} and rep["audit_ok"]
    assert main(["audit-verify", str(tmp_path / "audit.jsonl")]) == 0
