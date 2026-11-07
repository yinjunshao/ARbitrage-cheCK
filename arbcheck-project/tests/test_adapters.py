"""Venue adapters, tested against canned JSON shaped like each venue's documented responses.

The build sandbox could not reach the real APIs, so these prove our PARSING and our request
choices, not that the venues' live responses still look like this. Run a live check first.
"""
import json
from datetime import datetime, timezone

from arbcheck.compliance import ReadOnlyClient
from arbcheck.kalshi import Kalshi, market_from as kalshi_market, parse_orderbook
from arbcheck.manifold import Manifold, market_from as manifold_market
from arbcheck.polymarket import Polymarket, market_stub, parse_book
from arbcheck.venues import Registry, build_registry


class Router:
    """Fake transport: maps URL substrings to JSON bodies, records what was asked for."""
    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def __call__(self, url, headers, timeout):
        self.urls.append(url)
        for needle, body in self.routes.items():
            if needle in url:
                if isinstance(body, tuple):
                    return body[0], b"", {}
                return 200, json.dumps(body).encode(), {}
        return 404, b"", {}


def make(routes, kalshi=False):
    r = Router(routes)
    c = ReadOnlyClient(kalshi_enabled=kalshi, transport=r, clock=lambda: 0.0, sleep=lambda s: None,
                       min_interval={"gamma-api.polymarket.com": 0, "clob.polymarket.com": 0,
                                     "api.manifold.markets": 0, "api.elections.kalshi.com": 0})
    return c, r


# ---------------------------------------------------------------- Polymarket
def pm_raw(slug="harbor", mid="1", question="Will Harbor FC win?", **extra):
    base = {"id": mid, "slug": slug, "question": question, "description": "Resolves Yes if Harbor wins.",
            "resolutionSource": "Federation site", "endDate": "2026-12-20T23:59:00Z",
            "outcomes": '["Yes", "No"]', "clobTokenIds": f'["y{mid}", "n{mid}"]',
            "outcomePrices": '["0.41", "0.59"]', "active": True, "closed": False}
    base.update(extra)
    return base


def test_polymarket_market_stub_parses_the_important_fields():
    m = market_stub(pm_raw(), {"slug": "cup", "negRisk": True, "category": "Sports"})
    assert m.venue == "Polymarket" and m.tradeable and m.group == "cup"
    assert m.meta["yes_token"] == "y1" and m.meta["no_token"] == "n1" and m.meta["exclusive"] is True
    assert m.meta["exhaustive"] is False                        # never assumed
    assert "Federation site" in m.rules and m.prob == 0.41
    assert m.fee.k == 0.05 and m.end.tzinfo is not None


def test_polymarket_unknown_category_assumes_the_higher_fee():
    m = market_stub(pm_raw(), {"slug": "e"})
    assert m.fee.k == 0.05 and "assumed" in m.fee.name


def test_polymarket_drops_markets_it_cannot_trade_or_understand():
    assert market_stub(pm_raw(outcomes='["Harbor", "Redmoor"]')) is None
    assert market_stub(pm_raw(closed=True)) is None
    assert market_stub(pm_raw(endDate="garbage")) is None
    bad = pm_raw(); del bad["clobTokenIds"]
    assert market_stub(bad) is None


def test_polymarket_fee_free_flag_and_bad_fee_schedule():
    assert market_stub(pm_raw(feesEnabled=False)).fee.k == 0
    assert market_stub(pm_raw(feeSchedule={"rate": 0.04})).fee.k == 0.04
    assert market_stub(pm_raw(feeSchedule={"rate": 9})).fee.k == 0.05     # implausible: ignored


def test_parse_book_sorts_and_reads_strings():
    b = parse_book({"asks": [{"price": "0.6", "size": "10"}, {"price": "0.5", "size": "5"}],
                    "bids": [{"price": "0.4", "size": "7"}]})
    assert b.best_ask == 0.5 and b.best_bid == 0.4 and b.asks[0].size == 5


def test_polymarket_parse_url():
    p = Polymarket(make({})[0])
    assert p.parse_url("https://polymarket.com/event/cup/harbor") == {"kind": "event", "slug": "cup", "market_slug": "harbor"}
    assert p.parse_url("https://polymarket.com/market/harbor")["kind"] == "market"
    assert p.parse_url("https://kalshi.com/markets/x") is None
    assert p.parse_url("not a url") is None


