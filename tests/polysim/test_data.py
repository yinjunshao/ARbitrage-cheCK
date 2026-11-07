import json
from pathlib import Path

import pytest

from polysim import BookEvent, PriceEvent, ResolutionEvent
from polysim.data import (
    generate_binary_market,
    load_markets,
    merge_feeds,
    parse_book,
    parse_gamma_market,
    parse_price_history,
    parse_resolution,
    read_events,
    save_markets,
    write_events,
)
from polysim.data.polymarket import PolymarketClient

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_parse_gamma_market():
    m = parse_gamma_market(load("gamma_market.json"))
    assert m.market_id == "0xabc123"
    assert m.token("yes").token_id == "111" and m.token("No").token_id == "222"
    assert m.tick_size == 0.001 and m.min_order_size == 5
    assert m.end_ts == pytest.approx(1798718400)


def test_parse_resolution_from_closed_market():
    res = parse_resolution(load("gamma_market.json"))
    assert res.payouts == {"111": 1.0, "222": 0.0}


def test_parse_book():
    b = parse_book(load("clob_book.json"))
    assert b.token_id == "111" and b.ts == pytest.approx(1767225600)
    assert b.best_bid == 0.47 and b.best_ask == 0.50


def test_parse_price_history():
    evs = parse_price_history({"history": [{"t": 1, "p": 0.4}, {"t": 2, "p": 0.45}]}, "111")
    assert evs == [PriceEvent(1.0, "111", 0.4), PriceEvent(2.0, "111", 0.45)]


def test_jsonl_and_markets_roundtrip(tmp_path):
    market, events = generate_binary_market(seed=1)
    write_events(tmp_path / "ev.jsonl", events)
    back = list(read_events(tmp_path / "ev.jsonl"))
    assert len(back) == len(events)
    assert isinstance(back[0], BookEvent) and isinstance(back[-1], ResolutionEvent)
    assert back[0].book.bids[0].price == events[0].book.bids[0].price
    save_markets(tmp_path / "m.json", [market])
    assert load_markets(tmp_path / "m.json")[0].token_ids == market.token_ids


def test_merge_feeds_orders_by_time():
    a = [PriceEvent(1, "a", 0.1), PriceEvent(3, "a", 0.1)]
    b = [PriceEvent(2, "b", 0.1)]
    assert [e.ts for e in merge_feeds(a, b)] == [1, 2, 3]


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self.routes[url.rsplit("/", 1)[-1]])


def test_client_uses_expected_endpoints():
    s = FakeSession({"markets": [load("gamma_market.json")], "book": load("clob_book.json"),
                     "prices-history": {"history": [{"t": 5, "p": 0.5}]}})
    c = PolymarketClient(session=s)
    assert c.get_market("will-it-rain-in-london-tomorrow").slug == "will-it-rain-in-london-tomorrow"
    assert c.get_order_book("111").best_ask == 0.50
    assert c.get_price_history("111", fidelity=10)[0].price == 0.5
    url, params = s.calls[-1]
    assert url.endswith("/prices-history") and params == {"market": "111", "interval": "max", "fidelity": 10}
