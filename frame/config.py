"""Environment, model names and thresholds. Everything tunable lives here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "frame"

# Placeholder default: confirm the exact model ID in Google AI Studio on the day
# and set GEMINI_MODEL in .env. Per-call overrides below.
_DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-argon")


@dataclass(frozen=True)
class Config:
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    judge_model: str = os.getenv("FRAME_JUDGE_MODEL", _DEFAULT_MODEL)
    rules_model: str = os.getenv("FRAME_RULES_MODEL", _DEFAULT_MODEL)
    narrate_model: str = os.getenv("FRAME_NARRATE_MODEL", _DEFAULT_MODEL)
    temperature: float = float(os.getenv("FRAME_TEMPERATURE", "0.1"))
    max_output_tokens: int = int(os.getenv("FRAME_MAX_TOKENS", "4096"))
    llm_timeout_s: float = float(os.getenv("FRAME_LLM_TIMEOUT_S", "60"))   # per Gemini request

    # "arbcheck" = the real numeric core in arbcheck-project/ ; "fake" = fixed placeholders (unit tests).
    core: str = os.getenv("FRAME_CORE", "arbcheck")
    kalshi_member: bool = os.getenv("FRAME_KALSHI_MEMBER", "0") == "1"

    top_k: int = int(os.getenv("FRAME_TOP_K", "5"))
    date_window_days: int = int(os.getenv("FRAME_DATE_WINDOW_DAYS", "7"))
    min_similarity: float = float(os.getenv("FRAME_MIN_SIMILARITY", "0.45"))
    max_judge_per_scan: int = int(os.getenv("FRAME_MAX_JUDGE_PER_SCAN", "30"))  # LLM budget; rest retried next scan
    max_sets_per_scan: int = int(os.getenv("FRAME_MAX_SETS_PER_SCAN", "8"))     # venue exclusive sets priced (no LLM)
    max_singles_per_scan: int = int(os.getenv("FRAME_MAX_SINGLES_PER_SCAN", "30"))  # "Yes + No under $1" checks (no LLM)
    judge_concurrency: int = int(os.getenv("FRAME_JUDGE_CONCURRENCY", "4"))
    fewshot_k: int = int(os.getenv("FRAME_FEWSHOT_K", "5"))
    min_surface_confidence: str = "medium"  # surface only medium/high to review
    # Crypto is allowed, but only paired with crypto of the same coin (frame/rules.py).
    excluded_categories: tuple[str, ...] = ()
    monitor_every_s: int = int(os.getenv("FRAME_MONITOR_EVERY_S", "300"))
    # Time rules R5/R6: only suggest pairs that resolve soon and at about the same time.
    max_days_to_close: int = int(os.getenv("FRAME_MAX_DAYS_TO_CLOSE", "45"))
    max_close_gap_days: int = int(os.getenv("FRAME_MAX_CLOSE_GAP_DAYS", "14"))

    audit_path: Path = DATA / "audit.jsonl"
    labels_path: Path = DATA / "labels.jsonl"
    suggestions_path: Path = DATA / "suggestions.jsonl"
    confirmed_path: Path = DATA / "confirmed.jsonl"
    cache_dir: Path = DATA / "cache"
    fixtures_dir: Path = ROOT / "tests" / "frame" / "fixtures"      # test-only sample data
    replay_file: Path = ROOT / "tests" / "frame" / "fixtures" / "llm_replay_sample.json"


def get_config() -> Config:
    return Config()
