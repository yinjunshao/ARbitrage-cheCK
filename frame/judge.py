"""LLM call 1: are these two markets logically related, and how?"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .ground import verify_spans
from .llm import LLMClient, LLMError
from .schemas import JudgedPair, MatchVerdict, Market

SYSTEM = """You judge whether two prediction-market contracts are logically related.
Relations (A and B refer to the two markets as given):
- same_event: both resolve Yes in exactly the same states of the world.
- a_implies_b: whenever A resolves Yes, B must also resolve Yes (e.g. nested deadlines or thresholds).
- b_implies_a: the reverse.
- mutually_exclusive: both cannot resolve Yes.
- exhaustive_set: together with each other they cover every outcome (exactly one must be Yes).
- unrelated: no logical link.
- unclear: you cannot tell from the text.
Rules:
1. Say "unclear" rather than guess. Prefer "unrelated" when the link is only topical.
2. Read deadlines, thresholds, data sources and time zones from the text. Never assume them.
3. evidence_a / evidence_b must be copied character for character from the text of A / B. No paraphrase.
   Any span that is not an exact substring makes your verdict void.
4. Do not compute prices, probabilities or profits. You output a relation only.
5. Keep reasoning to two or three sentences."""


def render_market(tag: str, m: Market) -> str:
    return (f"MARKET {tag}\nvenue: {m.venue}\ntitle: {m.title}\noutcome: {m.outcome_label}\n"
            f"closes (UTC): {m.close_time_utc or 'not stated'}\nrules:\n{m.rules_text or '(no rules text)'}\n")


def source_text(m: Market) -> str:
    return "\n".join([m.title, m.outcome_label, m.rules_text, m.close_time_utc or ""])


def pair_id(a: Market, b: Market) -> str:
    h = hashlib.sha256((source_text(a) + "\x00" + source_text(b)).encode()).hexdigest()
    return h[:16]


def render_examples(examples: list[dict]) -> str:
    if not examples:
        return ""
    lines = ["Human-confirmed examples (for calibration; do not copy their evidence):"]
    for ex in examples:
        lines.append(f"- A: {ex['title_a']!r} | B: {ex['title_b']!r} -> {ex['relation']}"
                     f"{' (rejected by human)' if not ex.get('confirmed', True) else ''}")
    return "\n".join(lines) + "\n\n"


class Judge:
    def __init__(self, llm: LLMClient, model: str, cache_dir: Path | None = None, fewshot=None, concurrency: int = 4):
        self.llm, self.model, self.fewshot, self.concurrency = llm, model, fewshot, concurrency
        self.cache_dir = cache_dir
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

    def _cache_get(self, pid: str) -> JudgedPair | None:
        if not self.cache_dir:
            return None
        p = Path(self.cache_dir) / f"judge_{pid}.json"
        return JudgedPair.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None

    def _cache_put(self, jp: JudgedPair) -> None:
        if self.cache_dir and jp.source == "llm":
            (Path(self.cache_dir) / f"judge_{jp.pair_id}.json").write_text(jp.model_dump_json(), encoding="utf-8")

    def judge_pair(self, a: Market, b: Market) -> JudgedPair:
        pid = pair_id(a, b)
        if (hit := self._cache_get(pid)) is not None:
            return hit
        examples = self.fewshot.retrieve(a, b) if self.fewshot else []
        user = render_examples(examples) + render_market("A", a) + "\n" + render_market("B", b)
        try:
            v = self.llm.generate("judge_pair", SYSTEM, user, MatchVerdict, self.model, key=pid)
            source = getattr(self.llm, "name", "llm")
            source = source if source in ("llm", "replay") else "llm"
        except LLMError as e:
            v = MatchVerdict(relation="unclear", confidence="low", reasoning=f"LLM unavailable: {e}",
                             evidence_a=[], evidence_b=[])
            source = "fallback"
        jp = verify_verdict(pid, a, b, v, source)
        self._cache_put(jp)
        return jp

    def judge_many(self, pairs: list[tuple[Market, Market]]) -> list[JudgedPair]:
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            return list(ex.map(lambda p: self.judge_pair(*p), pairs))


def verify_verdict(pid: str, a: Market, b: Market, v: MatchVerdict, source: str = "llm") -> JudgedPair:
    failed = verify_spans(v.evidence_a, source_text(a)) + verify_spans(v.evidence_b, source_text(b))
    related = v.relation not in ("unrelated", "unclear")
    no_evidence = related and (not v.evidence_a or not v.evidence_b)
    if failed or no_evidence:
        why = f"unverified spans: {json.dumps(failed)}" if failed else "no evidence quoted"
        v = v.model_copy(update={"relation": "unclear", "confidence": "low",
                                 "reasoning": f"[downgraded: {why}] {v.reasoning}"})
    return JudgedPair(pair_id=pid, a=a, b=b, verdict=v, verified=not (failed or no_evidence),
                      failed_spans=failed, source=source)
