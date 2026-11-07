"""Verification helpers: verbatim quotes and number grounding.

- verify_spans: every quote must be an exact substring of its source (whitespace-normalised).
- check_numbers: every numeral in LLM prose must trace to the core result or verified source text.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

_WS = re.compile(r"\s+")
# currency, thousands separators, decimals, optional percent / "pp"
_NUM = re.compile(r"(?<![\w.])[-−]?[$£€]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.])[-−]?[$£€]?\d+(?:\.\d+)?%?")


def _norm(s: str) -> str:
    return _WS.sub(" ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip()


def verify_span(span: str, source: str) -> bool:
    span_n = _norm(span)
    return bool(span_n) and _norm(source).find(span_n) != -1


def verify_spans(spans: Iterable[str], source: str) -> list[str]:
    """Return the spans that FAIL verification."""
    return [s for s in spans if not verify_span(s, source)]


def extract_numbers(text: str) -> list[str]:
    return [m.group(0) for m in _NUM.finditer(text)]


def _numeric_leaves(obj: Any) -> list[float]:
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _numeric_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += _numeric_leaves(v)
    elif isinstance(obj, str):
        out += [_parse(t)[0] for t in extract_numbers(obj)]
    return out


def _parse(token: str) -> tuple[float, int, bool]:
    """-> (value, decimals shown, is_percent)"""
    t = token.replace("−", "-").replace(",", "")
    is_pct = t.endswith("%")
    t = t.strip("%$£€").replace("$", "").replace("£", "").replace("€", "")
    decimals = len(t.split(".")[1]) if "." in t else 0
    return float(t), decimals, is_pct


def _matches(value: float, decimals: int, is_pct: bool, allowed: list[float]) -> bool:
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    for a in allowed:
        # a fraction 0.034 may be written as 3.4 or 3.4%
        candidates = (abs(a), abs(a) * 100)
        if any(abs(abs(value) - c) <= tol for c in candidates):
            return True
    return False


def check_numbers(text: str, *allowed_sources: Any) -> list[str]:
    """Return the number tokens in `text` that are NOT grounded in any allowed source.

    allowed_sources may be pydantic models, dicts, lists or strings (verbatim rule quotes).
    Rounding rule: a token showing d decimals matches a source value if it equals that value
    (or value*100, for fractions written as percentages) rounded to d decimals.
    """
    allowed: list[float] = []
    for src in allowed_sources:
        if hasattr(src, "model_dump"):
            src = src.model_dump()
        allowed += _numeric_leaves(src)
    bad = []
    for tok in extract_numbers(text):
        value, decimals, is_pct = _parse(tok)
        if not _matches(value, decimals, is_pct, allowed):
            bad.append(tok)
    return bad
