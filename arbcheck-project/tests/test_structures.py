"""Engine tests for the arbitrage structures beyond a single nested-deadline pair, and for fees."""
import unittest
from datetime import datetime, timezone

from arbcheck.demo_data import binary
from arbcheck.engine import (basket_floor, candidate_baskets, evaluate_structure, leg_payout,
                             worlds_for)
from arbcheck.fees import KALSHI_FEE, NO_FEE, flat_fee, polymarket_fee
from arbcheck.models import Market

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)
END = datetime(2027, 1, 1, tzinfo=timezone.utc)


def mk(mid, bids, asks, venue="Polymarket", fee=NO_FEE, size=1000):
    yes, no = binary([(p, size) for p in bids], [(p, size) for p in asks])
    return Market(mid, f"Question {mid}?", "rules", END, yes, no, venue=venue, fee=fee)


class Fees(unittest.TestCase):
    def test_kalshi_matches_its_published_example(self):
        # Kalshi's fee schedule: 100 contracts at $0.55 -> $1.74 (0.07 * 100 * .55 * .45 = 1.7325, rounded up)
        self.assertAlmostEqual(KALSHI_FEE.order_fee([(0.55, 100)]), 1.74)

    def test_fee_peaks_at_fifty_cents_and_vanishes_at_the_ends(self):
        f = KALSHI_FEE
        self.assertAlmostEqual(f.per_share(0.5), 0.0175)
        self.assertGreater(f.per_share(0.5), f.per_share(0.9))
        self.assertAlmostEqual(f.per_share(0.5), f.per_share(0.5))
        self.assertAlmostEqual(f.per_share(0.3), f.per_share(0.7))

    def test_polymarket_category_and_cautious_default(self):
        self.assertAlmostEqual(polymarket_fee("politics").k, 0.04)
        self.assertEqual(polymarket_fee("geopolitics").k, 0.0)
        unknown = polymarket_fee("something new")
        self.assertAlmostEqual(unknown.k, 0.05)            # unknown -> the higher fee
        self.assertIn("assumed", unknown.name)

    def test_flat_fee_override_replaces_venue_fees(self):
        a = mk("a", [0.40], [0.42], fee=KALSHI_FEE)
        r_own = evaluate_structure([a], "complement", NOW)
        r_flat = evaluate_structure([a], "complement", NOW, fee_rate=0.5)
        self.assertGreaterEqual(r_own.top_edge, r_flat.top_edge - 1e-9)
        self.assertEqual(r_flat.fees[0], flat_fee(0.5))


class WorldsAndBaskets(unittest.TestCase):
    def test_world_counts(self):
        self.assertEqual(len(worlds_for("complement", 1)), 2)
        self.assertEqual(len(worlds_for("partition", 4)), 4)
        self.assertEqual(len(worlds_for("at_most_one", 4)), 5)
        self.assertIsNone(worlds_for("partition", 1))
        self.assertIsNone(worlds_for("none", 2))

    def test_floor_is_a_true_lower_bound_for_every_basket_and_structure(self):
        for kind, n in (("complement", 1), ("partition", 4), ("at_most_one", 4), ("equivalent", 2)):
            worlds = worlds_for(kind, n)
            for legs in candidate_baskets(n):
                floor = basket_floor(legs, worlds)
                for w in worlds:
                    self.assertGreaterEqual(sum(leg_payout(l, w) for l in legs), floor)

    def test_known_floors(self):
        n = 4
        yes_all = tuple((c, "YES") for c in "ABCD")
        no_all = tuple((c, "NO") for c in "ABCD")
        self.assertEqual(basket_floor(yes_all, worlds_for("partition", n)), 1)
        self.assertEqual(basket_floor(no_all, worlds_for("partition", n)), n - 1)
        self.assertEqual(basket_floor(yes_all, worlds_for("at_most_one", n)), 0)   # nobody may win
        self.assertEqual(basket_floor(no_all, worlds_for("at_most_one", n)), n - 1)

    def test_large_events_use_the_cheap_basket_set(self):
        self.assertLess(len(candidate_baskets(12)), 400)


