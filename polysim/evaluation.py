"""Evaluation: is our checker better than a naive price-only checker, on data it has not seen?

Input
-----
* **Candidates**: claimed arbitrages, one JSON object per line, for example
  ``{"id": "c1", "ts": 1767225600, "token_ids": ["A-NO", "B-YES"], "floor": 1.0,
  "rule_check": "match", "relation": "a_implies_b", "decision_id": "..."}``.
  ``floor`` is the guaranteed payout per basket share if the relation holds, and
  ``rule_check`` says whether the two markets' rules agree ("match"),
  disagree ("mismatch") or could not be confirmed ("unclear"). Both come from
  the checker (arbcheck / frame); this module does not re-derive them.
* **Recorded books and resolutions** (``polysim record`` / ``fetch``) and the
  markets file.

What happens to each candidate
------------------------------
1. ``naive``  the benchmark flags it if floor > sum of mid prices: no fees,
   no depth, no rule check. This is "the conventional program".
2. ``fees``   still positive after paying the best asks plus taker fees?
3. ``depth``  walk the books: at least ``min_shares`` baskets fillable, with an
   average edge of at least ``min_edge`` per share after fees?
4. ``rules``  the rules check said "match"? ("unclear" counts as a fail.)

Our checker takes a candidate only if it passes all four. Then both checkers'
trades are **executed on paper** with polysim (fill-or-kill legs, latency
between legs, automatic unwind) and held to resolution, so the result includes
fees, slippage, failed legs and outcomes the relation said were impossible.

Outcomes for our checker: **win** (took it, made money), **false alarm** (took
it, lost or made nothing), **miss** (blocked it, the naive trade made money),
**correct block** (blocked it, the naive trade lost money). The naive checker's
losses on correct blocks are the money our checker saved.

Honesty rules built in
----------------------
* ``min_edge`` is chosen on the earlier period only (``split_ts``), then used
  once on the later period. Without a split the report says it is in-sample.
* Bootstrap 95% intervals on every headline number; small samples say so.
* Candidates that are malformed or look manipulated are rejected and listed
  with the reason, never silently dropped.
* Every result is a paper result.
"""

from __future__ import annotations

import bisect
import json
import math
import random
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable, Sequence

from .basket import BasketLeg
from .engine import Backtester, BookSynthesizer
from .events import BookEvent, Event, PriceEvent, ResolutionEvent
from .exchange import market_fee_model
from .fees import FeeModel
from .models import EPS, Liquidity, Market, OrderBook
from .strategy import Strategy

RULE_VALUES = ("match", "mismatch", "unclear")
OUTCOMES = ("win", "false_alarm", "miss", "correct_block")


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Candidate:
    id: str
    ts: float
    token_ids: tuple[str, ...]
    floor: float
    rule_check: str = "unclear"
    relation: str = ""
    decision_id: str = ""
    note: str = ""


@dataclass(frozen=True)
class EvalConfig:
    size: float = 100.0  # baskets the naive checker tries to buy
    min_edge: float = 0.01  # USDC per share after fees our checker needs (tuned if split_ts is set)
    min_shares: float = 20.0  # depth our checker needs
    latency: float = 2.0  # seconds per order, applied between legs
    max_book_age: float = 120.0  # a book older than this at flag time is stale
    edge_grid: tuple[float, ...] = (0.0, 0.005, 0.01, 0.02, 0.03, 0.05)
    bootstrap: int = 2000
    seed: int = 0
    fee_model: FeeModel | None = None  # None = each market's own fee schedule


