import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from frame.config import Config  # noqa: E402
from frame.discover import load_fixture_markets  # noqa: E402

FIXTURES = ROOT / "data" / "frame" / "fixtures"


@pytest.fixture
def markets():
    return {m.id: m for m in load_fixture_markets(FIXTURES / "markets.json")}


@pytest.fixture
def cfg(tmp_path):
    return replace(Config(), gemini_api_key=None, audit_path=tmp_path / "audit.jsonl",
                   labels_path=tmp_path / "labels.jsonl", cache_dir=tmp_path / "cache",
                   fixtures_dir=FIXTURES, replay_file=FIXTURES / "llm_replay_sample.json",
                   core="fake", kalshi_member=True)
