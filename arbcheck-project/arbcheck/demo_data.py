"""SYNTHETIC demo markets. Every company, product and price here is invented.

They exist so the whole pipeline can be run, tested and demoed offline, and each pair is
built to show one situation a trader really meets:

  1. Orion Tech      a REAL arbitrage: rules match, gap survives costs
  2. Helix Motors    a FALSE arbitrage: looks like 9 cents, but the rules differ
  3. Kestrel Air     consistent prices: nothing to do
  4. Marlow Pharma   looks like 3 cents on mid-prices, but wide spreads and thin books kill it
  5. Polar Grid      a real gap with a minor rule difference (time zone): trade with care
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from .models import Book, Level, Market
from .persistence import SnapshotStore

UTC = timezone.utc
DEMO_NOW = datetime(2026, 11, 7, 16, 0, tzinfo=UTC)

# 11:59 PM Eastern on Dec 31 2026 / Mar 31 2027 (EST = UTC-5, EDT = UTC-4), and UTC deadlines.
END_DEC_ET = datetime(2027, 1, 1, 4, 59, tzinfo=UTC)
END_MAR_ET = datetime(2027, 4, 1, 3, 59, tzinfo=UTC)
END_MAR_UTC = datetime(2027, 3, 31, 23, 59, tzinfo=UTC)


def binary(yes_bids, yes_asks) -> tuple[Book, Book]:
    """Build mirrored YES and NO books. On a binary market a bid for YES at p is the same
    thing as an offer of NO at 1-p, so the NO book is the complement of the YES book."""
    yes = Book(asks=[Level(p, s) for p, s in yes_asks], bids=[Level(p, s) for p, s in yes_bids])
    no = Book(asks=[Level(round(1 - p, 4), s) for p, s in yes_bids],
              bids=[Level(round(1 - p, 4), s) for p, s in yes_asks])
    return yes, no


def _market(mid: str, question: str, rules: str, end: datetime, yes_bids, yes_asks) -> Market:
    yes, no = binary(yes_bids, yes_asks)
    return Market(mid, question, rules, end, yes, no, url="(synthetic demo market)")


def demo_pairs() -> list[tuple[Market, Market]]:
    orion_rules = ("Resolves Yes if Orion Tech publicly announces a stock split by {d}, 11:59 PM ET. "
                   "The announcement must appear in a press release or SEC filing. "
                   "Resolution source: Orion Tech investor relations page.")
    orion = (
        _market("orion-dec", "Will Orion Tech announce a stock split by December 31, 2026?",
                orion_rules.format(d="December 31, 2026"), END_DEC_ET,
                [(0.42, 300), (0.40, 500)], [(0.44, 200), (0.46, 400)]),
        _market("orion-mar", "Will Orion Tech announce a stock split by March 31, 2027?",
                orion_rules.format(d="March 31, 2027"), END_MAR_ET,
                [(0.33, 300), (0.31, 500)], [(0.35, 150), (0.37, 300)]),
    )
    helix = (
        _market("helix-dec", "Will Helix Motors recall the H3 model by December 31, 2026?",
                "Resolves Yes if a recall notice covering the Helix H3 model is published by NHTSA "
                "by December 31, 2026, 11:59 PM ET. Resolution source: NHTSA recall database.",
                END_DEC_ET, [(0.30, 400)], [(0.32, 300), (0.34, 300)]),
        _market("helix-mar", "Will Helix Motors recall the H3 model by March 31, 2027?",
                "Resolves Yes if Helix Motors itself publicly announces a recall of the Helix H3 model "
                "by March 31, 2027, 11:59 PM UTC. Resolution source: Helix Motors press releases.",
                END_MAR_UTC, [(0.21, 300), (0.19, 400)], [(0.23, 250), (0.25, 300)]),
    )
    kestrel_rules = ("Resolves Yes if Kestrel Airlines publicly announces a new route to Lisbon by {d}, "
                     "11:59 PM ET. Resolution source: Kestrel Airlines press releases.")
    kestrel = (
        _market("kestrel-dec", "Will Kestrel Airlines announce a new route to Lisbon by December 31, 2026?",
                kestrel_rules.format(d="December 31, 2026"), END_DEC_ET, [(0.17, 300)], [(0.19, 300)]),
        _market("kestrel-mar", "Will Kestrel Airlines announce a new route to Lisbon by March 31, 2027?",
                kestrel_rules.format(d="March 31, 2027"), END_MAR_ET, [(0.33, 300)], [(0.35, 300)]),
    )
    marlow_rules = ("Resolves Yes if the FDA approves MRL-204 by {d}, 11:59 PM ET. "
                    "Resolution source: FDA approvals database.")
    marlow = (
        _market("marlow-dec", "Will the FDA approve MRL-204 by December 31, 2026?",
                marlow_rules.format(d="December 31, 2026"), END_DEC_ET, [(0.27, 40)], [(0.35, 40)]),
        _market("marlow-mar", "Will the FDA approve MRL-204 by March 31, 2027?",
                marlow_rules.format(d="March 31, 2027"), END_MAR_ET, [(0.24, 40)], [(0.32, 40)]),
    )
    polar_rules = ("Resolves Yes if Polar Grid Energy publicly announces a merger agreement by {d}, "
                   "11:59 PM {tz}. Resolution source: Polar Grid Energy press releases.")
    polar = (
        _market("polar-dec", "Will Polar Grid Energy announce a merger by December 31, 2026?",
                polar_rules.format(d="December 31, 2026", tz="ET"), END_DEC_ET,
                [(0.24, 200)], [(0.26, 200)]),
        _market("polar-mar", "Will Polar Grid Energy announce a merger by March 31, 2027?",
                polar_rules.format(d="March 31, 2027", tz="UTC"), END_MAR_UTC,
                [(0.17, 200)], [(0.19, 150), (0.21, 200)]),
    )
    return [orion, helix, kestrel, marlow, polar]


# Hand labels for scoring extractors: what a careful human would say about each pair.
DEMO_LABELS = [
    {"kind": "a_implies_b", "has_high_flag": False},   # Orion
    {"kind": "a_implies_b", "has_high_flag": True},    # Helix (source + definition differ)
    {"kind": "a_implies_b", "has_high_flag": False},   # Kestrel
    {"kind": "a_implies_b", "has_high_flag": False},   # Marlow
    {"kind": "a_implies_b", "has_high_flag": False},   # Polar (time zone only: low)
]


def demo_snapshots(store: SnapshotStore, pair_id: str, minutes: int = 3 * 24 * 60,
                   seed: int = 7) -> None:
    """SYNTHETIC persistence data: a two-state random process, one snapshot per minute.

    A gap opens with probability 1% per minute and closes with probability 8% per minute
    (mean life about 12 minutes). It only demonstrates the survival-curve maths; replace
    it with real collector output before drawing any conclusion about Polymarket.
    """
    rng = random.Random(seed)
    t0 = DEMO_NOW
    live = False
    for i in range(minutes):
        live = (rng.random() > 0.08) if live else (rng.random() < 0.01)
        ts = datetime.fromtimestamp(t0.timestamp() + 60 * i, tz=UTC)
        store.record(pair_id, ts, 0.05 if live else -0.02, 150.0 if live else 0.0)


# ======================================================================================
# A SYNTHETIC multi-venue universe for the "check my idea" app (python -m arbcheck serve --demo)
#
# Everything below is invented. It lets anyone try the app with no internet and no accounts.
# The three venues are labelled "Polymarket", "Kalshi" and "Manifold" only so the screens
# look like the real thing; the app shows a banner saying so. Situations covered:
#
#   Vega Index          a price-threshold link on one venue (5,500 implies 5,000)
#   Zephyr Bank         the same event on two venues, rules agree: a real cross-venue gap
#   Atlas Corp CEO      the same event on two venues, rules differ: looks like a gap, is not
#   Northvale Cup       four mutually exclusive outcomes priced to add up to more than 100%
#   Quill Games         one market whose Yes and No together cost under $1
#   Zephyr (Manifold)   a play-money price far from the real-money one: a signal only
#   Orion/Helix/etc.    the nested-deadline pairs above
# ======================================================================================
END_VEGA = datetime(2026, 12, 31, 21, 0, tzinfo=UTC)
END_ZEPHYR = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
END_ATLAS = datetime(2027, 3, 31, 23, 59, tzinfo=UTC)
END_CUP = datetime(2026, 12, 20, 23, 59, tzinfo=UTC)
END_QUILL = datetime(2027, 2, 28, 23, 59, tzinfo=UTC)


def _tag(m: Market, venue: str, fee, group: str = "", slug: str = "", exclusive: bool = False,
         tradeable: bool = True, prob: float | None = None) -> Market:
    m.venue, m.fee, m.group, m.tradeable, m.prob = venue, fee, group, tradeable, prob
    m.url = f"https://{venue.lower()}.example/demo/{slug or m.id}"
    m.meta = {"slug": slug or m.id, "exclusive": exclusive, "exhaustive": False, "demo": True}
    return m


def demo_markets() -> list[Market]:
    from .fees import KALSHI_FEE, NO_FEE, polymarket_fee
    poly_fin, poly_other = polymarket_fee("finance"), polymarket_fee("other")
    out: list[Market] = []

    # Existing nested-deadline pairs, placed on Polymarket.
    for a, b in demo_pairs():
        for m in (a, b):
            out.append(_tag(m, "Polymarket", poly_other, group=m.id.split("-")[0], slug=m.id))

    # Vega Index thresholds: a higher threshold implies the lower one, so the lower one can
    # never be cheaper. Here the 5,500 contract is priced ABOVE the 5,000 contract.
    vega_rules = ("Resolves Yes if the Vega 500 Index official closing level on December 31, 2026 is "
                  "above {n}. Resolution source: Vega Exchange published closing level.")
    out.append(_tag(_market("vega-5000", "Will the Vega 500 close above 5,000 on December 31, 2026?",
                            vega_rules.format(n="5,000"), END_VEGA, [(0.38, 400)], [(0.40, 400), (0.42, 300)]),
                    "Polymarket", poly_fin, group="vega", slug="vega-5000"))
    out.append(_tag(_market("vega-5500", "Will the Vega 500 close above 5,500 on December 31, 2026?",
                            vega_rules.format(n="5,500"), END_VEGA, [(0.46, 400)], [(0.48, 300), (0.50, 300)]),
                    "Polymarket", poly_fin, group="vega", slug="vega-5500"))

    # Zephyr Bank: same event on Polymarket and Kalshi, same rules, prices disagree.
    zeph_rules = ("Resolves Yes if Zephyr Bank announces a cut to its benchmark interest rate on or before "
                  "December 31, 2026, 11:59 PM UTC. Resolution source: Zephyr Bank official statement.")
    out.append(_tag(_market("zephyr-poly", "Will Zephyr Bank cut interest rates by December 31, 2026?",
                            zeph_rules, END_ZEPHYR, [(0.38, 500)], [(0.40, 500), (0.42, 400)]),
                    "Polymarket", poly_fin, slug="zephyr-rate-cut"))
    out.append(_tag(_market("KXZEPHYR-26DEC", "Will Zephyr Bank cut interest rates by December 31, 2026?",
                            zeph_rules, END_ZEPHYR, [(0.50, 500)], [(0.52, 500), (0.54, 400)]),
                    "Kalshi", KALSHI_FEE, slug="KXZEPHYR-26DEC"))
    mz = _tag(_market("mf-zephyr", "Will Zephyr Bank cut interest rates by December 31, 2026?",
                      "Resolves YES if Zephyr Bank cuts its rate in 2026.", END_ZEPHYR, [], []),
              "Manifold", NO_FEE, slug="zephyr-bank-rate-cut", tradeable=False, prob=0.31)
    out.append(mz)

    # Atlas Corp: wide cross-venue gap, but one venue counts a firing and the other does not.
    out.append(_tag(_market("atlas-poly", "Will Atlas Corp's CEO leave by March 31, 2027?",
                            "Resolves Yes if the person serving as Atlas Corp CEO on today's date leaves the role for "
                            "any reason, including being fired, by March 31, 2027, 11:59 PM UTC. Resolution source: "
                            "Atlas Corp SEC filings.", END_ATLAS, [(0.25, 400)], [(0.27, 400)]),
                    "Polymarket", poly_other, slug="atlas-ceo-leaves"))
    out.append(_tag(_market("KXATLAS-27MAR", "Will Atlas Corp's CEO resign by March 31, 2027?",
                            "Resolves Yes only if the Atlas Corp CEO voluntarily resigns by March 31, 2027, "
                            "11:59 PM UTC. A termination or firing does not count. Resolution source: "
                            "Atlas Corp press release.", END_ATLAS, [(0.10, 400)], [(0.12, 400)]),
                    "Kalshi", KALSHI_FEE, slug="KXATLAS-27MAR"))

    # Northvale Cup: four mutually exclusive outcomes, priced to add up to 105%.
    cup_rules = ("Resolves Yes if {t} wins the Northvale Cup final on or before December 20, 2026. "
                 "Resolution source: Northvale Football Federation. Exactly one team wins.")
    for t, bid, ask in [("Harbor FC", 0.41, 0.43), ("Redmoor United", 0.31, 0.33),
                        ("Stonebridge", 0.21, 0.23), ("Dunmere Town", 0.15, 0.17)]:
        out.append(_tag(_market(f"cup-{t.split()[0].lower()}", f"Will {t} win the Northvale Cup?",
                                cup_rules.format(t=t), END_CUP, [(bid, 300)], [(ask, 300)]),
                        "Polymarket", polymarket_fee("sports"), group="northvale-cup",
                        slug=f"cup-{t.split()[0].lower()}", exclusive=True))

    # Quill Games: one market whose Yes and No together cost less than $1.
    q = _market("quill-release", "Will Quill Games release Starfall before February 28, 2027?",
                "Resolves Yes if Quill Games releases Starfall on any platform before February 28, 2027, "
                "11:59 PM UTC. Resolution source: Quill Games official store pages.",
                END_QUILL, [], [])
    q.yes = Book(asks=[Level(0.45, 250), Level(0.47, 250)], bids=[Level(0.43, 250)])
    q.no = Book(asks=[Level(0.50, 250), Level(0.52, 250)], bids=[Level(0.48, 250)])
    out.append(_tag(q, "Polymarket", poly_other, slug="quill-starfall"))
    return out


class DemoVenue:
    """Stands in for one real venue adapter. Same interface as Polymarket/Kalshi/Manifold."""

    def __init__(self, name: str, markets: list[Market]):
        self.name = name
        self._all = [m for m in markets if m.venue == name]
        self.tradeable = name != "Manifold"

    def parse_url(self, text: str) -> dict | None:
        text = text.strip()
        prefix = f"https://{self.name.lower()}.example/demo/"
        return {"slug": text[len(prefix):].split("?")[0]} if text.startswith(prefix) else None

    def lookup(self, ref: dict) -> list[Market]:
        return [m for m in self._all if m.meta.get("slug") == ref["slug"]]

    def search(self, text: str, limit: int = 5) -> list[Market]:
        from .relations import similarity
        ranked = sorted(self._all, key=lambda m: -similarity(text, m.question))
        return ranked[:limit]

    def siblings(self, market: Market) -> list[Market]:
        if not market.group:
            return []
        return [m for m in self._all if m.group == market.group and m.id != market.id]

    def hydrate(self, market: Market) -> Market:
        return market


def build_demo_registry():
    from .venues import Registry
    markets = demo_markets()
    return Registry([DemoVenue(n, markets) for n in ("Polymarket", "Kalshi", "Manifold")])
