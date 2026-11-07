"""Append-only, SHA-256 hash-chained audit log (JSONL).

Each record: {seq, ts, kind, payload, prev_hash, hash}; hash = sha256(prev_hash + canonical(record without hash)).
Tampering with any line breaks verification from that line on.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return -1, GENESIS
        last = None
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return -1, GENESIS
        rec = json.loads(last)
        return rec["seq"], rec["hash"]

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            seq, prev = self._tail()
            rec = {"seq": seq + 1, "ts": time.time(), "kind": kind, "payload": payload, "prev_hash": prev}
            rec["hash"] = sha(prev + _canon(rec))
            with self.path.open("a", encoding="utf-8") as f:
                f.write(_canon(rec) + "\n")
            return rec

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def verify(self) -> tuple[bool, int | None]:
        """-> (ok, first bad seq or None)."""
        prev = GENESIS
        for i, rec in enumerate(self.records()):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("seq") != i or rec.get("prev_hash") != prev or sha(prev + _canon(body)) != rec.get("hash"):
                return False, i
            prev = rec["hash"]
        return True, None
