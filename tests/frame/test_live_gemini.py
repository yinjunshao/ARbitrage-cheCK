"""Live smoke test against Gemini. Skipped unless GEMINI_API_KEY is set. Costs a few calls."""
import os

import pytest

from frame.config import Config
from frame.judge import Judge
from frame.llm import GeminiClient
from frame.rulediff import compare_rules

pytestmark = pytest.mark.skipif(not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
                                reason="no GEMINI_API_KEY")


def test_live_judge_and_rules(markets):
    cfg = Config()
    llm = GeminiClient(cfg)
    a, b = markets["SAMPLE-pm-fed-dec26"], markets["SAMPLE-KXFED-26DEC"]
    jp = Judge(llm, cfg.judge_model).judge_pair(a, b)
    assert jp.source == "llm"
    assert jp.verdict.relation in ("same_event", "unclear")  # unclear allowed if a span failed verification
    r = compare_rules(llm, cfg.rules_model, a, b)
    assert r.diff.differences
