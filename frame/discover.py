"""Pull open markets. Live data goes ONLY through the core's compliant client (via the adapter);
the frame adds no HTTP client of its own. Fixtures back tests and --replay."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .adapter import CoreAdapter
from .schemas import Market


def load_fixture_markets(path: Path) -> list[Market]:
    return [Market.model_validate(m) for m in json.loads(Path(path).read_text(encoding="utf-8"))]


def _still_open(m: Market, now: datetime) -> bool:
    # Kalshi has shown "open" markets with a past close date: re-filter ourselves.
    if not m.close_time_utc:
        return True
    try:
        return datetime.fromisoformat(m.close_time_utc.replace("Z", "+00:00")) > now
    except ValueError:
        return True


def discover(adapter: CoreAdapter, venues: list[str], excluded_categories: tuple[str, ...] = ("crypto",),
             kalshi_member: bool = False, now: datetime | None = None) -> list[Market]:
    if "kalshi" in venues and not kalshi_member:
        venues = [v for v in venues if v != "kalshi"]
    now = now or datetime.now(timezone.utc)
    out = []
    for m in adapter.list_open_markets(venues):
        if (m.category or "").lower() in excluded_categories:
            continue
        if m.venue == "manifold":  # signal only, never a leg
            continue
        if _still_open(m, now):
            out.append(m)
    return out