def parse_candidate(d: dict) -> Candidate:
    """Build a Candidate from JSON, or raise ValueError saying what is wrong."""
    if not isinstance(d, dict):
        raise ValueError("not a JSON object")
    missing = [k for k in ("id", "ts", "token_ids", "floor") if k not in d]
    if missing:
        raise ValueError(f"missing field(s): {', '.join(missing)}")
    tokens = d["token_ids"]
    if not isinstance(tokens, list) or not tokens or not all(isinstance(t, str) and t for t in tokens):
        raise ValueError("token_ids must be a non-empty list of strings")
    try:
        ts, floor = float(d["ts"]), float(d["floor"])
    except (TypeError, ValueError):
        raise ValueError("ts and floor must be numbers") from None
    if not (math.isfinite(ts) and math.isfinite(floor)):
        raise ValueError("ts and floor must be finite")
    rule = str(d.get("rule_check", "unclear")).lower()
    if rule not in RULE_VALUES:
        raise ValueError(f"rule_check must be one of {RULE_VALUES}, got {rule!r}")
    return Candidate(
        id=str(d["id"]),
        ts=ts,
        token_ids=tuple(tokens),
        floor=floor,
        rule_check=rule,
        relation=str(d.get("relation", "")),
        decision_id=str(d.get("decision_id", "")),
        note=str(d.get("note", "")),
    )


def parse_candidates(raw: Iterable) -> tuple[list[Candidate], list[dict]]:
    """Parse many candidates. Returns (candidates, rejected ones with reasons)."""
    good, bad = [], []
    for n, d in enumerate(raw, 1):
        try:
            good.append(parse_candidate(d))
        except ValueError as e:
            name = d.get("id") if isinstance(d, dict) and d.get("id") is not None else f"item {n}"
            bad.append({"id": str(name), "reason": str(e)})
    return good, bad


def load_candidates(path: str | Path) -> tuple[list[Candidate], list[dict]]:
    """Read a JSONL file. Returns (candidates, rejected lines with reasons)."""
    raw, bad = [], []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError as e:
                bad.append({"id": f"line {n}", "reason": f"not valid JSON: {e}"})
    good, more_bad = parse_candidates(raw)
    return good, bad + more_bad


# --------------------------------------------------------------------------- market data index


class MarketData:
    """Recorded events indexed by token, so 'the book at time t' is a quick lookup."""

    def __init__(self, events: Iterable[Event], markets: Iterable[Market], synthesizer: BookSynthesizer | None = None):
        self.markets = {m.market_id: m for m in markets}
        self.market_of = {t: m for m in self.markets.values() for t in m.token_ids}
        synth = synthesizer or BookSynthesizer()
        self.books: dict[str, list[BookEvent]] = {}
        self.synthesized: set[str] = set()
        self.resolutions: dict[str, ResolutionEvent] = {}
        for ev in events:
            if isinstance(ev, PriceEvent):
                m = self.market_of.get(ev.token_id)
                tick = m.tick_size if m else 0.01
                ev = BookEvent(ev.ts, synth.make(ev.token_id, ev.ts, ev.price, tick))
                self.synthesized.add(ev.token_id)
            if isinstance(ev, BookEvent):
                self.books.setdefault(ev.token_id, []).append(ev)
            elif isinstance(ev, ResolutionEvent):
                self.resolutions[ev.market_id] = ev
        for lst in self.books.values():
            lst.sort(key=lambda e: e.ts)
        self._ts = {t: [e.ts for e in lst] for t, lst in self.books.items()}

    def book_at(self, token_id: str, ts: float) -> OrderBook | None:
        times = self._ts.get(token_id)
        if not times:
            return None
        i = bisect.bisect_right(times, ts + EPS) - 1
        return self.books[token_id][i].book if i >= 0 else None

    def events_from(self, token_ids: Sequence[str], ts: float) -> list[Event]:
        """The latest book at or before `ts` for each token, every later book, and the resolutions."""
        out: list[tuple[float, int, Event]] = []
        for t in token_ids:
            times = self._ts.get(t, [])
            start = max(bisect.bisect_right(times, ts + EPS) - 1, 0)
            out += [(e.ts, 0, e) for e in self.books.get(t, [])[start:]]
        for mid in {self.market_of[t].market_id for t in token_ids if t in self.market_of}:
            if mid in self.resolutions:
                r = self.resolutions[mid]
                out.append((r.ts, 1, r))
        out.sort(key=lambda x: (x[0], x[1]))
        return [e for _, _, e in out]


