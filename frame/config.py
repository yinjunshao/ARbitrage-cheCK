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

    # "fake" = fixed placeholder results (default until the core lands); "real" = import the core.
    core: str = os.getenv("FRAME_CORE", "fake")
    kalshi_member: bool = os.getenv("FRAME_KALSHI_MEMBER", "0") == "1"

    top_k: int = int(os.getenv("FRAME_TOP_K", "8"))
    date_window_days: int = int(os.getenv("FRAME_DATE_WINDOW_DAYS", "7"))
    judge_concurrency: int = int(os.getenv("FRAME_JUDGE_CONCURRENCY", "4"))
    fewshot_k: int = int(os.getenv("FRAME_FEWSHOT_K", "5"))
    min_surface_confidence: str = "medium"  # surface only medium/high to review
    excluded_categories: tuple[str, ...] = ("crypto",)

    audit_path: Path = DATA / "audit.jsonl"
    labels_path: Path = DATA / "labels.jsonl"
    cache_dir: Path = DATA / "cache"
    fixtures_dir: Path = DATA / "fixtures"


def get_config() -> Config:
    return Config()