class Complement(unittest.TestCase):
    def test_yes_plus_no_below_a_dollar_is_an_arbitrage(self):
        # YES ask 0.47, NO ask 1-0.51 = 0.49  ->  cost 0.96, pays 1.00
        m = mk("a", [0.51], [0.47])
        r = evaluate_structure([m], "complement", NOW)
        self.assertTrue(r.executable)
        self.assertEqual(len(r.legs), 2)
        self.assertAlmostEqual(r.top_edge, 0.04, places=6)

    def test_normal_spread_is_not_an_arbitrage(self):
        r = evaluate_structure([mk("a", [0.44], [0.46])], "complement", NOW)
        self.assertFalse(r.executable)

    def test_fees_can_erase_it(self):
        m = mk("a", [0.51], [0.47], venue="Kalshi", fee=KALSHI_FEE)
        gross = evaluate_structure([mk("a", [0.51], [0.47])], "complement", NOW)
        net = evaluate_structure([m], "complement", NOW)
        self.assertLess(net.avg_edge, gross.avg_edge)


class MultiOutcome(unittest.TestCase):
    def test_buying_every_no_in_an_exclusive_set(self):
        # Four outcomes whose Yes prices sum to 1.12: sell-side overpricing -> buy every No.
        ms = [mk(c, [yb], [yb + 0.02]) for c, yb in zip("abcd", (0.40, 0.30, 0.25, 0.17))]
        r = evaluate_structure(ms, "at_most_one", NOW)
        self.assertTrue(r.executable)
        self.assertEqual(sorted(l[1] for l in r.legs), ["NO"] * 4)
        self.assertEqual(r.floor, 3)

    def test_the_long_side_needs_a_true_partition(self):
        # Yes asks sum to 0.9: only an arbitrage if exactly one outcome must win.
        ms = [mk(c, [yb - 0.02], [yb]) for c, yb in zip("abcd", (0.30, 0.25, 0.20, 0.15))]
        self.assertFalse(evaluate_structure(ms, "at_most_one", NOW).executable)
        r = evaluate_structure(ms, "partition", NOW)
        self.assertTrue(r.executable)
        self.assertEqual(sorted(l[1] for l in r.legs), ["YES"] * 4)
        self.assertAlmostEqual(r.top_edge, 0.10, places=6)

    def test_thin_leg_limits_the_basket(self):
        ms = [mk("a", [0.40], [0.42], size=500), mk("b", [0.40], [0.42], size=30),
              mk("c", [0.40], [0.42], size=500)]
        r = evaluate_structure(ms, "at_most_one", NOW)   # No asks are ~0.60: 1.80 < 2, gap 0.2
        self.assertTrue(r.executable)
        self.assertLessEqual(r.shares, 30 + 1e-9)


class CrossPlatform(unittest.TestCase):
    def test_equivalent_markets_on_two_venues(self):
        poly = mk("p", [0.40], [0.44], venue="Polymarket")
        kal = mk("k", [0.52], [0.54], venue="Kalshi")
        # buy YES on Polymarket (0.44) and NO on Kalshi (1 - 0.52 = 0.48): cost 0.92
        r = evaluate_structure([poly, kal], "equivalent", NOW)
        self.assertTrue(r.executable)
        self.assertEqual(r.legs, (("A", "YES"), ("B", "NO")))
        self.assertAlmostEqual(r.top_edge, 0.08, places=6)

    def test_each_leg_pays_its_own_venues_fee(self):
        poly = mk("p", [0.40], [0.44], venue="Polymarket", fee=polymarket_fee("politics"))
        kal = mk("k", [0.52], [0.54], venue="Kalshi", fee=KALSHI_FEE)
        r = evaluate_structure([poly, kal], "equivalent", NOW)
        expect = 0.04 * 0.44 * 0.56 + 0.07 * 0.48 * 0.52
        per_share = r.fee_cost / r.shares
        self.assertAlmostEqual(per_share, expect, delta=0.0002)   # Kalshi rounds the order up to a cent

    def test_play_money_market_is_never_a_leg(self):
        poly = mk("p", [0.40], [0.44])
        mani = mk("m", [0.60], [0.62], venue="Manifold")
        mani.tradeable = False
        r = evaluate_structure([poly, mani], "equivalent", NOW)
        self.assertFalse(r.executable)
        self.assertIsNone(r.legs)


if __name__ == "__main__":
    unittest.main()