def validate(c: Candidate, data: MarketData, cfg: EvalConfig, seen_ids: set[str]) -> str:
    """Why this candidate cannot be evaluated (malformed or suspicious), or '' if it is fine."""
    if c.id in seen_ids:
        return "duplicate candidate id"
    if len(set(c.token_ids)) != len(c.token_ids):
        return "the same token appears twice in one basket"
    unknown = [t for t in c.token_ids if t not in data.market_of]
    if unknown:
        return f"unknown token(s): {', '.join(unknown)}"
    if not (0 < c.floor <= len(c.token_ids) + EPS):
        return f"floor {c.floor:g} is impossible for {len(c.token_ids)} leg(s) (must be > 0 and <= number of legs)"
    for t in c.token_ids:
        r = data.resolutions.get(data.market_of[t].market_id)
        if r is not None and r.ts <= c.ts:
            return f"flagged after {data.market_of[t].market_id} had already resolved"
        book = data.book_at(t, c.ts)
        if book is None:
            return f"no recorded book for {t} at the flag time"
        if c.ts - book.ts > cfg.max_book_age:
            return f"book for {t} is {c.ts - book.ts:.0f}s old at the flag time (stale)"
        if book.best_bid is not None and book.best_ask is not None and book.best_bid >= book.best_ask - EPS:
            return f"crossed book for {t} (bid {book.best_bid} >= ask {book.best_ask})"
    return ""


# --------------------------------------------------------------------------- per-candidate stages


@dataclass
class CandidateResult:
    candidate: Candidate
    naive_edge: float = 0.0  # per share, floor - sum of mids
    top_edge: float = 0.0  # per share at the best asks, after fees
    depth_shares: float = 0.0  # baskets fillable while each extra one still profits
    depth_avg_edge: float = 0.0  # per share over those baskets, after fees
    stages: dict = field(default_factory=dict)
    taken: bool = False
    blocked_at: str = ""
    naive: dict = field(default_factory=dict)  # paper execution of the naive checker's trade
    ours: dict = field(default_factory=dict)  # paper execution of ours (if taken)
    outcome: str = ""  # win | false_alarm | miss | correct_block | open | not_flagged
    synthesized_books: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        d["candidate"] = asdict(self.candidate)
        return d


def _walk(books: list[OrderBook], fees: list[FeeModel], floor: float, max_shares: float):
    """Buy one share of every leg at a time, cheapest levels first, while the next one still profits."""
    idx = [0] * len(books)
    rem = [b.asks[0].size if b.asks else 0.0 for b in books]
    shares = profit = 0.0
    worst = [0.0] * len(books)
    while shares < max_shares - EPS and all(idx[k] < len(b.asks) for k, b in enumerate(books)):
        prices = [b.asks[idx[k]].price for k, b in enumerate(books)]
        unit = sum(p + f.per_share(p, Liquidity.TAKER) for p, f in zip(prices, fees))
        if floor - unit <= EPS:
            break
        take = min(min(rem), max_shares - shares)
        shares += take
        profit += take * (floor - unit)
        worst = [max(w, p) for w, p in zip(worst, prices)]
        for k, b in enumerate(books):
            rem[k] -= take
            if rem[k] <= EPS:
                idx[k] += 1
                rem[k] = b.asks[idx[k]].size if idx[k] < len(b.asks) else 0.0
    return shares, (profit / shares if shares > EPS else 0.0), worst


class _TakeBasket(Strategy):
    """Sends one basket as soon as every leg's book (as of the flag time) has been seen."""

    name = "evaluation"

    def __init__(self, legs: list[BasketLeg], decision_id: str):
        self.legs, self.decision_id = legs, decision_id
        self.seen: set[str] = set()
        self.basket = None

    def on_book(self, ctx, book):
        self.seen.add(book.token_id)
        if self.basket is None and all(leg.token_id in self.seen for leg in self.legs):
            self.basket = ctx.execute_basket(self.legs, decision_id=self.decision_id, tag="evaluation")


