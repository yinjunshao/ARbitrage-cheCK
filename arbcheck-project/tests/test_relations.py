import json
import unittest

from arbcheck.demo_data import DEMO_LABELS, demo_pairs
from arbcheck.relations import (HeuristicExtractor, LLMExtractor, Relation, build_user_prompt,
                                evaluate_extractor, verify)


def good_json(a, b, severity="high"):
    clause_a = a.rules.split(". ")[0]
    clause_b = b.rules.split(". ")[0]
    return json.dumps({"kind": "a_implies_b", "confidence": 0.9, "rationale": "same event",
                       "evidence_a": clause_a, "evidence_b": clause_b,
                       "flags": [{"kind": "source", "severity": severity, "clause_a": clause_a,
                                  "clause_b": clause_b, "note": "x"}]})


class Verification(unittest.TestCase):
    def setUp(self):
        self.a, self.b = demo_pairs()[0]

    def test_verbatim_quote_passes(self):
        q = "Resolution source: Orion Tech investor relations page."
        rel = Relation("a_implies_b", 0.9, [], "", "t", evidence_a=q, evidence_b=q)
        self.assertTrue(verify(rel, self.a, self.b).verified)

    def test_hallucinated_quote_fails(self):
        rel = Relation("a_implies_b", 0.9, [], "", "t",
                       evidence_a="Resolves Yes if Orion Tech merges with Helix.",
                       evidence_b="Resolution source: Orion Tech investor relations page.")
        out = verify(rel, self.a, self.b)
        self.assertFalse(out.verified)
        self.assertIn("market A", out.problems[0])

    def test_quote_matching_ignores_whitespace_and_case(self):
        q = "resolution   SOURCE: orion tech investor relations page."
        rel = Relation("a_implies_b", 0.9, [], "", "t", evidence_a=q, evidence_b=q)
        self.assertTrue(verify(rel, self.a, self.b).verified)


class Heuristic(unittest.TestCase):
    def setUp(self):
        self.pairs = demo_pairs()
        self.ex = HeuristicExtractor()

    def test_matching_rules_have_no_flags(self):
        rel = self.ex.extract(*self.pairs[0])
        self.assertEqual((rel.kind, rel.flags, rel.verified), ("a_implies_b", [], True))

    def test_different_fine_print_is_flagged_high(self):
        rel = self.ex.extract(*self.pairs[1])
        self.assertTrue(rel.verified)
        kinds = {f.kind: f.severity for f in rel.flags}
        self.assertEqual(kinds["source"], "high")
        self.assertEqual(kinds["definition"], "high")

    def test_time_zone_only_is_low(self):
        rel = self.ex.extract(*self.pairs[4])
        self.assertEqual([(f.kind, f.severity) for f in rel.flags], [("timezone", "low")])

    def test_unrelated_questions_give_none(self):
        rel = self.ex.extract(self.pairs[0][0], self.pairs[1][0])
        self.assertEqual(rel.kind, "none")

    def test_scores_perfectly_on_demo_labels(self):
        labelled = [(a, b, lab) for (a, b), lab in zip(self.pairs, DEMO_LABELS)]
        m = evaluate_extractor(self.ex, labelled)
        self.assertEqual((m["kind_accuracy"], m["high_flag_precision"], m["high_flag_recall"]),
                         (1.0, 1.0, 1.0))


class LLM(unittest.TestCase):
    def setUp(self):
        self.a, self.b = demo_pairs()[0]

    def test_good_output_is_verified(self):
        ex = LLMExtractor(lambda s, u: "Here you go:\n" + good_json(self.a, self.b))
        rel = ex.extract(self.a, self.b)
        self.assertTrue(rel.verified)
        self.assertEqual(rel.kind, "a_implies_b")

    def test_fabricated_clause_is_unverified(self):
        bad = json.loads(good_json(self.a, self.b))
        bad["flags"][0]["clause_a"] = "The market resolves No if it rains."
        rel = LLMExtractor(lambda s, u: json.dumps(bad)).extract(self.a, self.b)
        self.assertFalse(rel.verified)

    def test_invalid_json_fails_safe(self):
        rel = LLMExtractor(lambda s, u: "I think they are related!").extract(self.a, self.b)
        self.assertEqual((rel.kind, rel.verified, rel.confidence), ("none", False, 0.0))

    def test_api_error_fails_safe(self):
        def boom(s, u):
            raise RuntimeError("quota")
        rel = LLMExtractor(boom).extract(self.a, self.b)
        self.assertFalse(rel.verified)

    def test_unknown_severity_is_treated_as_high(self):
        data = json.loads(good_json(self.a, self.b))
        data["flags"][0]["severity"] = "negligible"
        rel = LLMExtractor(lambda s, u: json.dumps(data)).extract(self.a, self.b)
        self.assertEqual(rel.flags[0].severity, "high")

    def test_rules_are_fenced_as_data_in_the_prompt(self):
        prompt = build_user_prompt(self.a, self.b)
        self.assertIn("<rules_a>", prompt)
        self.assertIn("</rules_b>", prompt)


if __name__ == "__main__":
    unittest.main()
