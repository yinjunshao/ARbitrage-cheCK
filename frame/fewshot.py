"""'Training' without fine-tuning: store human decisions, retrieve nearest confirmed examples as few-shot.

Split is assigned by EVENT so one event never lands in both dev and sealed. Never retrieve from sealed.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import numpy as np

from .candidates import Embedder, embed_text
from .schemas import Market


def split_for(event_key: str, sealed_fraction: float = 0.3) -> str:
    h = int(hashlib.sha256(event_key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "sealed" if h < sealed_fraction else "dev"


def event_key(a: Market, b: Market) -> str:
    return "|".join(sorted([f"{a.venue}:{a.event_id or a.id}", f"{b.venue}:{b.event_id or b.id}"]))


class LabelStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def add(self, pair_id: str, a: Market, b: Market, relation: str, confirmed: bool, note: str = "",
            proposed: str | None = None) -> dict:
        ek = event_key(a, b)
        rec = {"pair_id": pair_id, "relation": relation, "proposed": proposed, "confirmed": confirmed,
               "note": note, "event_key": ek, "split": split_for(ek), "ts": time.time(),
               "a": a.model_dump(), "b": b.model_dump()}
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.open(encoding="utf-8") if l.strip()]


class FewShot:
    def __init__(self, store: LabelStore, embedder: Embedder, k: int = 5):
        self.store, self.embedder, self.k = store, embedder, k

    def retrieve(self, a: Market, b: Market) -> list[dict]:
        ek = event_key(a, b)
        pool = [r for r in self.store.all() if r["split"] == "dev" and r["event_key"] != ek]
        if not pool:
            return []
        q = self.embedder.embed([embed_text(a) + " || " + embed_text(b)])[0]
        E = self.embedder.embed([embed_text(Market(**r["a"])) + " || " + embed_text(Market(**r["b"])) for r in pool])
        idx = np.argsort(-(E @ q))[: self.k]
        return [{"title_a": pool[i]["a"]["title"], "title_b": pool[i]["b"]["title"],
                 "relation": pool[i]["relation"], "confirmed": pool[i]["confirmed"]} for i in idx]