def paper_execute(c: Candidate, legs: list[BasketLeg], data: MarketData, cfg: EvalConfig) -> dict:
    """Run the basket through polysim from the flag time to resolution.

    ``pnl`` scores only what the arbitrage claimed to guarantee: the floor payout
    on the baskets held, minus everything paid (fees, slippage, unwinds). When an
    outcome pays more than the floor (e.g. both legs win), the extra is luck, not
    arbitrage, so it is reported as ``windfall`` and left out of ``pnl``. When the
    relation breaks and the payout is below the floor, the whole loss counts.
    ``pnl`` is None while the trade is still open.
    """
    markets = [data.market_of[t] for t in c.token_ids]
    markets = list({m.market_id: m for m in markets}.values())
    cash = sum(leg.size for leg in legs) * 2 + 10  # enough for any price <= $1 plus fees
    strat = _TakeBasket(legs, c.decision_id)
    bt = Backtester(data.events_from(c.token_ids, c.ts), markets, strat, initial_cash=cash,
                    fee_model=cfg.fee_model, latency=cfg.latency)
    res = bt.run()
    b = strat.basket
    open_shares = sum(p.qty for p in bt.portfolio.positions.values())
    status = b.status if b else "not sent"
    total = None if open_shares > EPS or bt.manual_settlements else bt.portfolio.cash - cash
    proceeds = sum(sum(r["payload"]["credited"].values()) for r in bt.audit.records("settlement"))
    held = legs[0].size if b is not None and b.status == "filled" else 0.0
    windfall = max(0.0, proceeds - c.floor * held) if total is not None else 0.0
    pnl = None if total is None else total - windfall
    return {
        "status": status,
        "size": legs[0].size if legs else 0.0,
        "cost": b.cost if b else 0.0,
        "leg_risk_cost": b.leg_risk_cost if b else 0.0,
        "fees": bt.portfolio.fees_paid,
        "pnl": pnl,
        "pnl_total": total,
        "windfall": windfall,
        "open": pnl is None,
        "reason": b.reason if b else "",
        "audit_ok": res.audit_ok,
    }


def assess(c: Candidate, data: MarketData, cfg: EvalConfig) -> CandidateResult:
    r = CandidateResult(c, synthesized_books=any(t in data.synthesized for t in c.token_ids))
    books = [data.book_at(t, c.ts) for t in c.token_ids]
    fees = [cfg.fee_model or market_fee_model(data.market_of[t]) for t in c.token_ids]

    mids = [b.mid for b in books]
    r.naive_edge = c.floor - sum(mids) if all(m is not None for m in mids) else -math.inf
    asks = [b.best_ask for b in books]
    if all(a is not None for a in asks):
        r.top_edge = c.floor - sum(a + f.per_share(a, Liquidity.TAKER) for a, f in zip(asks, fees))
    else:
        r.top_edge = -math.inf
    r.depth_shares, r.depth_avg_edge, worst = _walk(books, fees, c.floor, cfg.size)

    r.stages = {
        "naive": r.naive_edge > EPS,
        "fees": r.top_edge > EPS,
        "depth": r.depth_shares >= cfg.min_shares - EPS and r.depth_avg_edge >= cfg.min_edge - EPS,
        "rules": c.rule_check == "match",
    }
    if not r.stages["naive"]:
        r.outcome = "not_flagged"
        return r

    # The naive checker buys the full size at whatever the book offers.
    r.naive = paper_execute(c, [BasketLeg(t, cfg.size) for t in c.token_ids], data, cfg)
    # Our checker's trade, executed whether or not we take it, so blocks can be judged too.
    our_size = math.floor(r.depth_shares * 100) / 100
    if our_size > EPS:
        legs = [BasketLeg(t, our_size, w) for t, w in zip(c.token_ids, worst)]
        r.ours = paper_execute(c, legs, data, cfg)
    decide(r, cfg.min_edge, cfg.min_shares)
    return r


