"""Smart watch loop, rules after the review, sports links, smart ordering and the test-data mode."""
import json
import time
from datetime import datetime, timedelta, timezone

from frame import priority, sports, testdata
from frame.llm import FakeLLM
from frame.rules import check_pair
from frame.schemas import Market
from frame.watch import WatchItem, WatchList, interval_s


def soon(days=0.0, hours=0.0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def mk(i, title="Fed cut?", cat="economics", venue="polymarket", close=None, **kw):
    return Market(venue=venue, id=i, title=title, category=cat, close_time_utc=close or soon(5), rules_text=title, **kw)


# ---------------------------------------------------------------- rules after the review

def test_r0_same_market_settled_and_already_closed():
    a = mk("1")
    assert check_pair(a, a).rule == "R0"
    assert check_pair(a, mk("2", prob=0.99)).rule == "R0"
    assert check_pair(a, mk("3", close=soon(-1))).rule == "R5"


def test_time_rules_do_not_depend_on_argument_order():
    a, b = mk("1", close=soon(1)), mk("2", close=soon(15.5))      # 14.5 days apart: refused either way
    assert check_pair(a, b).rule == check_pair(b, a).rule == "R6"


def test_nested_ladders_are_not_refused_by_r6():
    a, b = mk("1", close=soon(2)), mk("2", close=soon(30))
    assert check_pair(a, b).rule == "R6" and check_pair(a, b, nested=True).ok


def test_r3_matches_any_shared_category():
    trade = mk("1", cat="politics", tags=["politics", "trade-war"])
    econ = mk("2", cat="economics", venue="kalshi", tags=["Economics"])
    assert check_pair(trade, econ).ok


# ---------------------------------------------------------------- the watch list

def test_interval_tightens_towards_the_deadline_and_when_warm():
    now = time.time()
    iso = lambda h: datetime.fromtimestamp(now + h * 3600, timezone.utc).isoformat()
    assert interval_s(iso(24 * 10), now) == 6 * 3600
    assert interval_s(iso(24 * 3), now) == 3600
    assert interval_s(iso(30), now) == 20 * 60
    assert interval_s(iso(10), now) == 5 * 60
    assert interval_s(iso(1), now) == 60 and interval_s(iso(24 * 10), now, live=True) == 60
    assert interval_s(iso(24 * 10), now, last_edge=-0.005) == 3 * 3600        # within a cent: twice as often


def test_watchlist_due_record_drop_and_no_kalshi_numbers(tmp_path):
    wl = WatchList(tmp_path / "w.json")
    poly = WatchItem("p", "single", "same-market", "x", [{"venue": "polymarket"}], soon(hours=1))
    kal = WatchItem("k", "single", "same-market", "y", [{"venue": "kalshi"}], soon(hours=1))
    gone = WatchItem("g", "single", "same-market", "z", [{"venue": "polymarket"}], soon(-1))
    for it in (poly, kal, gone):
        wl.add(it, now=time.time() - 3600)
    due = wl.due()
    assert {i.key for i in due} == {"p", "k"}                                 # closed one dropped
    wl.record(poly, "nothing_to_do", -0.004)
    wl.record(kal, "nothing_to_do", -0.004)
    assert poly.last_edge == -0.004 and kal.last_edge is None
    wl.save()
    saved = {d["key"]: d for d in json.loads((tmp_path / "w.json").read_text())}
    assert saved["k"]["last_edge"] is None and saved["p"]["last_edge"] == -0.004
    assert WatchList(tmp_path / "w.json").items.keys() == {"p", "k"}


# ---------------------------------------------------------------- sports

def test_exact_score_implies_the_result_inside_one_game():
    g = dict(game_id="90114167", start_time_utc=soon(hours=1))
    ms = [mk("w1", "Will Crystal Palace FC win on 2026-11-08?", "sports", sport_type="moneyline", **g),
          mk("dr", "Will Crystal Palace FC vs. Liverpool FC end in a draw?", "sports", sport_type="moneyline", **g),
          mk("w2", "Will Liverpool FC win on 2026-11-08?", "sports", sport_type="moneyline", **g),
          mk("e01", "Exact Score: Crystal Palace FC 0 - 1 Liverpool FC?", "sports", sport_type="soccer_exact_score", **g),
          mk("e11", "Exact Score: Crystal Palace FC 1 - 1 Liverpool FC?", "sports", sport_type="soccer_exact_score", **g),
          mk("e20", "Exact Score: Crystal Palace FC 2 - 0 Liverpool FC?", "sports", sport_type="soccer_exact_score", **g)]
    links = {a.id: b.id for a, b, _ in sports.game_links(ms)}
    assert links == {"e01": "w2", "e11": "dr", "e20": "w1"}


def test_in_play_window():
    assert sports.in_play(mk("1", start_time_utc=soon(hours=-1)))
    assert not sports.in_play(mk("2", start_time_utc=soon(hours=1)))
    assert not sports.in_play(mk("3", start_time_utc=soon(hours=-3)))


# ---------------------------------------------------------------- smart ordering

def test_priority_puts_a_real_gap_soon_first():
    from frame.schemas import CoreResult
    from frame.watch import WatchItem
    hot = WatchItem("a", "ladder", "logical", "a", [], soon(hours=3), last_edge=-0.002)
    cold = WatchItem("b", "ladder", "logical", "b", [], soon(30), last_edge=-0.08)
    assert priority.score_watch(hot)[0] > priority.score_watch(cold)[0]

    class F:  # a finding-shaped object
        def __init__(self, verdict, edge, close):
            self.core = CoreResult(verdict=verdict, status="X", headline="", kind="k", avg_edge=edge, naive_edge=edge,
                                   core_version="test")
            self.arb_type, self.legs = "multi-outcome", [mk("x", close=close)]
    assert priority.score_finding(F("worth_a_look", 0.03, soon(20)))[0] > priority.score_finding(F("nothing_to_do", -0.02, soon(hours=2)))[0]


# ---------------------------------------------------------------- test-data mode

ROWS_MAPPING = {
    "file_format": "csv", "records_path": "", "layout": "rows",
    "fields": {"id": "market_ref", "title": "question", "close_time_utc": "closes", "event_id": "group",
               "category": "tag", "exclusive": "one_of_set"},
    "row_side": "side", "row_bid_value": "BID", "row_price": "px_cents", "row_size": "qty",
    "price_scale": "cents", "book_is_for": "yes", "venue_constant": "polymarket"}


def test_gemini_maps_fields_code_copies_numbers_and_the_mapping_is_cached(tmp_path, cfg):
    raw, out, maps = tmp_path / "raw", tmp_path / "norm", tmp_path / "maps"
    testdata.write_sample(raw)
    llm = FakeLLM(lambda call, **_: ROWS_MAPPING if call == "map_testdata" else None)
    doc = testdata.convert(cfg, llm, raw, out, maps, say=lambda s: None)
    assert len(doc["markets"]) == 5 and len(llm.calls) == 1
    m8 = next(m for m in doc["markets"] if m["id"] == "ex-marathon-8")
    assert m8["yes_bids"][0] == [0.41, 800.0] and m8["yes_asks"][0] == [0.43, 250.0]     # cents -> dollars, by code
    assert {m["id"] for m in doc["markets"] if m["exclusive"]} == {"ex-home", "ex-draw", "ex-away"}
    testdata.convert(cfg, llm, raw, out, maps, say=lambda s: None)
    assert len(llm.calls) == 1                                                           # cached: no second call
    rows = testdata.core_report(out / "markets.json", say=lambda s: None, report=tmp_path / "r.md")
    ladder = next(r for r in rows if r["type"] == "Logical link")
    assert ladder["verdict"] == "worth_a_look" and rows[0] is ladder                     # the planted inversion


def test_downward_threshold_ladder_is_blocked_not_reported_as_arbitrage():
    """The core's extractor orders '(LOW)' thresholds like upward ones (CORE_REQUESTS #6); the frame blocks them."""
    close = soon(20)
    doc = {"as_of": datetime.now(timezone.utc).isoformat(), "markets": [
        {"venue": "polymarket", "id": "low80", "title": "Will WTI Crude Oil (WTI) hit (LOW) $80 in November?",
         "rules_text": "Resolves Yes if WTI trades at or below $80 in November.", "close_time_utc": close,
         "category": "economics", "event_id": "wti-nov", "yes_bids": [[0.40, 500]], "yes_asks": [[0.41, 500]]},
        {"venue": "polymarket", "id": "low70", "title": "Will WTI Crude Oil (WTI) hit (LOW) $70 in November?",
         "rules_text": "Resolves Yes if WTI trades at or below $70 in November.", "close_time_utc": close,
         "category": "economics", "event_id": "wti-nov", "yes_bids": [[0.07, 500]], "yes_asks": [[0.075, 500]]}]}
    ad = testdata.make_adapter(doc)
    a, b = ad.list_open_markets(["polymarket"])
    r = ad.screen_pair(a, b)
    assert r.verdict == "blocked" and "threshold_direction_unverified" in r.flags


def test_nested_windows_across_events_link_the_narrow_to_the_wide():
    from frame.windows import window_links
    W = lambda i, t, close, ev: Market(venue="polymarket", id=i, title=t, close_time_utc=close, event_id=ev, category="crypto")
    ms = [W("w", "Will Bitcoin reach $90,000 November 2-8?", "2026-11-09T05:00:00Z", "week"),
          W("m", "Will Bitcoin reach $90,000 in November?", "2026-12-01T05:00:00Z", "month"),
          W("y", "Will Bitcoin reach $90,000 by December 31, 2026?", "2027-01-01T05:00:00Z", "year"),
          W("d", "Will Bitcoin dip to $90,000 in November?", "2026-12-01T05:00:00Z", "month"),      # other direction
          W("o", "Will Bitcoin reach $95,000 in November?", "2026-12-01T05:00:00Z", "month"),      # other threshold
          W("late", "Will Bitcoin reach $90,000 by December 31, 2026?", "2027-01-01T05:00:00Z", "late-year")]
    links = {(a.id, b.id) for a, b, _ in window_links(ms, {"year": "2026-08-01T00:00:00Z", "late-year": "2026-11-05T00:00:00Z"})}
    # 'late' (a 'by Dec 31' created Nov 5) sits inside the Aug-created one, but NOT around the Nov 2-8 week or
    # November: a hit on Nov 3 would not count for it, so no link from the week or the month to it.
    assert links == {("w", "m"), ("w", "y"), ("m", "y"), ("late", "y")}


def test_recording_drops_stale_and_non_simultaneous_books(tmp_path):
    import gzip
    root = tmp_path / "rec"
    (root / "events").mkdir(parents=True)
    end = (datetime.now(timezone.utc) + timedelta(days=5)).timestamp()
    (root / "markets.json").write_text(json.dumps([
        {"market_id": "m1", "question": "Q1?", "tokens": [["y1", "Yes"], ["n1", "No"]], "end_ts": end, "event_slug": "e"},
        {"market_id": "m2", "question": "Q2?", "tokens": [["y2", "Yes"], ["n2", "No"]], "end_ts": end, "event_slug": "e"}]))
    t0 = time.time()
    rows = [{"type": "book", "ts": t0 - 1000, "token_id": "y2", "bids": [[0.4, 10]], "asks": [[0.5, 10]]},   # stale
            {"type": "book", "ts": t0, "token_id": "y1", "bids": [[0.40, 10]], "asks": [[0.42, 10]]},
            {"type": "book", "ts": t0 + 30, "token_id": "n1", "bids": [[0.50, 10]], "asks": [[0.52, 10]]}]  # 30 s later
    with gzip.open(root / "books-1.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")
    ts, books = list(testdata._polysim_rounds(root))[-1]
    doc = testdata.polysim_doc(root, books, ts)
    assert [m["id"] for m in doc["markets"]] == ["m1"]                 # m2's only book is 1,000 s old: dropped
    assert "no_asks" not in doc["markets"][0]                          # No book 30 s apart: mirror used instead


def test_bad_rows_are_rejected_not_guessed():
    text = "id,q,c,side,p,s\nm1,Q,2027-01-01T00:00:00Z,bid,150,10\nm2,Q,2027-01-01T00:00:00Z,ask,40,10\n" \
           "m3,Q,,ask,40,10\nm4,Q,2027-01-01T00:00:00Z,bid,60,10\nm4,Q,2027-01-01T00:00:00Z,ask,50,10\n"
    m = testdata.Mapping.model_validate({**ROWS_MAPPING, "fields": {"id": "id", "title": "q", "close_time_utc": "c"},
                                         "row_side": "side", "row_bid_value": "bid", "row_price": "p", "row_size": "s"})
    got, problems = testdata.apply_mapping(text, m)
    assert [g["id"] for g in got] == ["m2"]
    assert any("outside 0-1" in p for p in problems) and any("no close time" in p for p in problems) \
        and any("crossed" in p for p in problems)
