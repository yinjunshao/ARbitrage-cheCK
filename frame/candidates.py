"""Candidate generation: block, embed, cosine top-k across venues, plus within-event pairs.

Embedder: local BAAI/bge-small-en-v1.5 via sentence-transformers if installed (optional, pulls torch);
otherwise a deterministic hashing embedder so tests and the offline demo need no model download.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Protocol

import numpy as np

from .schemas import Market


class Embedder(Protocol):
    name: str
    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Word + char-trigram feature hashing, L2-normalised. Also serves as the title-similarity baseline."""
    name = "hashing-512"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _feats(self, t: str) -> list[str]:
        words = re.findall(r"[a-z0-9]+", t.lower())
        grams = [w[i:i + 3] for w in words for i in range(max(1, len(w) - 2))]
        return words + [f"#{g}" for g in grams]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for f in self._feats(t):
                h = int.from_bytes(hashlib.md5(f.encode()).digest()[:4], "little")
                out[i, h % self.dim] += 1.0 if (h >> 31) & 1 == 0 else -1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(n == 0, 1, n)


class BgeEmbedder:
    name = "bge-small-en-v1.5"

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)


def default_embedder() -> Embedder:
    try:
        return BgeEmbedder()
    except Exception:
        return HashingEmbedder()


def embed_text(m: Market) -> str:
    return f"{m.title} | {m.outcome_label} | closes {m.close_time_utc or '?'} | {m.rules_text[:300]}"


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)   # mixing naive and aware times raised TypeError


def blocked_ok(a: Market, b: Market, window_days: int) -> bool:
    """Date blocking only. Category/type rules live in frame/rules.py so every refusal is logged."""
    da, db = _dt(a.close_time_utc), _dt(b.close_time_utc)
    if da and db and abs(da - db) > timedelta(days=window_days):
        return False
    return True


def _event(m: Market) -> tuple[str, str]:
    return (m.venue, m.event_id or f"solo:{m.id}")


def cross_event_candidates(markets: list[Market], embedder: Embedder, k: int = 8, window_days: int = 7,
                           min_similarity: float = 0.0) -> list[tuple[Market, Market, float, str]]:
    """Top-k most similar markets from OTHER events (same venue or another venue)."""
    if len(markets) < 2:
        return []
    E = embedder.embed([embed_text(m) for m in markets])
    S = E @ E.T
    seen, out = set(), []
    for i, a in enumerate(markets):
        taken = 0
        for j in np.argsort(-S[i]):
            j = int(j)
            if S[i, j] < min_similarity:
                break
            b = markets[j]
            if j == i or _event(a) == _event(b) or not blocked_ok(a, b, window_days):
                continue
            key = tuple(sorted((f"{a.venue}:{a.id}", f"{b.venue}:{b.id}")))
            if key not in seen:
                seen.add(key)
                out.append((a, b, float(S[i, j]), "cross_venue" if a.venue != b.venue else "cross_event"))
            taken += 1
            if taken >= k:
                break
    return sorted(out, key=lambda t: -t[2])


def within_event_candidates(markets: list[Market], link_filter=None,
                            max_per_event: int = 30) -> list[tuple[Market, Market, float, str]]:
    """Nested deadlines / thresholds live inside one event group. With `link_filter` (the core's cheap
    deterministic check) only linked pairs are kept, so a 100-outcome event does not flood the judge."""
    groups: dict[tuple[str, str], list[Market]] = {}
    for m in markets:
        if m.event_id:
            groups.setdefault((m.venue, m.event_id), []).append(m)
    out = []
    for g in groups.values():
        pairs = [(a, b) for a, b in combinations(g, 2) if link_filter is None or link_filter(a, b)]
        out += [(a, b, 1.0, "within_event") for a, b in pairs[:max_per_event]]
    return out


def generate(markets: list[Market], embedder: Embedder, k: int, window_days: int, min_similarity: float = 0.0,
             link_filter=None) -> list[tuple[Market, Market, float, str]]:
    """-> (a, b, similarity, how) with how in within_event | cross_event | cross_venue."""
    return (within_event_candidates(markets, link_filter)
            + cross_event_candidates(markets, embedder, k, window_days, min_similarity))