def decide(r: CandidateResult, min_edge: float, min_shares: float) -> None:
    """Apply our checker's thresholds and label the outcome. Cheap, so tuning can re-run it."""
    if not r.stages.get("naive"):
        r.outcome = "not_flagged"
        return
    r.stages["depth"] = r.depth_shares >= min_shares - EPS and r.depth_avg_edge >= min_edge - EPS
    r.blocked_at = next((s for s in ("fees", "depth", "rules") if not r.stages[s]), "")
    r.taken = not r.blocked_at
    if r.taken:
        pnl = r.ours.get("pnl")
        r.outcome = "open" if pnl is None else "win" if pnl > EPS else "false_alarm"
    else:
        pnl = r.naive.get("pnl")
        r.outcome = "open" if pnl is None else "miss" if pnl > EPS else "correct_block"


# --------------------------------------------------------------------------- statistics


def _stats(rs: Sequence[CandidateResult]) -> dict:
    flagged = [r for r in rs if r.outcome != "not_flagged"]
    closed = [r for r in flagged if r.outcome != "open"]
    taken = [r for r in closed if r.taken]
    count = {o: sum(r.outcome == o for r in closed) for o in OUTCOMES}
    naive_pnls = [r.naive["pnl"] for r in closed if r.naive.get("pnl") is not None]
    ours_pnls = [r.ours["pnl"] for r in taken]
    wins_and_fa = count["win"] + count["false_alarm"]
    return {
        "candidates": len(rs),
        "naive_flags": len(flagged),
        "still_open": len(flagged) - len(closed),
        "funnel": {
            "naive_flags": len(flagged),
            "after_fees": sum(r.stages["fees"] for r in flagged),
            "after_depth": sum(r.stages["fees"] and r.stages["depth"] for r in flagged),
            "after_rule_check": sum(r.taken for r in flagged),
            "profitable_at_resolution": count["win"],
        },
        "outcomes": count,
        "naive_total_pnl": sum(naive_pnls),
        "naive_trades": len(naive_pnls),
        "naive_hit_rate": (sum(p > EPS for p in naive_pnls) / len(naive_pnls)) if naive_pnls else None,
        "ours_total_pnl": sum(ours_pnls),
        "ours_trades": len(ours_pnls),
        "ours_precision": (count["win"] / wins_and_fa) if wins_and_fa else None,
        "loss_avoided": -sum(r.naive["pnl"] for r in closed if r.outcome == "correct_block"),
        "profit_missed": sum(r.naive["pnl"] for r in closed if r.outcome == "miss"),
        "naive_windfall": sum(r.naive.get("windfall", 0.0) for r in closed),
        "ours_windfall": sum(r.ours.get("windfall", 0.0) for r in taken),
        "naive_leg_risk_cost": sum(r.naive.get("leg_risk_cost", 0.0) for r in closed),
        "ours_leg_risk_cost": sum(r.ours.get("leg_risk_cost", 0.0) for r in taken),
    }


