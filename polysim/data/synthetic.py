"""Synthetic binary-market generator for offline development and tests.

The YES probability follows a random walk in logit space with a slow drift
towards the (pre-drawn) true outcome, which mimics information arriving over
the life of a market. The NO book mirrors YES (p_no = 1 - p_yes).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..events import BookEvent, Event, ResolutionEvent
from ..models import Market, OrderBook, Token


@dataclass
class SyntheticConfig:
    n_steps: int = 500
    step_seconds: float = 60.0
    start_ts: float = 1_700_000_000.0
    p0: float = 0.5
    volatility: float = 0.08  # logit-space std-dev per step
    drift: float = 0.004  # logit drift per step towards the true outcome
    spread_ticks: int = 2
    levels: int = 5
    base_size: float = 300.0
    tick: float = 0.01
    yes_wins: bool | None = None  # None => drawn with probability p0


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _book(token_id: str, ts: float, mid: float, cfg: SyntheticConfig, rng: random.Random) -> OrderBook:
    book = OrderBook.from_mid(
        token_id, ts, mid, spread=cfg.spread_ticks * cfg.tick, levels=cfg.levels, tick=cfg.tick
    )
    for side in (book.bids, book.asks):
        for i, lv in enumerate(side):  # thicker further from the touch, with noise
            lv.size = round(cfg.base_size * rng.uniform(0.5, 1.5) * (1 + 0.5 * i), 2)
    return book


def generate_binary_market(
    cfg: SyntheticConfig | None = None, seed: int = 0, name: str = "synthetic"
) -> tuple[Market, list[Event]]:
    cfg = cfg or SyntheticConfig()
    rng = random.Random(seed)
    yes_wins = cfg.yes_wins if cfg.yes_wins is not None else rng.random() < cfg.p0
    market = Market(
        market_id=f"{name}-{seed}",
        question=f"Synthetic market {name} (seed {seed})?",
        tokens=(Token(f"{name}-{seed}-YES", "Yes"), Token(f"{name}-{seed}-NO", "No")),
        slug=f"{name}-{seed}",
        tick_size=cfg.tick,
        min_order_size=1.0,
        end_ts=cfg.start_ts + cfg.n_steps * cfg.step_seconds,
    )
    yes, no = market.tokens
    x = _logit(min(max(cfg.p0, 0.02), 0.98))
    direction = 1.0 if yes_wins else -1.0
    lo, hi = _logit(0.02), _logit(0.98)

    events: list[Event] = []
    for i in range(cfg.n_steps):
        ts = cfg.start_ts + i * cfg.step_seconds
        x = min(max(x + direction * cfg.drift + rng.gauss(0, cfg.volatility), lo), hi)
        p = _sigmoid(x)
        events.append(BookEvent(ts, _book(yes.token_id, ts, p, cfg, rng)))
        events.append(BookEvent(ts, _book(no.token_id, ts, 1 - p, cfg, rng)))

    events.append(
        ResolutionEvent(
            market.end_ts,
            market.market_id,
            {yes.token_id: 1.0 if yes_wins else 0.0, no.token_id: 0.0 if yes_wins else 1.0},
        )
    )
    return market, events
