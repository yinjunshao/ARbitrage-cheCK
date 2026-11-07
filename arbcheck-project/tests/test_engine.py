import unittest

from arbcheck.demo_data import DEMO_NOW, demo_pairs
from arbcheck.engine import (WORLDS, basket_floor, clip_to_budget, evaluate_pair, leg_payout,
                             totals, walk)
from arbcheck.fees import NO_FEE, flat_fee
from arbcheck.models import Level

TWO = [NO_FEE, NO_FEE]


class WorldMaths(unittest.TestCase):
    def test_floor_is_a_true_lower_bound_in_every_world(self):
        """For every relation, the 'guaranteed' payout really is paid in every possible world."""
        for kind, worlds in WORLDS.items():
            for sa in ("YES", "NO"):
                for sb in ("YES", "NO"):
                    legs = (("A", sa), ("B", sb))
                    floor = basket_floor(legs, worlds)
                    for w in worlds:
                        self.assertGreaterEqual(sum(leg_payout(l, w) for l in legs), floor)

    def test_nested_deadline_has_exactly_one_arbitrage_basket(self):
        worlds = WORLDS["a_implies_b"]
        good = [(sa, sb) for sa in ("YES", "NO") for sb in ("YES", "NO")
                if basket_floor((("A", sa), ("B", sb)), worlds) > 0]
        self.assertEqual(good, [("NO", "YES")])   # buy NO on the earlier, YES on the later

    def test_each_relation_pays_at_least_one_dollar(self):
        expected = {"a_implies_b": ("NO", "YES"), "b_implies_a": ("YES", "NO"),
                    "mutually_exclusive": ("NO", "NO"), "exhaustive": ("YES", "YES")}
        for kind, (sa, sb) in expected.items():
            self.assertEqual(basket_floor((("A", sa), ("B", sb)), WORLDS[kind]), 1, kind)
        self.assertEqual(max(basket_floor((("A", sa), ("B", sb)), WORLDS["equivalent"])
                             for sa in ("YES", "NO") for sb in ("YES", "NO")), 1)


class BookWalking(unittest.TestCase):
    def test_walk_stops_when_edge_is_gone(self):
        asks1 = [Level(0.35, 100), Level(0.50, 100)]
        asks2 = [Level(0.58, 500)]
        fills = walk([asks1, asks2], 1.0, TWO)
        self.assertEqual([(f.prices, f.shares) for f in fills], [((0.35, 0.58), 100)])

    def test_fees_remove_marginal_profit(self):
        asks1, asks2 = [Level(0.35, 100)], [Level(0.58, 100)]   # 7 cents gross
        self.assertEqual(len(walk([asks1, asks2], 1.0, TWO)), 1)
        ten = [flat_fee(0.10)] * 2
        self.assertEqual(len(walk([asks1, asks2], 1.0, ten)), 0)  # 10% fee on 0.93 > 7 cents

    def test_clip_respects_budget(self):
        fills = walk([[Level(0.35, 150), Level(0.37, 300)], [Level(0.58, 300), Level(0.60, 500)]], 1.0, TWO)
        clipped = clip_to_budget(fills, 300.0, TWO)
        shares, cost, _ = totals(clipped, 1.0, TWO)
        self.assertAlmostEqual(cost, 300.0, places=6)
        self.assertAlmostEqual(shares, 150 + 150 + 18 / 0.97, places=6)


class DemoNumbers(unittest.TestCase):
    """Hand-checked against the order books in demo_data.py."""

    def setUp(self):
        self.pairs = demo_pairs()

    def test_orion_real_arbitrage(self):
        a, b = self.pairs[0]
        r = evaluate_pair(a, b, "a_implies_b", DEMO_NOW)
        self.assertEqual(r.legs, (("A", "NO"), ("B", "YES")))
        self.assertAlmostEqual(r.naive_edge, 0.09, places=6)    # price-only scanner: 9c
        self.assertAlmostEqual(r.top_edge, 0.07, places=6)      # spread costs 2c
        self.assertAlmostEqual(r.avg_edge, 0.05, places=6)      # depth costs another 2c
        self.assertAlmostEqual(r.shares, 450)
        self.assertAlmostEqual(r.cost, 427.5)
        self.assertAlmostEqual(r.net_profit, 22.5)

    def test_thin_book_has_no_executable_gap(self):
        a, b = self.pairs[3]
        r = evaluate_pair(a, b, "a_implies_b", DEMO_NOW)
        self.assertFalse(r.executable)
        self.assertAlmostEqual(r.naive_edge, 0.03, places=6)    # looks like 3c on mids...
        self.assertLess(r.top_edge, 0)                          # ...but costs 5c to actually do

    def test_relation_none_prices_nothing(self):
        a, b = self.pairs[0]
        r = evaluate_pair(a, b, "none", DEMO_NOW)
        self.assertFalse(r.executable)
        self.assertEqual(r.shares, 0)

    def test_scenarios_never_lose_when_the_relation_holds(self):
        a, b = self.pairs[0]
        r = evaluate_pair(a, b, "a_implies_b", DEMO_NOW)
        self.assertEqual(len(r.scenarios), 3)
        self.assertTrue(all(s["profit"] > 0 for s in r.scenarios))


if __name__ == "__main__":
    unittest.main()