def bootstrap(rs: Sequence[CandidateResult], n: int, seed: int) -> dict:
    """95% percentile intervals, resampling candidates with replacement."""
    closed = [r for r in rs if r.outcome not in ("not_flagged", "open")]
    if len(closed) < 2 or n <= 0:
        return {"n": len(closed), "note": "too few resolved candidates for an interval"}
    rng = random.Random(seed)
    diff, prec, hit = [], [], []
    for _ in range(n):
        s = _stats([closed[rng.randrange(len(closed))] for _ in closed])
        diff.append(s["ours_total_pnl"] - s["naive_total_pnl"])
        if s["ours_precision"] is not None:
            prec.append(s["ours_precision"])
        if s["naive_hit_rate"] is not None:
            hit.append(s["naive_hit_rate"])

    def ci(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return [xs[int(0.025 * (len(xs) - 1))], xs[int(0.975 * (len(xs) - 1))]]

    out = {"n": len(closed), "resamples": n, "ours_minus_naive_pnl": ci(diff),
           "ours_precision": ci(prec), "naive_hit_rate": ci(hit)}
    if len(closed) < 30:
        out["note"] = f"only {len(closed)} resolved candidates: intervals are wide and approximate"
    return out


# --------------------------------------------------------------------------- the whole evaluation


@dataclass
class EvalReport:
    label: str
    config: dict
    period: str  # "held-out test" | "in-sample (no split)"
    chosen_min_edge: float
    tuning: list[dict]
    stats: dict
    intervals: dict
    results: list[CandidateResult]
    tune_results: list[CandidateResult]
    rejected_inputs: list[dict]

    def as_dict(self) -> dict:
        return {
            "paper_trading": True,
            "label": self.label,
            "period": self.period,
            "config": self.config,
            "chosen_min_edge": self.chosen_min_edge,
            "tuning": self.tuning,
            "stats": self.stats,
            "intervals": self.intervals,
            "rejected_inputs": self.rejected_inputs,
            "results": [r.as_dict() for r in self.results],
            "tune_results": [r.as_dict() for r in self.tune_results],
        }


def evaluate(
    candidates: Sequence[Candidate],
    data: MarketData,
    cfg: EvalConfig = EvalConfig(),
    *,
    split_ts: float | None = None,
    label: str = "recorded Polymarket data",
    rejected_inputs: Sequence[dict] = (),
) -> EvalReport:
    rejected = list(rejected_inputs)
    seen: set[str] = set()
    valid: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: c.ts):
        why = validate(c, data, cfg, seen)
        seen.add(c.id)
        if why:
            rejected.append({"id": c.id, "reason": why})
        else:
            valid.append(c)

    results = [assess(c, data, cfg) for c in valid]

    tuning: list[dict] = []
    chosen = cfg.min_edge
    if split_ts is not None:
        tune = [r for r in results if r.candidate.ts < split_ts]
        test = [r for r in results if r.candidate.ts >= split_ts]
        best = None
        for e in cfg.edge_grid:
            for r in tune:
                decide(r, e, cfg.min_shares)
            s = _stats(tune)
            tuning.append({"min_edge": e, "ours_total_pnl": s["ours_total_pnl"], "ours_trades": s["ours_trades"]})
            # highest P&L; on a tie keep the stricter threshold (the cautious choice)
            if best is None or s["ours_total_pnl"] >= best[1] - EPS:
                best = (e, s["ours_total_pnl"])
        chosen = best[0] if best else cfg.min_edge
        for r in tune:
            decide(r, chosen, cfg.min_shares)
        period = "held-out test"
    else:
        tune, test = [], results
        period = "in-sample (no split: thresholds were not tuned on separate data)"
    for r in test:
        decide(r, chosen, cfg.min_shares)

    cfg_dict = {k: v for k, v in asdict(cfg).items() if k != "fee_model"}
    cfg_dict["fees"] = "override" if cfg.fee_model else "each market's own schedule"
    return EvalReport(
        label=label,
        config=cfg_dict,
        period=period,
        chosen_min_edge=chosen,
        tuning=tuning,
        stats=_stats(test),
        intervals=bootstrap(test, cfg.bootstrap, cfg.seed),
        results=test,
        tune_results=tune,
        rejected_inputs=rejected,
    )


# --------------------------------------------------------------------------- report text


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _ci(pair, fmt) -> str:
    return "n/a" if not pair else f"[{fmt(pair[0])}, {fmt(pair[1])}]"


