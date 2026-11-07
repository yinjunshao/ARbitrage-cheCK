import gzip
import json

from polysim import BookEvent, ResolutionEvent
from polysim.data import load_markets, read_event_files
from polysim.data.recorder import EventRecorder, open_markets_of
from polysim.models import OrderBook, PriceLevel


def gamma_market(i, closed=False, outcome="[\"0\", \"1\"]"):
    return {
        "conditionId": f"0x{i}", "question": f"X by date {i}?", "slug": f"x-by-{i}",
        "outcomes": '["Yes","No"]', "clobTokenIds": f'["{i}Y","{i}N"]', "groupItemTitle": f"date {i}",
        "active": True, "closed": closed, "enableOrderBook": True, "outcomePrices": outcome,
        "feesEnabled": True, "feeType": "politics_fees", "feeSchedule": {"rate": 0.04, "exponent": 1, "takerOnly": True},
    }


class FakeClient:
    def __init__(self):
        self.closed = set()
        self.fail_tokens = set()
        self.calls = 0

    def get_event_raw(self, slug):
        return {"slug": slug, "markets": [gamma_market(i, closed=i in self.closed) for i in (1, 2)]}

    def get_order_book(self, token_id):
        self.calls += 1
        if token_id in self.fail_tokens:
            raise ConnectionError("blip")
        levels = [PriceLevel(round(0.01 * k, 2), 10) for k in range(1, 40)]
        return OrderBook(token_id, 0, levels[:30], levels[30:])


class FakeClock:
    def __init__(self):
        self.t = 1_767_225_600.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_open_markets_skips_closed_ones():
    ev = {"markets": [gamma_market(1), gamma_market(2, closed=True)]}
    assert [m.market_id for m in open_markets_of(ev)] == ["0x1"]


def test_recorder_writes_trimmed_books_markets_and_resolutions(tmp_path):
    client, clock = FakeClient(), FakeClock()
    rec = EventRecorder(client, ["ladder"], tmp_path, interval=60, depth=5, rate=1000, refresh=120,
                        clock=clock, sleep=clock.sleep)
    rec.refresh_events()
    assert rec.record_round() == 4  # 2 markets x 2 tokens

    client.closed.add(2)  # market 2 resolves (No won)
    client.fail_tokens.add("1N")  # and one request fails
    rec.refresh_events()
    assert rec.record_round() == 1  # only 1Y: 1N failed, market 2 is no longer recorded
    rec.refresh_events()  # a second refresh must not write the result twice

    books = [json.loads(line) for line in gzip.open(next(tmp_path.glob("books-*.jsonl.gz")), "rt")]
    assert len(books) == 5 and all(len(b["bids"]) <= 5 and len(b["asks"]) <= 5 for b in books)
    assert [json.loads(line)["market_id"] for line in (tmp_path / "resolutions.jsonl").read_text().splitlines()] == ["0x2"]
    markets = load_markets(tmp_path / "markets.json")
    assert {m.market_id for m in markets} == {"0x1", "0x2"}
    assert markets[0].event_slug == "ladder" and markets[0].fee_rate == 0.04 and markets[0].group_title == "date 1"
    assert (tmp_path / "events" / "ladder.json").exists()
    assert rec.errors == 1 and "blip" in (tmp_path / "recorder.log").read_text()

    events = read_event_files([tmp_path])
    kinds = [type(e) for e in events]
    assert kinds.count(BookEvent) == 5 and kinds.count(ResolutionEvent) == 1
    assert [e.ts for e in events] == sorted(e.ts for e in events)


def test_recorder_respects_the_request_rate(tmp_path):
    client, clock = FakeClient(), FakeClock()
    rec = EventRecorder(client, ["ladder"], tmp_path, rate=2, clock=clock, sleep=clock.sleep)
    rec.refresh_events()
    t0 = clock.t
    rec.record_round()
    assert clock.t - t0 >= 4 / 2 - 1e-9  # 4 requests at <= 2 per second


def test_recorder_run_stops_when_every_market_has_closed(tmp_path):
    client, clock = FakeClient(), FakeClock()
    client.closed.update({1, 2})
    rec = EventRecorder(client, ["ladder"], tmp_path, clock=clock, sleep=clock.sleep)
    rec.markets = {}  # nothing recorded yet, so closed markets are not "ours" and get no result line
    rec.run(duration=3600)
    log = (tmp_path / "recorder.log").read_text()
    assert "no open markets left; stopping" in log and "done:" in log


def test_restarting_keeps_known_markets_and_results(tmp_path):
    client, clock = FakeClient(), FakeClock()
    rec = EventRecorder(client, ["ladder"], tmp_path, clock=clock, sleep=clock.sleep)
    rec.refresh_events()
    client.closed.add(2)
    rec.refresh_events()
    again = EventRecorder(client, ["ladder"], tmp_path, clock=clock, sleep=clock.sleep)
    assert again.resolved == {"0x2"} and set(again.markets) == {"0x1", "0x2"}
    again.refresh_events()
    assert len((tmp_path / "resolutions.jsonl").read_text().splitlines()) == 1