def test_polymarket_lookup_event_marks_the_pointed_market_as_linked():
    event = {"slug": "cup", "negRisk": True, "markets": [pm_raw("harbor", "1"), pm_raw("redmoor", "2")]}
    c, r = make({"/events": [event]})
    ms = Polymarket(c).lookup({"kind": "event", "slug": "cup", "market_slug": "redmoor"})
    assert {m.id: m.meta["linked"] for m in ms} == {"1": False, "2": True}
    ms = Polymarket(c).lookup({"kind": "event", "slug": "cup", "market_slug": None})
    assert all(m.meta["linked"] for m in ms)


def test_polymarket_lookup_falls_back_to_a_single_market():
    c, r = make({"/events": [], "/markets": [pm_raw("solo", "9")]})
    ms = Polymarket(c).lookup({"kind": "market", "slug": "solo", "market_slug": None})
    assert [m.id for m in ms] == ["9"]


def test_polymarket_search_and_siblings_and_hydrate():
    event = {"slug": "cup", "negRisk": True, "markets": [pm_raw("harbor", "1"), pm_raw("redmoor", "2")]}
    book = {"asks": [{"price": "0.43", "size": "100"}], "bids": [{"price": "0.41", "size": "100"}]}
    c, r = make({"/public-search": {"events": [event]}, "/events": [event], "/book": book})
    p = Polymarket(c)
    found = p.search("harbor")
    assert len(found) == 2
    assert [m.id for m in p.siblings(found[0])] == ["2"]
    p.hydrate(found[0])
    assert found[0].yes.best_ask == 0.43 and found[0].no.best_ask == 0.43
    assert any("token_id=y1" in u for u in r.urls) and any("token_id=n1" in u for u in r.urls)


# ---------------------------------------------------------------- Kalshi
def k_raw(ticker="KXA-26DEC", **extra):
    base = {"ticker": ticker, "event_ticker": "KXA", "title": "Will A happen?", "status": "active",
            "market_type": "binary", "close_time": "2026-12-31T23:59:00Z", "yes_bid_dollars": "0.48",
            "yes_ask_dollars": "0.52", "rules_primary": "Resolves Yes if A happens.",
            "rules_secondary": "Source: official statement."}
    base.update(extra)
    return base


def test_kalshi_market_parsing_new_and_old_price_fields():
    m = kalshi_market(k_raw(), {"title": "A event", "mutually_exclusive": True})
    assert m.venue == "Kalshi" and m.tradeable and m.fee.k == 0.07 and m.fee.cent_round
    assert m.prob == 0.5 and m.group == "KXA" and m.meta["exclusive"] is True
    assert "Source: official statement." in m.rules
    old = kalshi_market({k: v for k, v in k_raw().items() if "dollars" not in k} | {"yes_bid": 48, "yes_ask": 52})
    assert old.prob == 0.5


def test_kalshi_skips_closed_or_non_binary():
    assert kalshi_market(k_raw(status="closed")) is None
    assert kalshi_market(k_raw(market_type="scalar")) is None
    assert kalshi_market(k_raw(close_time=None, expiration_time=None)) is None


def test_kalshi_orderbook_is_bids_only_so_asks_are_derived():
    yes, no = parse_orderbook({"orderbook_fp": {"yes_dollars": [["0.40", "100"], ["0.38", "50"]],
                                                "no_dollars": [["0.55", "80"]]}})
    assert yes.best_bid == 0.40 and no.best_bid == 0.55
    assert yes.best_ask == 0.45 and yes.asks[0].size == 80       # YES ask = 1 - best NO bid
    assert no.best_ask == 0.60 and no.asks[0].size == 100        # NO ask = 1 - best YES bid
    yes2, no2 = parse_orderbook({"orderbook": {"yes": [[40, 100]], "no": [[55, 80]]}})
    assert yes2.best_ask == 0.45 and no2.best_ask == 0.60


def test_kalshi_is_refused_when_not_enabled_even_if_called_directly():
    c, r = make({"kalshi.com": {"markets": []}}, kalshi=False)
    try:
        Kalshi(c).lookup({"ticker": "KXA"})
        raised = False
    except Exception as e:
        raised = type(e).__name__ == "Refused"
    assert raised and r.urls == []


def test_kalshi_parse_url_and_lookup_event_then_market():
    k = Kalshi(make({}, kalshi=True)[0])
    assert k.parse_url("https://kalshi.com/markets/kxa/some-title/kxa-26dec") == {"ticker": "KXA-26DEC"}
    assert k.parse_url("https://polymarket.com/event/x") is None
    event = {"event": {"event_ticker": "KXA", "title": "A event", "mutually_exclusive": False,
                       "markets": [k_raw("KXA-26DEC"), k_raw("KXA-27MAR")]}}
    c, r = make({"/events/KXA": event}, kalshi=True)
    assert len(Kalshi(c).lookup({"ticker": "KXA"})) == 2
    c, r = make({"/events/": (404,), "/markets/KXA-26DEC": {"market": k_raw()}}, kalshi=True)
    assert len(Kalshi(c).lookup({"ticker": "KXA-26DEC"})) == 1


