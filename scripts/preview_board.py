"""Offline preview of the arbitrage board for design work: the core's own invented demo markets (one is a real
arbitrage), a fake Gemini, no network. Not for judging: every market here is invented.

    python scripts/preview_board.py            # http://127.0.0.1:8090/board
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FRAME_EMBEDDER"] = "hashing"

from nicegui import ui  # noqa: E402

import app_frame.main as m  # noqa: E402
from app_frame import board_page, style  # noqa: E402
from frame.config import Config  # noqa: E402
from frame.llm import FakeLLM  # noqa: E402
from frame.pipeline import build  # noqa: E402

t = Path(tempfile.mkdtemp())
cfg = replace(Config(), gemini_api_key=None, audit_path=t / "a.jsonl", labels_path=t / "l.jsonl", cache_dir=t / "c",
              suggestions_path=t / "s.jsonl", confirmed_path=t / "cf.jsonl", watch_path=t / "w.json", core="arbcheck",
              min_similarity=0.0, max_days_to_close=3650, max_close_gap_days=3650, max_singles_per_scan=12)


def responder(call, user, schema, key):
    if call == "judge_pair":
        if ("Orion Tech" in user and "December 31, 2026" in user and "March 31, 2027" in user) or \
                ("Vega" in user and "5,500" in user and "5,000" in user):
            return {"relation": "a_implies_b", "confidence": "high", "reasoning": "nested deadlines / thresholds",
                    "evidence_a": [], "evidence_b": []}
        return {"relation": "unrelated", "confidence": "high", "reasoning": "no", "evidence_a": [], "evidence_b": []}
    if call == "compare_rules":
        return {"overall": "compatible", "differences": []}
    return None


p = build(demo=True, cfg=cfg, llm=FakeLLM(responder))
p.scan()
for c in list(p.review_queue()):                      # confirm and price what the (fake) judge surfaced
    p.confirm(c.id, "preview")
    p.price(c.id, 300)
if p.cases:                                           # show how another scanner's claim sits beside ours
    next(iter(p.cases.values())).external = {"source": "another scanner", "roi_pct": 9.7, "max_wager_usd": 479,
                                             "detected_at": "just now"}
m.P, m.MODE = p, "testdata"
m.PREFS.update(venues={"polymarket"}, categories={"other"}, leagues=set())


async def scan():
    return p.last_scan


async def send(row):
    ui.notify("Would send to the checker (Gemini + you).")

style.install()
board_page.register(lambda: p, scan, m.busy, send)
print(f"preview: {len(p.cases)} verified, {len(p.screens)} screens, {len(p.findings)} structures", flush=True)
ui.run(title="Board preview", port=8090, host="127.0.0.1", reload=False, show=False)
