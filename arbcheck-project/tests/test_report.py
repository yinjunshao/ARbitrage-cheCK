import unittest
from dataclasses import replace

from arbcheck.demo_data import DEMO_NOW, demo_pairs, demo_snapshots
from arbcheck.guard import Guard, Limits
from arbcheck.persistence import SnapshotStore
from arbcheck.pipeline import scan
from arbcheck.relations import HeuristicExtractor
from arbcheck.report import render_report
from arbcheck.__main__ import survival_summary


def build(limits=None, pairs=None, survival=True):
    guard = Guard(limits or Limits())
    reports = scan(pairs or demo_pairs(), HeuristicExtractor(), guard, DEMO_NOW)
    sv = None
    if survival:
        store = SnapshotStore()
        demo_snapshots(store, "d")
        sv = survival_summary(store, "d", 60, True)
    return render_report(reports, guard.limits, survival=sv, demo=True, generated=DEMO_NOW)


class Report(unittest.TestCase):
    def test_headline_numbers_and_verdicts(self):
        page = build()
        self.assertIn('2<span class="of"> of 4</span>', page)     # 4 look like arbitrage, 2 hold up
        self.assertIn("Not a real arbitrage.", page)
        self.assertIn("Worth a look.", page)
        self.assertIn('<mark class="sev-high">', page)             # differing clause is highlighted

    def test_every_status_has_an_icon_and_a_word(self):
        page = build()
        for label in ("Worth a look", "Look closely", "Not a real arbitrage", "Nothing to do"):
            self.assertIn(f'aria-label="{label}"', page)

    def test_kill_switch_is_visible(self):
        page = build(Limits(kill_switch=True))
        self.assertIn("ON, all trades blocked", page)
        self.assertIn("every trade is blocked", page)

    def test_untrusted_market_text_is_escaped(self):
        pairs = demo_pairs()
        a, b = pairs[0]
        evil = replace(a, question="Will <script>alert(1)</script> happen by December 31, 2026?",
                       rules=a.rules + " <img src=x onerror=alert(2)>")
        page = build(pairs=[(evil, b)], survival=False)
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;", page)

    def test_page_is_self_contained(self):
        page = build()
        for banned in ("http://", "https://", "<link ", "src="):
            self.assertNotIn(banned, page)

    def test_survival_chart_is_labelled_synthetic(self):
        self.assertIn("Synthetic demo data", build())


if __name__ == "__main__":
    unittest.main()