def format_report(rep: EvalReport) -> str:
    s, ci = rep.stats, rep.intervals
    f = s["funnel"]
    out = [
        f"PAPER EVALUATION on {rep.label}: simulated trades, not real-world performance",
        f"period: {rep.period}",
    ]
    if rep.tuning:
        grid = ", ".join(f"{t['min_edge']:g}->{t['ours_total_pnl']:+.2f}" for t in rep.tuning)
        out.append(f"min_edge chosen on the earlier period: {rep.chosen_min_edge:g}  (grid: {grid})")
    else:
        out.append(f"min_edge: {rep.chosen_min_edge:g} (not tuned)")
    out += [
        "",
        "Funnel (each step keeps the candidates that pass every step so far)",
        f"  naive checker flags        {f['naive_flags']:>5}",
        f"  still positive after fees  {f['after_fees']:>5}",
        f"  enough depth               {f['after_depth']:>5}",
        f"  rules match (we take it)   {f['after_rule_check']:>5}",
        f"  made money at resolution   {f['profitable_at_resolution']:>5}",
        f"  (not yet resolved: {s['still_open']}, not flagged by the naive checker: {s['candidates'] - s['naive_flags']})",
        "",
        "Results, paper-executed with fees, depth, latency and unwinds, held to resolution.",
        "P&L counts only the guaranteed part (floor x baskets - cost); extra payouts from lucky outcomes are listed apart.",
        f"  naive checker   {s['naive_trades']:>4} trades  P&L {_usd(s['naive_total_pnl']):>9} USDC"
        f"  hit rate {_pct(s['naive_hit_rate'])} 95% CI {_ci(ci.get('naive_hit_rate'), _pct)}",
        f"  our checker     {s['ours_trades']:>4} trades  P&L {_usd(s['ours_total_pnl']):>9} USDC"
        f"  precision {_pct(s['ours_precision'])} 95% CI {_ci(ci.get('ours_precision'), _pct)}",
        f"  difference (ours - naive)          {_usd(s['ours_total_pnl'] - s['naive_total_pnl']):>9} USDC"
        f"  95% CI {_ci(ci.get('ours_minus_naive_pnl'), _usd)}",
        f"  loss avoided by blocking           {s['loss_avoided']:>9.2f} USDC",
        f"  profit missed by blocking          {s['profit_missed']:>9.2f} USDC",
        f"  leg-risk cost: naive {s['naive_leg_risk_cost']:.2f}, ours {s['ours_leg_risk_cost']:.2f} USDC",
        f"  lucky extra payouts (not counted): naive {s['naive_windfall']:.2f}, ours {s['ours_windfall']:.2f} USDC",
    ]
    if ci.get("note"):
        out.append(f"  note: {ci['note']}")
    o = s["outcomes"]
    out += ["", f"Outcomes: {o['win']} win, {o['false_alarm']} false alarm, {o['miss']} miss, {o['correct_block']} correct block"]
    bad = [r for r in rep.results if r.outcome in ("false_alarm", "miss")]
    if bad:
        out.append("False alarms and misses (shown as plainly as the wins):")
        for r in bad:
            pnl = r.ours.get("pnl") if r.outcome == "false_alarm" else r.naive.get("pnl")
            why = f"blocked at {r.blocked_at}" if r.outcome == "miss" else f"taken, {r.ours.get('status')}"
            out.append(f"  {r.outcome:<12} {r.candidate.id:<14} P&L {_usd(pnl):>8}  {why}  {r.candidate.note}")
    blocks = [r for r in rep.results if r.outcome == "correct_block"]
    if blocks:
        out.append("Correct blocks (what the naive checker lost):")
        for r in blocks:
            out.append(f"  {r.candidate.id:<14} naive P&L {_usd(r.naive.get('pnl')):>8}  blocked at {r.blocked_at}  {r.candidate.note}")
    if rep.rejected_inputs:
        out += ["", f"Inputs rejected ({len(rep.rejected_inputs)}), never evaluated:"]
        out += [f"  {x['id']:<14} {x['reason']}" for x in rep.rejected_inputs]
    if any(r.synthesized_books for r in rep.results):
        out.append("\nwarning: some books were built from prices only, so depth results are approximate")
    return "\n".join(out)
