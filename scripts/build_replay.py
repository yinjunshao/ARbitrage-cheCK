"""Write tests/frame/fixtures/llm_replay_sample.json: hand-authored LLM outputs for the offline demo.

These are NOT model outputs; they stand in for Gemini so `--replay` runs with no key or network.
Keys are "<call>:<pair_id>", so they follow the fixture texts. Re-run after editing markets.json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from frame.discover import load_fixture_markets  # noqa: E402
from frame.judge import pair_id  # noqa: E402

FX = Path(__file__).resolve().parent.parent / "tests" / "frame" / "fixtures"
m = {x.id: x for x in load_fixture_markets(FX / "markets.json")}
fed_pm, fed_k = m["SAMPLE-pm-fed-dec26"], m["SAMPLE-KXFED-26DEC"]
uk_mar, uk_jun = m["SAMPLE-pm-ukge-mar27"], m["SAMPLE-pm-ukge-jun27"]
fed, uk = pair_id(fed_pm, fed_k), pair_id(uk_mar, uk_jun)

replay = {
    f"judge_pair:{fed}": {
        "relation": "same_event", "confidence": "medium",
        "reasoning": "Both resolve on whether the Fed lowers its target at the December 2026 meeting. "
                     "A defines Yes as an announced cut; B as a lower upper bound. Close times differ slightly.",
        "evidence_a": ["announces a cut to the target federal funds rate at its December 2026 FOMC meeting"],
        "evidence_b": ["the upper bound of the federal funds target range announced after the Federal Reserve's December 2026 meeting is lower"],
    },
    f"compare_rules:{fed}": {
        "overall": "material_differences",
        "differences": [
            {"topic": "yes_definition", "severity": "material",
             "quote_a": "announces a cut to the target federal funds rate",
             "quote_b": "the upper bound of the federal funds target range announced after the Federal Reserve's December 2026 meeting is lower than the upper bound in effect immediately before that meeting",
             "explanation": "A counts any announced cut; B needs the upper bound to fall. A cut to only the lower bound would split them."},
            {"topic": "data_source", "severity": "cosmetic",
             "quote_a": "the official FOMC statement published on federalreserve.gov",
             "quote_b": "Source: Federal Reserve Board press release.",
             "explanation": "Both are Federal Reserve publications."},
            {"topic": "deadline", "severity": "material",
             "quote_a": "If no FOMC meeting is held by December 31, 2026, 11:59 PM ET, this market will resolve to \"No\".",
             "quote_b": "",
             "explanation": "A has a no-meeting fallback; B is silent if the meeting is moved."},
        ],
    },
    f"judge_pair:{uk}": {
        "relation": "a_implies_b", "confidence": "high",
        "reasoning": "Same event group with nested deadlines: an election called by the earlier date is also called by the later date.",
        "evidence_a": ["between market creation and March 31, 2027, 11:59 PM GMT"],
        "evidence_b": ["between market creation and June 30, 2027, 11:59 PM BST"],
    },
    f"compare_rules:{uk}": {
        "overall": "compatible",
        "differences": [
            {"topic": "time_zone", "severity": "cosmetic",
             "quote_a": "11:59 PM GMT", "quote_b": "11:59 PM BST",
             "explanation": "Each deadline is stated in the UK clock in force on that date."},
        ],
    },
    # Narrative for the fake core's placeholder numbers (net 0.021, gross 0.042, 850 max size).
    f"narrate:{fed}": {
        "headline": "Look closely: the core finds a 2.1% net edge (4.2% gross) on the December Fed cut, but rules differ.",
        "why_gap": "Kalshi and Polymarket price the same Fed decision differently; the core prices both legs against their books.",
        "what_could_go_wrong": [
            "Thin depth: the core caps fillable size at 850.",
            "Yes is defined differently: a cut to only the lower bound would split the two markets.",
            "Polymarket has a no-meeting fallback that Kalshi lacks.",
            "These are placeholder numbers from the fake core.",
        ],
        "human_checks": ["Read both definitions of Yes side by side.", "Confirm the meeting date has not moved."],
        "recommendation": "review",
    },
    f"narrate:{uk}": {
        "headline": "Look closely: nested-deadline pair, core net edge 2.1%.",
        "why_gap": "The earlier deadline implies the later one, so the earlier market should not trade above it.",
        "what_could_go_wrong": ["Thin depth flagged by the core.", "These are placeholder numbers from the fake core."],
        "human_checks": ["Confirm both markets use the same definition of 'called'."],
        "recommendation": "review",
    },
}
(FX / "llm_replay_sample.json").write_text(json.dumps(replay, indent=2), encoding="utf-8")
print(f"wrote {len(replay)} replay entries")
