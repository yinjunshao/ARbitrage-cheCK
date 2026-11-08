"""The core -> paper desk hand-over, with the REAL arbcheck core.

Runs wherever arbcheck is importable (the integrated branch keeps it in arbcheck-project/);
skipped on branches that only have polysim.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "arbcheck-project").is_dir():
    sys.path.insert(0, str(ROOT / "arbcheck-project"))
arbcheck = pytest.importorskip("arbcheck")

from arbcheck.guard import Guard  # noqa: E402
from arbcheck.models import Book, Level, Market as CoreMarket  # noqa: E402
from arbcheck.pipeline import evaluate_case  # noqa: E402
from arbcheck.relations import structural_relation  # noqa: E402

from polysim import BookEvent, Market, Token  # noqa: E402
from polysim.desk import paper_trade  # noqa: E402
from polysim.tickets import ticket_from_core  # noqa: E402
from polysim_helpers import make_book  # noqa: E402

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
TS = NOW.timestamp()
BOOKS = {  # token -> (bids, asks): "by Nov 30" (A) implies "by Dec 31" (B), and A-NO + B-YES costs 0.93
    "A-YES": (((0.40, 500),), ((0.46, 500),)),
    "A-NO": (((0.50, 500),), ((0.55, 300), (0.56, 300))),
    "B-YES": (((0.36, 500),), ((0.38, 200), (0.39, 400))),
    "B-NO": (((0.58, 500),), ((0.64, 500),)),
}


def core_market(mid, yes, no, days):
    book = lambda t: Book([Level(p, s) for p, s in BOOKS[t][1]], [Level(p, s) for p, s in BOOKS[t][0]])
    return CoreMarket(mid, f"Will X happen ({mid})?", f"Rules for {mid}.", NOW + timedelta(days=days),
                      book(yes), book(no), meta={"yes_token": yes, "no_token": no})


def test_an_approved_core_decision_paper_fills_at_the_profit_the_core_promised():
    markets = [core_market("A", "A-YES", "A-NO", 20), core_market("B", "B-YES", "B-NO", 50)]
    rel = structural_relation("a_implies_b", "by Nov 30 implies by Dec 31", "structure")
    case = evaluate_case(markets, rel, Guard(), NOW)
    assert case.decision.status in ("OK", "WARN"), case.decision.headline

    ticket = ticket_from_core(case.markets, case.arb, case.decision, ts=TS, decision_id="case-1")
    assert {(leg.token_id, leg.outcome) for leg in ticket.legs} == {("A-NO", "NO"), ("B-YES", "YES")}

    sim_markets = [Market(m.id, m.question, (Token(m.meta["yes_token"], "Yes"), Token(m.meta["no_token"], "No")),
                          min_order_size=1.0, fee_rate=0.0) for m in markets]
    events = [BookEvent(TS, make_book(TS, bids=b, asks=a, token=t)) for t, (b, a) in BOOKS.items()]
    out = paper_trade([ticket], events, sim_markets, latency=0.0).outcomes[0]
    assert out.status == "filled"
    assert out.paper_cost == pytest.approx(case.decision.approved_cost, abs=1e-6)
    assert out.locked_profit == pytest.approx(ticket.guaranteed_value - case.decision.approved_cost, abs=1e-6)
    assert all(o.decision_id == "case-1" for o in out.basket.orders)
