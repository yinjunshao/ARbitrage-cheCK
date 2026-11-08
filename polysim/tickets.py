"""Trade tickets: how an opportunity the checker approved reaches the paper desk.

The numeric core (``arbcheck``) decides *whether* a basket is worth buying and at
what prices. polysim decides what would have *happened* if we had tried: the
book can move while the legs are sent, a leg can fail and have to be unwound,
risk limits can refuse it, and a person can say no. A :class:`Ticket` is the
hand-over between the two, in plain JSON so neither side imports the other::

    {"ticket_id": "...", "ts": 1794080000.0, "kind": "a_implies_b",
     "floor": 1.0, "shares": 120, "expected_cost": 118.4, "expected_profit": 1.6,
     "legs": [{"token_id": "...", "market_id": "0x..", "outcome": "NO", "max_price": 0.42, "label": "A NO"},
              {"token_id": "...", "market_id": "0x..", "outcome": "YES", "max_price": 0.57, "label": "B YES"}]}

``floor`` is the basket's guaranteed payout per share across every world the
relation allows. ``max_price`` is the worst price per share the checker priced
that leg at; the paper desk never pays more.

:func:`ticket_from_core` builds a ticket from the core's own objects (a list of
markets, an ``ArbResult`` and the guard's ``Decision``) without importing them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

APPROVED_STATUSES = ("OK", "WARN")  # the core guard's statuses that allow a trade


@dataclass(frozen=True)
class TicketLeg:
    token_id: str
    outcome: str  # "YES" or "NO"
    max_price: float | None = None
    market_id: str = ""
    label: str = ""  # e.g. "A NO", for people reading the report


@dataclass(frozen=True)
class Ticket:
    ticket_id: str  # the checker's decision id: carried into every order and audit record
    ts: float  # unix seconds when the checker priced it
    legs: tuple[TicketLeg, ...]
    shares: float  # basket shares: buy this many of every leg
    floor: float = 1.0  # guaranteed payout per basket share
    expected_cost: float = 0.0  # USDC, fees included, as the checker priced it
    expected_profit: float = 0.0  # USDC the checker expects to lock in
    kind: str = ""
    source: str = ""
    note: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def guaranteed_value(self) -> float:
        return self.floor * self.shares


# --------------------------------------------------------------------------- JSON


def ticket_to_dict(t: Ticket) -> dict:
    d = asdict(t)
    d["legs"] = [asdict(leg) for leg in t.legs]
    return d


def parse_ticket(d: dict) -> Ticket:
    """Build a ticket from a dict, refusing anything the desk could not trade safely."""
    if not isinstance(d, dict):
        raise ValueError("ticket must be a JSON object")
    tid = str(d.get("ticket_id") or d.get("decision_id") or "").strip()
    if not tid:
        raise ValueError("ticket_id is missing")
    try:
        ts = float(d["ts"])
        shares = float(d["shares"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{tid}: ts and shares must be numbers") from exc
    if shares <= 0:
        raise ValueError(f"{tid}: shares must be positive")
    raw_legs = d.get("legs") or []
    if not raw_legs:
        raise ValueError(f"{tid}: no legs")
    legs = []
    for i, leg in enumerate(raw_legs):
        token = str(leg.get("token_id") or "").strip()
        if not token:
            raise ValueError(f"{tid}: leg {i} has no token_id")
        outcome = str(leg.get("outcome") or "").upper()
        if outcome not in ("YES", "NO"):
            raise ValueError(f"{tid}: leg {i} outcome must be YES or NO")
        mp = leg.get("max_price")
        if mp is not None:
            mp = float(mp)
            if not 0 < mp < 1:
                raise ValueError(f"{tid}: leg {i} max_price {mp} is not a price between 0 and 1")
        legs.append(TicketLeg(token, outcome, mp, str(leg.get("market_id") or ""), str(leg.get("label") or "")))
    if len({leg.token_id for leg in legs}) != len(legs):
        raise ValueError(f"{tid}: the same token appears in two legs")
    floor = float(d.get("floor", 1.0))
    if floor <= 0:
        raise ValueError(f"{tid}: floor must be positive (a basket that can pay 0 is not an arbitrage)")
    return Ticket(
        tid, ts, tuple(legs), shares, floor,
        float(d.get("expected_cost") or 0.0), float(d.get("expected_profit") or 0.0),
        str(d.get("kind") or ""), str(d.get("source") or ""), str(d.get("note") or ""), dict(d.get("meta") or {}),
    )


def load_tickets(path: str | Path) -> tuple[list[Ticket], list[str]]:
    """Read tickets from a JSON list or JSONL file. Returns (tickets, problems with the rest)."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if text.startswith("["):
        rows = json.loads(text)
    else:
        rows = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    rows.append({"_bad": f"line {n}: not JSON ({exc.msg})"})
    tickets, problems = [], []
    for row in rows:
        if isinstance(row, dict) and "_bad" in row:
            problems.append(row["_bad"])
            continue
        try:
            tickets.append(parse_ticket(row))
        except ValueError as exc:
            problems.append(str(exc))
    return tickets, problems


def save_tickets(tickets: Iterable[Ticket], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for t in tickets:
            fh.write(json.dumps(ticket_to_dict(t)) + "\n")


# --------------------------------------------------------------------------- from the core


def _label_index(label: str) -> int:
    """Core leg labels: A..Z, then AA..AZ, BA..BZ (see arbcheck.engine.LABELS)."""
    if len(label) == 1:
        return ord(label) - 65
    return 26 + (ord(label[0]) - 65) * 26 + (ord(label[1]) - 65)


def ticket_from_core(
    markets: Sequence[Any],
    arb: Any,
    decision: Any,
    *,
    ts: float,
    decision_id: str = "",
    source: str = "arbcheck",
    note: str = "",
) -> Ticket:
    """Turn the core's approved decision into a ticket.

    Duck-typed against ``arbcheck``: each market needs ``id`` and
    ``meta["yes_token"]`` / ``meta["no_token"]``; ``arb`` needs ``legs``
    (e.g. ``(("A", "NO"), ("B", "YES"))``), ``floor`` and ``kind``; ``decision``
    needs ``status``, ``approved_shares``, ``approved_cost``,
    ``approved_profit`` and ``fills`` (each with per-leg ``prices``).
    """
    status = getattr(decision, "status", "")
    if status not in APPROVED_STATUSES:
        raise ValueError(f"the core guard did not approve this ({status or 'no status'}): nothing to trade")
    shares = float(getattr(decision, "approved_shares", 0.0) or 0.0)
    if shares <= 0 or not getattr(arb, "legs", None):
        raise ValueError("the core approved 0 shares: nothing to trade")
    fills = list(getattr(decision, "fills", None) or getattr(arb, "fills", None) or [])
    legs = []
    for i, (label, side) in enumerate(arb.legs):
        m = markets[_label_index(label)]
        meta = getattr(m, "meta", {}) or {}
        side = str(side).upper()
        token = meta.get("yes_token" if side == "YES" else "no_token")
        if not token:
            raise ValueError(f"market {getattr(m, 'id', '?')} has no {side} token id: cannot trade it on Polymarket")
        prices = [f.prices[i] for f in fills if getattr(f, "shares", 0) > 0]
        legs.append(TicketLeg(str(token), side, max(prices) if prices else None, str(getattr(m, "id", "")),
                              f"{label} {side}"))
    did = decision_id or "core:" + "|".join(str(getattr(m, "id", "")) for m in markets)
    return Ticket(
        did, float(ts), tuple(legs), shares, float(arb.floor),
        float(getattr(decision, "approved_cost", 0.0) or 0.0), float(getattr(decision, "approved_profit", 0.0) or 0.0),
        str(getattr(arb, "kind", "")), source, note or str(getattr(decision, "headline", "")),
        {"core_status": status, "haircut_per_share": float(getattr(decision, "haircut_per_share", 0.0) or 0.0)},
    )
