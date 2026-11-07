"""Append-only, SHA-256 hash-chained audit log (JSONL).

Same record layout and hash rule as ``frame/audit.py`` on the frame/llm-layer
branch, so the two logs can be merged into one later:

    {seq, ts, kind, payload, prev_hash, hash, paper_trading}
    hash = sha256(prev_hash + canonical JSON of the record without "hash")

Every record is tagged ``paper_trading: true``. Editing, deleting or reordering
any line breaks :meth:`AuditLog.verify` from that line on.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .models import OrderBook

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def book_digest(book: OrderBook | None) -> dict | None:
    """A short summary of the book an order was checked against, plus a hash of every level."""
    if book is None:
        return None
    levels = {"bids": [[lv.price, lv.size] for lv in book.bids], "asks": [[lv.price, lv.size] for lv in book.asks]}
    return {
        "token_id": book.token_id,
        "ts": book.ts,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "sha256": sha(canonical(levels)),
    }


class AuditLog:
    """Hash-chained log. ``path=None`` keeps it in memory (handy for tests and backtests)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._records: list[dict] = []
        if self.path and self.path.exists():
            self._records = self._read(self.path)
        elif self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read(path: Path) -> list[dict]:
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        prev = self._records[-1]["hash"] if self._records else GENESIS
        rec = {
            "seq": len(self._records),
            "ts": time.time(),
            "kind": kind,
            "payload": payload,
            "prev_hash": prev,
            "paper_trading": True,
        }
        rec["hash"] = sha(prev + canonical(rec))
        self._records.append(rec)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical(rec) + "\n")
        return rec

    def records(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self._records if kind is None or r["kind"] == kind]

    def verify(self) -> tuple[bool, int | None]:
        """Check the chain as stored (re-reading the file if there is one). -> (ok, first bad seq)."""
        return verify_records(self._read(self.path) if self.path else self._records)

    def __len__(self) -> int:
        return len(self._records)


def verify_records(records: list[dict]) -> tuple[bool, int | None]:
    prev = GENESIS
    for i, rec in enumerate(records):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if (
            rec.get("seq") != i
            or rec.get("prev_hash") != prev
            or rec.get("paper_trading") is not True
            or sha(prev + canonical(body)) != rec.get("hash")
        ):
            return False, i
        prev = rec["hash"]
    return True, None


def verify_file(path: str | Path) -> tuple[bool, int | None]:
    return verify_records(AuditLog._read(Path(path)))
