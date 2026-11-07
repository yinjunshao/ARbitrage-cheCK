"""A SYNTHETIC dataset for exercising the evaluation end to end (``polysim evaluate --demo``).

Every market, book and result here is invented. It exists to show how the
evaluation behaves, and to test it, before we have enough recorded real data.
It must never be presented as evidence that the checker works.

Each candidate is a ladder pair, "X by date 1" (A) implies "X by date 2" (B),
so buying A-NO + B-YES pays at least $1 if the rules really line up. Six kinds:

* ``real``       a genuine gap with deep books and matching rules
* ``fees_eat``   looks positive at mid prices, but the spread and fees eat it
* ``thin``       a real gap on a handful of shares, then expensive levels
* ``mismatch``   the rules differ ("touches at any time" vs "closes above"),
                 and the "impossible" outcome often happens
* ``unclear``    a real gap, but the rules could not be confirmed (our checker's
                 caution costs money here: these become misses)
* ``vanishing``  looks fine when flagged, but the second leg's sellers leave
                 during the order delay (these become false alarms)

Plus a few deliberately bad inputs, which the evaluation must reject.
"""

from __future__ import annotations

import random

from .events import BookEvent, Event, ResolutionEvent
from .models import Market, OrderBook, PriceLevel, Token

KINDS = ("real", "fees_eat", "thin", "mismatch", "unclear", "vanishing")
DAY = 86_400.0
START = 1_767_225_600.0  # 2026-01-01, an arbitrary invented start


def _book(token: str, ts: float, asks: list[tuple[float, float]], spread: float = 0.02) -> BookEvent:
    best = asks[0][0] if asks else None
    bids = [PriceLevel(round(best - spread, 2), 400)] if best is not None else []
    return BookEvent(ts, OrderBook(token, ts, bids, [PriceLevel(p, s) for p, s in asks]))


def make_eval_dataset(seed: int = 0, n: int = 60, days: int = 30):
    """-> (markets, events, raw candidate dicts, split_ts). Half the period is for tuning."""
    rng = random.Random(seed)
    markets: list[Market] = []
    events: list[Event] = []
    cands: list[dict] = []
    fee = dict(category="politics", fee_rate=0.04, min_order_size=1.0)

    for i in range(n):
        kind = KINDS[i % len(KINDS)] if i < len(KINDS) * 2 else rng.choice(KINDS)
        t = START + (i + 0.5) * days * DAY / n
        a, b = f"A{i}", f"B{i}"
        a_no, b_yes = f"{a}-NO", f"{b}-YES"
        markets.append(Market(a, f"Will X{i} happen by date 1? (invented)", (Token(f"{a}-YES", "Yes"), Token(a_no, "No")), **fee))
        markets.append(Market(b, f"Will X{i} happen by date 2? (invented)", (Token(b_yes, "Yes"), Token(f"{b}-NO", "No")), **fee))

        total = {"real": rng.uniform(0.90, 0.95), "fees_eat": rng.uniform(0.985, 0.995),
                 "thin": rng.uniform(0.90, 0.94), "mismatch": rng.uniform(0.88, 0.93),
                 "unclear": rng.uniform(0.90, 0.95), "vanishing": rng.uniform(0.90, 0.95)}[kind]
        pa = round(rng.uniform(0.35, 0.65), 2)
        pb = round(total - pa, 2)
        if kind == "thin":
            a_asks = [(pa, rng.choice([5, 8, 10, 12])), (round(pa + 0.08, 2), 600)]
            b_asks = [(pb, 600)]
        else:
            a_asks, b_asks = [(pa, rng.randint(300, 600))], [(pb, rng.randint(300, 600))]

        for k in range(0, 10):  # a snapshot every second around the flag time
            ts = t - 1 + k
            events.append(_book(a_no, ts, a_asks))
            gone = kind == "vanishing" and k >= 2  # sellers leave right after the flag
            events.append(_book(b_yes, ts, [] if gone else b_asks))

        # Outcome. Possible worlds for A implies B: (0,0), (0,1), (1,1). (1,0) breaks the relation.
        if kind == "mismatch" and rng.random() < 0.7:
            world = (1, 0)  # the rules differed and the "impossible" result happened
        else:
            world = rng.choice([(0, 0), (0, 1), (1, 1)])
        res_ts = t + rng.uniform(5, 20) * DAY
        events.append(ResolutionEvent(res_ts, a, {f"{a}-YES": float(world[0]), a_no: float(1 - world[0])}))
        events.append(ResolutionEvent(res_ts, b, {b_yes: float(world[1]), f"{b}-NO": float(1 - world[1])}))

        rule = {"mismatch": "mismatch", "unclear": "unclear"}.get(kind, "match")
        cands.append({"id": f"c{i:02d}-{kind}", "ts": t, "token_ids": [a_no, b_yes], "floor": 1.0,
                      "rule_check": rule, "relation": "a_implies_b", "decision_id": f"demo-{i}", "note": kind})

    # Deliberately bad inputs: each one must be rejected, with its reason shown.
    first = cands[0]
    cands += [
        {**first, "note": "duplicate id"},
        {**first, "id": "bad-floor", "floor": 3.0, "note": "claims $3 guaranteed on two legs"},
        {**first, "id": "bad-token", "token_ids": ["NOPE-YES", first["token_ids"][1]], "note": "unknown market"},
        {**first, "id": "bad-stale", "ts": first["ts"] + 600, "note": "book 10 minutes old"},
        {**first, "id": "bad-late", "ts": first["ts"] + 40 * DAY, "note": "after resolution"},
        {"id": "bad-shape", "ts": "yesterday", "token_ids": "A0-NO", "floor": 1, "note": "malformed"},
    ]
    events.sort(key=lambda e: (e.ts, isinstance(e, ResolutionEvent)))
    return markets, events, cands, START + days * DAY / 2
