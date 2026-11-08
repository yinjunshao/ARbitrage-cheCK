import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from arbcheck import polymarket
from arbcheck.demo_data import DEMO_NOW, demo_pairs, demo_snapshots
from arbcheck.guard import DecisionLog, Guard, Limits
from arbcheck.persistence import SnapshotStore, episodes, kaplan_meier, median_survival
from arbcheck.pipeline import candidate_pairs, scan, sort_reports
from arbcheck.relations import HeuristicExtractor, LLMExtractor


def run(limits=None, extractor=None, log=None):
    guard = Guard(limits or Limits())
    return scan(demo_pairs(), extractor or HeuristicExtractor(), guard, DEMO_NOW, log=log)


class GuardDecisions(unittest.TestCase):
    def test_each_demo_pair_gets_the_right_verdict(self):
        got = [r.decision.status for r in run()]
        self.assertEqual(got, ["OK", "BLOCKED", "NO_ARB", "NO_ARB", "WARN"])

    def test_false_arbitrage_headline_names_the_naive_profit(self):
        helix = run()[1]
        self.assertIn("9.0¢", helix.decision.headline)
        self.assertIn("not locked in", helix.decision.headline)

    def test_exposure_cap_clips_size(self):
        d = run()[0].decision
        self.assertTrue(d.clipped)
        self.assertAlmostEqual(d.approved_cost, 300.0, places=6)

    def test_kill_switch_blocks_everything(self):
        self.assertTrue(all(r.decision.status == "BLOCKED" for r in run(Limits(kill_switch=True))))

    def test_total_budget_is_shared_across_trades(self):
        out = run(Limits(max_notional=300, max_total_notional=300))
        self.assertEqual(out[0].decision.status, "OK")
        self.assertEqual(out[4].decision.status, "NO_ARB")   # budget already spent on Orion
        self.assertIn("Exposure", out[4].decision.headline)

    def test_unverified_llm_relation_blocks_a_real_gap(self):
        def liar(system, user):
            return json.dumps({"kind": "a_implies_b", "confidence": 0.95, "rationale": "x",
                               "evidence_a": "invented clause", "evidence_b": "invented clause",
                               "flags": []})
        out = run(extractor=LLMExtractor(liar))
        self.assertEqual(out[0].decision.status, "BLOCKED")   # Orion would otherwise be OK
        self.assertIn("trace", out[0].decision.headline)

    def test_low_confidence_blocks(self):
        low = Limits(min_confidence=0.95)                      # heuristic confidence is 0.9
        self.assertEqual(run(low)[0].decision.status, "BLOCKED")

    def test_haircut_reduces_profit_for_soft_flags(self):
        polar = run()[4].decision
        self.assertAlmostEqual(polar.haircut_per_share, 0.005)
        self.assertAlmostEqual(polar.approved_profit, 9.0 - 0.005 * 200)

    def test_llm_cannot_increase_size_only_reduce(self):
        """Same market data: a more cautious relation never yields a bigger approved trade."""
        base = run()[4].decision.approved_profit
        cautious = run(Limits(haircuts={"low": 0.02, "medium": 0.03}))[4].decision.approved_profit
        self.assertLessEqual(cautious, base)


class LogAndSort(unittest.TestCase):
    def test_log_has_one_line_per_pair_with_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.jsonl")
            run(log=DecisionLog(path))
            rows = [json.loads(line) for line in open(path)]
        self.assertEqual(len(rows), 5)
        self.assertEqual(len(rows[0]["rules_sha256"]["a"]), 64)
        self.assertIn("decision", rows[0])

    def test_sort_puts_real_opportunities_first(self):
        order = [r.decision.status for r in sort_reports(run())]
        self.assertEqual(order, ["OK", "WARN", "BLOCKED", "NO_ARB", "NO_ARB"])

    def test_candidate_pairs_finds_nested_deadlines(self):
        flat = [m for pair in demo_pairs() for m in pair]
        found = candidate_pairs(flat)
        self.assertEqual(len(found), 5)
        self.assertTrue(all(a.end < b.end for a, b in found))


class PolymarketParsing(unittest.TestCase):
    RAW = {"id": "123", "slug": "orion-split", "question": "Will X happen by June 30, 2027?",
           "description": "Resolves Yes if X.", "resolutionSource": "Example.com",
           "endDate": "2027-06-30T23:59:00Z",
           "outcomes": '["Yes", "No"]', "clobTokenIds": '["tok_yes", "tok_no"]'}

    def test_stub_parses_double_encoded_fields(self):
        m = polymarket.market_stub(self.RAW)
        self.assertEqual((m.meta["yes_token"], m.meta["no_token"]), ("tok_yes", "tok_no"))
        self.assertTrue(m.rules.endswith("Resolution source: Example.com"))
        self.assertEqual(m.end, datetime(2027, 6, 30, 23, 59, tzinfo=timezone.utc))

    def test_non_binary_or_undated_markets_are_skipped(self):
        self.assertIsNone(polymarket.market_stub({**self.RAW, "outcomes": '["A", "B", "C"]'}))
        raw = dict(self.RAW)
        del raw["endDate"]
        self.assertIsNone(polymarket.market_stub(raw))

    def test_book_is_sorted_and_numeric(self):
        book = polymarket.parse_book({"bids": [{"price": "0.40", "size": "10"}, {"price": "0.42", "size": "5"}],
                                      "asks": [{"price": "0.47", "size": "7"}, {"price": "0.44", "size": "3"}]})
        self.assertEqual((book.best_bid, book.best_ask), (0.42, 0.44))

    def test_hydrate_fetches_both_books(self):
        m = polymarket.market_stub(self.RAW)
        fake = lambda url, params=None, timeout=15: {"bids": [{"price": "0.4", "size": "1"}],
                                                      "asks": [{"price": "0.5", "size": "2"}]}
        with mock.patch.object(polymarket, "_get", side_effect=fake) as g:
            polymarket.hydrate(m)
        self.assertEqual(g.call_count, 2)
        self.assertEqual(m.yes.best_ask, 0.5)


class Persistence(unittest.TestCase):
    def test_episodes_and_censoring(self):
        t0 = DEMO_NOW
        pattern = [1, 1, 0, 1, 0, 0, 1, 1]          # last episode still open at the end
        series = [(t0 + timedelta(minutes=i), 0.05 if x else -0.01, 100 if x else 0)
                  for i, x in enumerate(pattern)]
        eps = episodes(series, poll_s=60)
        self.assertEqual([(e.duration_s, e.ongoing) for e in eps], [(120, False), (60, False), (120, True)])

    def test_kaplan_meier_handles_censoring(self):
        curve = kaplan_meier([1, 2, 3], [True, False, True])
        self.assertAlmostEqual(curve[1][1], 2 / 3)    # one of three died at t=1
        self.assertAlmostEqual(curve[2][1], 0.0)      # the last at-risk episode died at t=3
        self.assertEqual(median_survival(curve), 3)

    def test_demo_snapshots_produce_episodes(self):
        store = SnapshotStore()
        demo_snapshots(store, "p")
        eps = episodes(store.series("p"), poll_s=60)
        self.assertGreater(len(eps), 10)


if __name__ == "__main__":
    unittest.main()