def test_kalshi_search_ranks_locally_and_hydrate_fills_both_books():
    events = {"events": [{"event_ticker": "KXA", "title": "A", "markets": [k_raw("KXA-26DEC", title="Will Zephyr Bank cut rates?")]},
                         {"event_ticker": "KXB", "title": "B", "markets": [k_raw("KXB-26DEC", event_ticker="KXB", title="Who wins the cup?")]}],
              "cursor": ""}
    ob = {"orderbook_fp": {"yes_dollars": [["0.40", "10"]], "no_dollars": [["0.55", "10"]]}}
    c, r = make({"/orderbook": ob, "/events": events}, kalshi=True)
    k = Kalshi(c)
    found = k.search("Will Zephyr Bank cut rates?")
    assert found[0].id == "KXA-26DEC"
    k.hydrate(found[0])
    assert found[0].yes.best_ask == 0.45 and found[0].no.best_ask == 0.60


# ---------------------------------------------------------------- Manifold
def mf_raw(**extra):
    base = {"id": "m1", "question": "Will A happen?", "outcomeType": "BINARY", "probability": 0.31,
            "closeTime": 1798761540000, "isResolved": False, "url": "https://manifold.markets/u/a",
            "textDescription": "Resolves YES if A."}
    base.update(extra)
    return base


def test_manifold_markets_are_signal_only():
    m = manifold_market(mf_raw())
    assert m.venue == "Manifold" and m.tradeable is False and m.prob == 0.31 and not m.has_book
    assert manifold_market(mf_raw(outcomeType="MULTIPLE_CHOICE")) is None
    assert manifold_market(mf_raw(isResolved=True)) is None
    assert manifold_market(mf_raw(probability=None)) is None
    far = manifold_market(mf_raw(closeTime=None))
    assert far.end.year == 2100


def test_manifold_lookup_search_and_parse_url():
    c, r = make({"/slug/a": mf_raw(), "/search-markets": [mf_raw(), mf_raw(id="m2", outcomeType="PSEUDO_NUMERIC")]})
    mf = Manifold(c)
    assert mf.parse_url("https://manifold.markets/u/a") == {"slug": "a"}
    assert len(mf.lookup({"slug": "a"})) == 1
    assert len(mf.search("a")) == 1
    assert mf.siblings(mf.lookup({"slug": "a"})[0]) == []


# ---------------------------------------------------------------- Registry
def test_unreadable_links_are_never_fetched():
    c, r = make({})
    reg = build_registry(c)
    res = reg.resolve("https://evil.example.com/steal?x=1")
    assert res.kind == "unreadable" and r.urls == []


def test_registry_text_search_finds_matches_and_reports_misses():
    event = {"slug": "cup", "markets": [pm_raw("harbor", "1", "Will Harbor FC win the Northvale Cup?")]}
    c, r = make({"/public-search": {"events": [event]}, "/search-markets": []})
    res = build_registry(c).resolve("Will Harbor FC win the Northvale Cup?")
    assert [m.market.id for m in res.matches] == ["1"] and res.matches[0].score > 0.9
    far = build_registry(c).resolve("completely unrelated banana question")
    assert far.matches == [] and "No close match" in far.notes[0]


def test_one_venue_failing_does_not_stop_the_others():
    event = {"slug": "cup", "markets": [pm_raw("harbor", "1", "Will Harbor FC win the Northvale Cup?")]}
    c, r = make({"/public-search": {"events": [event]}, "/search-markets": (500,)})
    reg = build_registry(c)
    res = reg.resolve("Will Harbor FC win the Northvale Cup?")
    assert len(res.matches) == 1
    assert any("Manifold" in n for n in reg.notes)


def test_kalshi_is_skipped_with_a_reason_unless_member_on_loopback():
    c, _ = make({})
    assert "Kalshi" not in build_registry(c).venues and "Kalshi" in build_registry(c).skipped
    reg = build_registry(make({})[0], kalshi_member=True, loopback=False)
    assert "Kalshi" not in reg.venues and "another computer" in reg.skipped["Kalshi"]
    c2, _ = make({})
    reg = build_registry(c2, kalshi_member=True, loopback=True)
    assert "Kalshi" in reg.venues and c2.kalshi_enabled is True
