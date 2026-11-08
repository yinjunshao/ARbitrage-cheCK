"""Append-only, SHA-256 hash-chained audit log (JSONL).

Each record: {seq, ts, kind, payload, prev_hash, hash}; hash = sha256(prev_hash + canonical(record without hash)).
Tampering with any line breaks verification from that line on.

Safe with several processes on one file (the app and the monitor at once): every append takes an OS file
lock, and re-reads the chain tail if another process wrote since. Unreadable lines (e.g. from an old crash)
are skipped by `records()` and reported by `verify()`, never fatal.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def _file_lock(path: Path):
    """Exclusive lock across processes, on a side file `<log>.lock`."""
    with open(path, "a+b") as f:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.005)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lockfile = self.path.with_name(self.path.name + ".lock")
        self._lock = threading.Lock()
        self._last: tuple[int, str] | None = None   # (seq, hash) of the last record we know of
        self._size = -1                              # file size right after our last read/write

    def _size_now(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def _tail(self) -> tuple[int, str]:
        size = self._size_now()
        if self._last is not None and size == self._size:
            return self._last
        if size == 0:
            return -1, GENESIS
        # Read backwards from the end until a complete, parseable record turns up.
        with self.path.open("rb") as f:
            chunk = 1 << 16
            while True:
                start = max(0, size - chunk)
                f.seek(start)
                lines = f.read(size - start).splitlines()
                if start > 0:
                    lines = lines[1:]          # the first one may be cut in half
                for line in reversed(lines):
                    try:
                        rec = json.loads(line)
                        return rec["seq"], rec["hash"]
                    except (ValueError, KeyError, TypeError):
                        continue
                if start == 0:
                    return -1, GENESIS
                chunk *= 4

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock, _file_lock(self._lockfile):
            seq, prev = self._tail()
            rec = {"seq": seq + 1, "ts": time.time(), "kind": kind, "payload": payload, "prev_hash": prev}
            rec["hash"] = sha(prev + _canon(rec))
            with self.path.open("ab") as f:
                f.write((_canon(rec) + "\n").encode("utf-8"))
            self._last, self._size = (rec["seq"], rec["hash"]), self._size_now()
            return rec

    def _lines(self):
        if not self.path.exists():
            return
        with self.path.open("rb") as f:
            for line in f:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        yield None

    def records(self) -> list[dict[str, Any]]:
        return [r for r in self._lines() if r is not None]

    def verify(self) -> tuple[bool, int | None]:
        """-> (ok, index of the first bad line or None)."""
        prev = GENESIS
        for i, rec in enumerate(self._lines()):
            if rec is None:
                return False, i
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("seq") != i or rec.get("prev_hash") != prev or sha(prev + _canon(body)) != rec.get("hash"):
                return False, i
            prev = rec["hash"]
        return True, None
