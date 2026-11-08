import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from frame.config import Config  # noqa: E402
from frame.discover import load_fixture_markets  # noqa: E402

FIXTURES = ROOT / "tests" / "frame" / "fixtures"


@pytest.fixture
def markets():
    return {m.id: m for m in load_fixture_markets(FIXTURES / "markets.json")}


@pytest.fixture
def cfg(tmp_path):
    return replace(Config(), gemini_api_key=None, audit_path=tmp_path / "audit.jsonl",
                   labels_path=tmp_path / "labels.jsonl", cache_dir=tmp_path / "cache",
                   suggestions_path=tmp_path / "suggestions.jsonl", confirmed_path=tmp_path / "confirmed.jsonl",
                   watch_path=tmp_path / "watchlist.json",
                   min_similarity=0.0, top_k=8,
                   fixtures_dir=FIXTURES, replay_file=FIXTURES / "llm_replay_sample.json",
                   core="fake", kalshi_member=True,
                   max_days_to_close=36500, max_close_gap_days=36500)   # fixtures have fixed dates; R5/R6 tested on their own
