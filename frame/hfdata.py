"""Historical Polymarket trades (the SII-WANGZJ/Polymarket_data dataset on Hugging Face) as test data, by SAMPLING.

The dataset is ~1.27 billion trades (Nov 2022 - Oct 2026), trades only (no order books). Layout, measured:
rows are in DAY order, and within a day they are grouped BY MARKET (each market's whole day together); markets of
one event have neighbouring ids, so they sit next to each other. We never scan it. The sampling frame is
DAY x CHUNK: find a day by binary search on row number (~15 cheap probes, cached in data/hfdata/index.json), then
read a few random contiguous chunks (~150k rows) of that day straight from Hugging Face (HTTP range requests). Each
chunk holds the complete day of a handful of sibling markets, which is what arbitrage between them needs.

    python -m frame.hfdata scout                          # score the curated days, 3 chunks each
    python -m frame.hfdata scout --random 4 --chunks 4    # ...plus 4 random days, 4 chunks per day
    python -m frame.hfdata export <find # from scout.json>  # -> recordings/hf-..., trade-implied books
    python -m frame.testdata study recordings/hf-...       # then the usual replay through the core (no LLM)

Honest limits (said again in every export's README):
  * no order books in this dataset. Books are TRADE-IMPLIED: in each minute, taker buys of the YES token are taken
    as asks at that price and size, taker sells as bids. That is liquidity that really traded, not the resting
    book, so depth is understated and a gap may only have existed for the instant of the trade;
  * metadata (questions, rules, end dates, neg-risk) comes from Polymarket's Gamma API for the sampled events only.
Requires `duckdb` (requirements-data.txt).
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data/resolve/main/"
QUANT = BASE + "quant.parquet"
CACHE = ROOT / "data" / "hfdata"
INDEX = CACHE / "index.json"

# The sampling frame: DAYS known for volatility, where arbitrage is most likely. Coverage of this dataset is very
# uneven: ~2.5% of its rows are before 2025 (the 2024 election night is nearly absent), ~85% are Jan-Sep 2026.
FRAME = [
    ("Crypto crash (10 Oct 2025 liquidations)", "2025-10-10"),
    ("Super Bowl LX (8 Feb 2026, US time)", "2026-02-09"),
    ("Fed decision, Mar 2026 FOMC", "2026-03-18"),
    ("Premier League final day", "2026-05-24"),
    ("World Cup final", "2026-07-19"),
]
RANDOM_RANGE = ("2025-10-01T00:00:00Z", "2026-09-30T00:00:00Z")   # where the dataset is dense
CHUNK_ROWS = 150_000
FRESH_S = 900          # the scout only adds up prices traded within 15 minutes of each other


def _con():
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class Index:
    """Row number <-> time for the remote file, learned by probing and cached."""

    def __init__(self, con):
        self.con = con
        CACHE.mkdir(parents=True, exist_ok=True)
        self.probes: dict[int, float] = {}
        self.n = None
        if INDEX.exists():
            d = json.loads(INDEX.read_text())
            self.n, self.probes = d["n"], {int(k): v for k, v in d["probes"].items()}
        if self.n is None:
            self.n = self.con.execute(f"SELECT sum(num_rows) FROM parquet_file_metadata('{QUANT}')").fetchone()[0]

    def save(self):
        INDEX.write_text(json.dumps({"n": self.n, "probes": self.probes}))

    def ts_at(self, row: int) -> float:
        if row not in self.probes:
            self.probes[row] = float(self.con.execute(
                f"SELECT timestamp FROM read_parquet('{QUANT}', file_row_number=true) WHERE file_row_number = {row}"
            ).fetchone()[0])
        return self.probes[row]

    def row_at(self, ts: float) -> int:
        """First row with timestamp >= ts (binary search, starting from the cached probes)."""
        lo, hi = 0, self.n - 1
        for r, t in self.probes.items():               # tighten the bracket with what we already know
            if t < ts and r > lo:
                lo = r
            if t >= ts and r < hi:
                hi = r
        while hi - lo > 50_000:                         # ~one row group: close enough, the query filters exactly
            mid = (lo + hi) // 2
            if self.ts_at(mid) < ts:
                lo = mid
            else:
                hi = mid
        self.save()
        return lo


def day_rows(idx: Index, day: str) -> tuple[int, int]:
    """Row range of one UTC day (to within ~one row group at each end)."""
    t0 = _ts(day + "T00:00:00Z")
    return idx.row_at(t0), idx.row_at(t0 + 86400)


def chunk(con, r0: int, n: int = CHUNK_ROWS, event_ids: list[str] | None = None) -> list[tuple]:
    """(timestamp, market_id, event_id, price, token_amount, side) for rows r0..r0+n, time-ordered."""
    ev = f"AND event_id IN ({','.join(repr(str(e)) for e in event_ids)})" if event_ids else ""
    return con.execute(f"""SELECT timestamp, market_id, event_id, price, token_amount, side
                           FROM read_parquet('{QUANT}', file_row_number=true)
                           WHERE file_row_number BETWEEN {r0} AND {r0 + n} {ev}
                           ORDER BY timestamp""").fetchall()


# ------------------------------------------------------------------ metadata (Polymarket Gamma, sampled events only)

def event_meta(event_ids: list[str]) -> dict[str, dict]:
    from frame.adapter import _import_core
    _import_core()
    from arbcheck import polymarket
    from arbcheck.compliance import ReadOnlyClient
    client, out = ReadOnlyClient(), {}
    for eid in event_ids:
        try:
            ev = client.get(f"{polymarket.GAMMA}/events/{eid}")
            if isinstance(ev, dict) and ev.get("markets"):
                out[str(eid)] = ev
        except Exception as e:      # an event that can't be read is skipped, never guessed
            print(f"   (event {eid}: {e})")
    return out


# ------------------------------------------------------------------ scouting: a cheap screen per chunk

def _ladder_order(ev: dict) -> list[tuple[str, str]]:
    """(must-be-cheaper market id, must-be-dearer market id) pairs inside one event, by code:
    'by <date>' ladders (same question stem, earlier end <= later end) and reach/dip price thresholds."""
    import re
    from frame.windows import _T
    ms = ev.get("markets") or []
    pairs = []
    stem = lambda q: re.sub(r"\b(by|before|on|in)\b.*$", "", (q or "").lower()).strip()
    by_stem: dict[str, list] = defaultdict(list)
    th: dict[tuple, list] = defaultdict(list)
    for m in ms:
        q = m.get("question") or ""
        t = _T.match(q.strip())
        if t:
            x = float(t["x"].replace(",", "")) * {"k": 1e3, "m": 1e6}.get((t["k"] or "").lower(), 1)
            up = t["dir"].lower() in ("reach", "hit (high)")
            th[(t["asset"].lower(), up, t["rest"].strip().lower())].append((x, str(m.get("id"))))
        elif re.search(r"\bby\b", q.lower()) and m.get("endDate"):
            by_stem[stem(q)].append((m["endDate"], str(m.get("id"))))
    for group in by_stem.values():
        group.sort()
        pairs += [(a[1], b[1]) for a, b in zip(group, group[1:])]          # earlier date cheaper than later
    for (asset, up, _), group in th.items():
        group.sort()                                                        # by threshold, low to high
        if up:
            pairs += [(b[1], a[1]) for a, b in zip(group, group[1:])]      # reach higher is cheaper
        else:
            pairs += [(a[1], b[1]) for a, b in zip(group, group[1:])]      # dip to lower is cheaper
    return pairs


def scout_chunk(con, label: str, day: str, r0: int, top_events: int = 6) -> dict:
    t0 = time.time()
    rows = chunk(con, r0)
    count: dict[str, int] = defaultdict(int)
    mkts: dict[str, set] = defaultdict(set)
    for ts, mid, eid, *_ in rows:
        if eid:
            count[eid] += 1
            mkts[eid].add(mid)
    linked = [e for e in sorted(count, key=count.get, reverse=True) if len(mkts[e]) >= 2][:top_events]
    meta = event_meta(linked)
    finds = []
    for eid in linked:
        ev = meta.get(eid)
        if not ev:
            continue
        outcomes = {str(m.get("id")) for m in ev.get("markets") or []}
        order = _ladder_order(ev)
        seen: dict[str, tuple[float, float]] = {}          # market -> (last price, when)
        best = (0.0, None, "")
        for ts, mid, e, price, amt, side in rows:          # replay this event's trades in time order
            if e != eid:
                continue
            seen[mid] = (price, ts)
            # only prices traded within FRESH_S of now count: an hours-old last trade is not a price you could get
            last = {m: p for m, (p, t) in seen.items() if ts - t <= FRESH_S}
            if ev.get("negRisk"):
                s = sum(p for m, p in last.items() if m in outcomes)
                full = outcomes <= set(last)
                gap = s - 1 if s > 1 else ((1 - s) if full else 0.0)
                if gap > best[0]:
                    best = (gap, ts, f"exclusive outcomes priced {s:.3f} in total ({len(last)}/{len(outcomes)} "
                                     f"traded within {FRESH_S // 60} min)")
            for cheap, dear in order:
                if cheap in last and dear in last and last[cheap] - last[dear] > best[0]:
                    best = (last[cheap] - last[dear], ts, "ladder inversion: the harder/earlier rung traded above the easier/later one")
        finds.append({"event_id": eid, "title": ev.get("title"), "slug": ev.get("slug"), "neg_risk": bool(ev.get("negRisk")),
                      "markets_in_chunk": len(mkts[eid]), "outcomes": len(outcomes), "ladder_pairs": len(order),
                      "trades": count[eid], "signal": round(best[0], 4),
                      "when": _iso(best[1]) if best[1] else None, "why": best[2]})
    return {"label": label, "day": day, "r0": r0, "rows": len(rows), "seconds": round(time.time() - t0, 1),
            "finds": finds, "signal": max((f["signal"] for f in finds), default=0.0)}


def scout(n_random: int = 0, chunks_per_day: int = 3, seed: int = 7, say=print) -> list[dict]:
    con = _con()
    idx = Index(con)
    rnd = random.Random(seed)
    days = list(FRAME)
    lo, hi = _ts(RANDOM_RANGE[0]), _ts(RANDOM_RANGE[1])
    days += [("random day", datetime.fromtimestamp(rnd.uniform(lo, hi), timezone.utc).strftime("%Y-%m-%d"))
             for _ in range(n_random)]
    out = []
    for label, day in days:
        d0, d1 = day_rows(idx, day)
        if d1 - d0 < chunks_per_day * CHUNK_ROWS:      # day edges are fuzzy (histories run across days): widen
            d0, d1 = max(0, d0 - 2_000_000), d1 + 2_000_000
        say(f"{label} · around {day} · sampling {chunks_per_day} chunks of {CHUNK_ROWS:,} rows")
        for _ in range(chunks_per_day):
            r0 = rnd.randint(d0, max(d0, d1 - CHUNK_ROWS))
            c = scout_chunk(con, label, day, r0)
            out.append(c)
            say(f"   rows {r0:,}: {c['rows']:,} trades in {c['seconds']}s, best signal {c['signal']:.3f}")
            for f in sorted(c["finds"], key=lambda f: -f["signal"])[:3]:
                say(f"     {(f['title'] or '')[:58]:<58} {f['signal']:+.3f} at {f['when']}  {f['why']}")
    best_per_event: dict[str, dict] = {}
    for c in out:
        for f in c["finds"]:
            g = {**f, "label": c["label"], "day": c["day"], "r0": c["r0"]}
            if f["event_id"] not in best_per_event or f["signal"] > best_per_event[f["event_id"]]["signal"]:
                best_per_event[f["event_id"]] = g
    finds = sorted(best_per_event.values(), key=lambda f: -f["signal"])
    for i, f in enumerate(finds):
        f["find"] = i
    (CACHE / "scout.json").write_text(json.dumps(finds, indent=1), encoding="utf-8")
    say(f"\nTop finds (python -m frame.hfdata export <find #>):")
    for f in finds[:10]:
        say(f"  #{f['find']:<3} {f['signal']:+.3f}  {f['day']}  {(f['title'] or '')[:55]:<55} {f['why'][:60]}")
    say(f"All {len(finds)} saved to {CACHE / 'scout.json'}")
    return finds


# ------------------------------------------------------------------ export a window as a polysim-style recording

def export(find: int, minutes: float = 60, step_s: int = 60, book_window_s: int = 300,
           out_dir: Path | None = None, say=print) -> Path:
    """Write one scout find (its event, `minutes` centred on its strongest signal) as a recording that
    `python -m frame.testdata study` and the test-data app can read. Books are TRADE-IMPLIED (module docstring)."""
    finds = json.loads((CACHE / "scout.json").read_text(encoding="utf-8"))
    pick = next(x for x in finds if x["find"] == find)
    con = _con()
    event_ids = [pick["event_id"]]
    # the event's markets sit around the sampled chunk; read a margin either side, only this event's rows
    rows = chunk(con, max(0, pick["r0"] - 200_000), CHUNK_ROWS + 400_000, event_ids)
    centre = _ts(pick["when"].replace(" ", "T") + ":00Z") if pick.get("when") else (rows[len(rows) // 2][0] if rows else 0)
    start = centre - minutes * 30
    rows = [r for r in rows if start - book_window_s <= r[0] < start + minutes * 60]
    meta = event_meta(event_ids)
    name = f"hf-{datetime.fromtimestamp(start, timezone.utc):%Y%m%d-%H%M}Z-{(pick.get('slug') or pick['event_id'])[:40]}"
    out = out_dir or ROOT / "recordings" / name
    (out / "events").mkdir(parents=True, exist_ok=True)
    markets, tok_of = [], {}
    for eid, ev in meta.items():
        (out / "events" / f"{ev.get('slug') or eid}.json").write_text(json.dumps(ev), encoding="utf-8")
        for m in ev.get("markets") or []:
            try:
                toks = json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds")
                end = _ts(m["endDate"])
            except (KeyError, TypeError, ValueError):
                continue
            # Polymarket sets a game's endDate to KICKOFF, but it trades in play: use when it really closed, or
            # kickoff + 2 h 15 min for games (as frame/sports.py does live).
            for k in ("closedTime", "umaEndDate"):
                try:
                    if m.get(k):
                        end = max(end, _ts(str(m[k]).replace(" ", "T").replace("+00", "+00:00")))
                except ValueError:
                    pass
            kick = m.get("gameStartTime") or ev.get("startTime")
            if kick and (m.get("sportsMarketType") or ev.get("gameId")):
                try:
                    end = max(end, _ts(str(kick).replace(" ", "T").replace("+00", "+00:00")) + 8100)
                except ValueError:
                    pass
            mid = str(m.get("id"))
            tok_of[mid] = toks[0]
            markets.append({"market_id": m.get("conditionId") or mid, "gamma_id": mid, "question": m.get("question"),
                            "slug": m.get("slug"), "tokens": [[toks[0], "Yes"], [toks[1], "No"]], "end_ts": end,
                            "category": "", "event_slug": ev.get("slug")})
    (out / "markets.json").write_text(json.dumps(markets, indent=1), encoding="utf-8")
    # trade-implied books, one round per step: the trailing `book_window_s` of trades of each YES token
    lines, wanted = [], set(tok_of)
    per = [r for r in rows if str(r[1]) in wanted]
    t = start
    while t < start + minutes * 60:
        for mid, tok in tok_of.items():
            recent = [r for r in per if str(r[1]) == mid and t - book_window_s <= r[0] < t]
            if not recent:
                continue
            asks, bids = defaultdict(float), defaultdict(float)
            for _, _, _, price, amt, side in recent:
                (asks if str(side).lower() in ("buy", "b") else bids)[round(price, 3)] += abs(amt)
            bid_l = sorted(([p, s] for p, s in bids.items() if 0 < p < 1), reverse=True)
            ask_l = sorted([p, s] for p, s in asks.items() if 0 < p < 1)
            if bid_l and ask_l and bid_l[0][0] >= ask_l[0][0]:      # crossed only because trades are not a book:
                bid_l = [l for l in bid_l if l[0] < ask_l[0][0]]     # keep the honest side, never invent a level
            if ask_l:
                lines.append(json.dumps({"type": "book", "ts": t, "token_id": tok, "bids": bid_l, "asks": ask_l}))
        t += step_s
    with gzip.open(out / f"books-{name}.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    (out / "README.txt").write_text(
        f"Exported from the SII-WANGZJ/Polymarket_data dataset (Hugging Face), {_iso(start)} UTC + {minutes:.0f} min.\n"
        f"Events: {', '.join((ev.get('title') or eid) for eid, ev in meta.items())}\n"
        f"Why it was picked (scout, on trade prices): {pick.get('why')} (signal {pick.get('signal')})\n\n"
        "BOOKS ARE TRADE-IMPLIED: each minute, the trades of the previous few minutes of each YES token are turned into\n"
        "levels (taker buys -> asks, taker sells -> bids). That is liquidity that really traded, not the resting\n"
        "order book: depth is understated and a gap may have existed only at the instant of a trade.\n"
        "Respect Polymarket's terms: keep this folder private (recordings/ is gitignored).\n", encoding="utf-8")
    say(f"Exported {len(markets)} markets, {len(lines)} trade-implied books to {out}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="frame.hfdata")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scout", help="score a sampling frame of days x chunks")
    s.add_argument("--random", type=int, default=0, help="also sample this many random days (baseline)")
    s.add_argument("--chunks", type=int, default=3, help="chunks of ~150k rows per day")
    s.add_argument("--seed", type=int, default=7)
    e = sub.add_parser("export", help="write one scout find as a recording")
    e.add_argument("find", type=int, help="the find # printed by scout (see data/hfdata/scout.json)")
    e.add_argument("--minutes", type=float, default=60, help="window length, centred on the strongest signal")
    a = ap.parse_args(argv)
    if a.cmd == "scout":
        scout(a.random, a.chunks, a.seed)
    else:
        out = export(a.find, a.minutes)
        print(f"Next: python -m frame.testdata study {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
