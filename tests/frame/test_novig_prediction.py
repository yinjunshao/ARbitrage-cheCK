"""Novig mapping (offline, recorded shapes from the public API) and the prediction.com claim handling."""
from datetime import datetime, timedelta, timezone

from frame import novig, predictioncom, sports
from frame.adapter import ArbcheckAdapter
from frame.schemas import Market

START = int((datetime.now(timezone.utc) + timedelta(hours=3)).timestamp() * 1000)
MKT = {"marketId": "01a19d4c-4075-7a10-b9ab-1e681043254a", "description": "Aston Villa MONEYLINE_3_WAY_WIN",
       "eventId": "ev1", "marketType": "MONEYLINE_3_WAY_WIN", "status": "OPEN", "voids": "FMV", "startsTs": START,
       "fee": {"coefficient": "0.06", "makerCredit": "0.25", "charged": "WHEN_LIVE"},
       "outcomes": [{"outcomeId": "yes1", "name": "Yes"}, {"outcomeId": "no1", "name": "No"}]}
EV = {"eventId": "ev1", "description": "Aston Villa FC @ Manchester United FC", "league": "EPL", "startsTs": START}
BOOK = {"marketId": MKT["marketId"], "orders": {
    "yes1": [{"price": "0.30", "qty": 25000}, {"price": "0.30", "qty": 15000}, {"price": "0.29", "qty": 922}],
    "no1": [{"price": "0.69", "qty": 71460}]}}


class FakeClient:
    def get(self, path, params=None):
        assert path.startswith("/catalog/")
        return BOOK


def test_novig_books_follow_novigs_own_definition():
    yb, ya, nb, na = novig.books(FakeClient(), MKT)
    assert yb[0] == (0.30, 400.0)                 # 25,000 + 15,000 one-cent contracts = 400 $1 shares
    assert ya[0] == (0.31, 714.6)                 # YES ask = 1 - best NO bid
    assert na[0] == (0.70, 400.0)                 # NO ask = 1 - best YES bid


def test_novig_market_reaches_the_core_with_fee_and_labelled_rules():
    ad = ArbcheckAdapter(demo=True)
    cm = novig.core_market(FakeClient(), MKT, EV, ad.models, ad.fees, hydrate=True)
    assert cm.venue == "Novig" and cm.fee.k == 0.06 and cm.yes.best_ask == 0.31
    assert "Will Aston Villa win on" in cm.question and "publishes no rule text" in cm.rules
    r = ad.pipeline.evaluate_case([cm], ad.relations.structural_relation("complement", "t", "structure"),
                                  ad.guard_mod.Guard(ad.guard_mod.Limits()), datetime.now(timezone.utc))
    assert r.decision.status in ("NO_ARB", "BLOCKED", "WARN", "OK")


def test_same_match_on_two_venues_is_matched_by_code():
    k = datetime.fromtimestamp(START / 1000, timezone.utc).isoformat()
    day = k[:10]
    m = lambda v, i, t: Market(venue=v, id=i, title=t, category="sports", start_time_utc=k, sport_type="moneyline",
                               close_time_utc=k)
    ms = [m("novig", "n1", f"Will Man Utd win on {day}? (Aston Villa FC @ Manchester United FC)"),
          m("polymarket", "p1", f"Will Manchester United FC win on {day}?"),
          m("polymarket", "p2", f"Will Aston Villa FC win on {day}?"),
          m("novig", "n2", f"Will Aston Villa FC @ Manchester United FC end in a draw on {day}?"),
          m("polymarket", "p3", "Will Aston Villa FC vs. Manchester United FC end in a draw?")]
    got = {(a.id, b.id) for a, b, _ in sports.cross_venue_games(ms)}
    assert got == {("n1", "p1"), ("n2", "p3")}


def test_prediction_com_claim_is_kept_as_theirs():
    opp = {"group_title": "Lazio vs. Napoli", "roi_pct": 9.7, "max_wager_usd": 479, "total_cost": 0.9,
           "detected_at": "2026-11-08T12:00:00Z",
           "legs": [{"side": "yes", "platform": "polymarket", "market_id": "1", "price": 0.27, "liquidity_usd": 332,
                     "fee_usd": 0.01, "source_url": "https://polymarket.com/event/x"},
                    {"side": "no", "platform": "predictit", "market_id": "2", "price": 0.63}]}
    c = predictioncom.claim(opp)
    assert c["source"] == "prediction.com" and c["roi_pct"] == 9.7 and len(c["legs"]) == 2
    a, b, note = predictioncom.resolve_legs(ArbcheckAdapter(demo=True), opp)
    assert a is None and "predictit" in note                     # a venue we don't read is skipped, not guessed


def test_prediction_com_client_needs_a_key_and_stays_on_the_allowlist(monkeypatch):
    monkeypatch.delenv("PREDICTION_API_KEY", raising=False)
    import pytest
    with pytest.raises(PermissionError):
        predictioncom.Client().arb()
    with pytest.raises(PermissionError):
        predictioncom.Client("k").get("/../admin")
