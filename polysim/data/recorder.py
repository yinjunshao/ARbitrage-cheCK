"""Long-running, polite recorder for whole Polymarket events (e.g. date or price ladders).

What it writes to ``out_dir`` (keep it out of git: Polymarket's terms bar
redistributing their data, so recordings stay on our own machines):

* ``books-YYYYMMDD.jsonl.gz``  one book snapshot per token per round, top
  ``depth`` levels, rotated daily (UTC). Each round is a separate gzip member,
  so a crash loses at most one round.
* ``markets.json``            every market seen, with its fee schedule.
* ``events/<slug>.json``      the raw Gamma event (rules text, tags), refreshed.
* ``resolutions.jsonl``       one line per market as soon as it resolves.
* ``recorder.log``            errors and progress.

Only public GET endpoints are called, at most ``rate`` requests a second (far
below Polymarket's documented limits), with a back-off on errors.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..events import BookEvent, event_to_dict
from ..models import Market
from .feeds import market_to_dict
from .polymarket import PolymarketClient, parse_gamma_market, parse_resolution

USER_AGENT = "polysim/0.1 (read-only research; paper trading only; never places orders)"


def open_markets_of(event: dict) -> list[Market]:
    """Markets of an event that are open and have an order book."""
    out = []
    for raw in event.get("markets", []):
        if raw.get("closed") or not raw.get("active", True) or raw.get("enableOrderBook") is False:
            continue
        try:
            m = parse_gamma_market(raw)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if m.token_ids:
            out.append(m)
    return out


class EventRecorder:
    def __init__(
        self,
        client: PolymarketClient,
        slugs: list[str],
        out_dir: str | Path,
        *,
        interval: float = 60.0,
        depth: int = 20,
        rate: float = 5.0,
        refresh: float = 1800.0,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client, self.slugs = client, list(slugs)
        self.out = Path(out_dir)
        (self.out / "events").mkdir(parents=True, exist_ok=True)
        self.interval, self.depth, self.rate, self.refresh = interval, depth, rate, refresh
        self.clock, self.sleep = clock, sleep
        self._log_fn = log
        self.markets: dict[str, Market] = {}  # market_id -> Market (everything ever seen)
        self.active: dict[str, Market] = {}  # market_id -> Market still being recorded
        self.resolved: set[str] = set()
        self._event_markets: dict[str, set[str]] = {}  # event slug -> its open market ids
        self.snapshots = 0
        self.errors = 0
        self._last_refresh = -1e18
        self._load_state()

    # ------------------------------------------------------------------ state

    def _load_state(self) -> None:
        mf = self.out / "markets.json"
        if mf.exists():
            from .feeds import market_from_dict

            for d in json.loads(mf.read_text()):
                m = market_from_dict(d)
                self.markets[m.market_id] = m
        rf = self.out / "resolutions.jsonl"
        if rf.exists():
            for line in rf.read_text().splitlines():
                if line.strip():
                    self.resolved.add(json.loads(line)["market_id"])

    def log(self, msg: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
        with (self.out / "recorder.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self._log_fn:
            self._log_fn(line)

    def _save_markets(self) -> None:
        tmp = self.out / "markets.json.tmp"
        tmp.write_text(json.dumps([market_to_dict(m) for m in self.markets.values()], indent=2))
        tmp.replace(self.out / "markets.json")

    # ------------------------------------------------------------------ refresh

    def refresh_events(self) -> None:
        """Re-read each event: pick up new markets, drop closed ones, write their results."""
        active: dict[str, Market] = {}
        for slug in self.slugs:
            try:
                res = self.client.get_event_raw(slug)
            except Exception as exc:  # keep recording what we already know
                self.errors += 1
                self.log(f"warn: event {slug}: {exc}")
                keep = self._event_markets.get(slug, set())
                active.update({k: m for k, m in self.active.items() if k in keep})
                continue
            (self.out / "events" / f"{slug}.json").write_text(json.dumps(res, indent=1))
            opened = [replace(m, event_slug=slug) for m in open_markets_of(res)]
            for m in opened:
                active[m.market_id] = m
                self.markets[m.market_id] = m
            for raw in res.get("markets", []):
                self._maybe_resolution(raw)
            self._event_markets[slug] = {m.market_id for m in opened}
        self.active = active
        self._save_markets()
        self._last_refresh = self.clock()
        n_tokens = sum(len(m.token_ids) for m in active.values())
        self.log(f"refresh: {len(active)} open markets, {n_tokens} tokens, {len(self.resolved)} resolved so far")

    def _maybe_resolution(self, raw: dict) -> None:
        if not raw.get("closed"):
            return
        try:
            ev = parse_resolution(raw)
        except (ValueError, KeyError, json.JSONDecodeError):
            return
        if ev is None or ev.market_id in self.resolved:
            return
        # Only markets we actually recorded matter.
        if ev.market_id not in self.markets:
            return
        with (self.out / "resolutions.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event_to_dict(ev)) + "\n")
        self.resolved.add(ev.market_id)
        self.log(f"resolved: {ev.market_id} {ev.payouts}")

    # ------------------------------------------------------------------ recording

    def books_path(self, ts: float) -> Path:
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d")
        return self.out / f"books-{day}.jsonl.gz"

    def record_round(self) -> int:
        started = self.clock()
        lines = []
        for m in list(self.active.values()):
            for tid in m.token_ids:
                t0 = self.clock()
                try:
                    book = self.client.get_order_book(tid)
                except Exception as exc:
                    self.errors += 1
                    self.log(f"warn: book {tid[:16]}… ({m.slug}): {exc}")
                    if "429" in str(exc):
                        self.sleep(30)  # rate-limited: back off hard
                    continue
                finally:
                    self.sleep(max(0.0, 1.0 / self.rate - (self.clock() - t0)))
                book.token_id = book.token_id or tid
                book.bids, book.asks = book.bids[: self.depth], book.asks[: self.depth]
                ts = self.clock()
                book.ts = ts
                lines.append(json.dumps(event_to_dict(BookEvent(ts, book))))
        if lines:
            with gzip.open(self.books_path(started), "at", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        self.snapshots += len(lines)
        return len(lines)

    def run(self, duration: float) -> None:
        deadline = self.clock() + duration
        self.log(f"start: events {', '.join(self.slugs)}; every {self.interval:g}s, depth {self.depth}, "
                 f"<= {self.rate:g} req/s, for {duration / 3600:.1f}h -> {self.out}")
        rounds = 0
        try:
            while self.clock() < deadline:
                start = self.clock()
                if start - self._last_refresh >= self.refresh:
                    self.refresh_events()
                    if not self.active:
                        self.log("no open markets left; stopping")
                        break
                n = self.record_round()
                rounds += 1
                if rounds == 1 or rounds % 30 == 0:
                    self.log(f"round {rounds}: {n} books ({self.snapshots} total, {self.errors} errors)")
                self.sleep(max(0.0, self.interval - (self.clock() - start)))
        except KeyboardInterrupt:
            self.log("stopped by user")
        finally:
            self.refresh_events_safely()
            self.log(f"done: {self.snapshots} snapshots, {len(self.resolved)} resolutions, {self.errors} errors")

    def refresh_events_safely(self) -> None:
        try:
            self.refresh_events()
        except Exception as exc:  # never lose the summary line over a final refresh
            self.log(f"warn: final refresh failed: {exc}")
