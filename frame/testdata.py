"""Test-data mode: run the checker on DOWNLOADED order books instead of live venues (e.g. for judging, or to
replay a moment you recorded).

    python -m frame.testdata convert     # files in data/testdata/raw  ->  data/testdata/normalized/markets.json
    python -m frame.testdata core        # the core on the converted data: NO Gemini at all
    python -m frame.testdata sample      # write an invented example file into data/testdata/raw
    run.bat  (menu 5 / 6 / 7)            # convert / core only / the app on the converted data

How a file you downloaded gets in:
  1. A file already in our format (see FORMAT below) is read by code directly.
  2. Any other JSON / JSONL / CSV: Gemini reads a SAMPLE of it and returns a field MAPPING only: which field
     is the market id, the question, the close time, the bids and asks, what the price scale is. It never
     writes a price. Code then copies every number through that mapping and validates it
     (price within 0-1, size > 0, best bid below best ask). The mapping is cached per file, so a second
     conversion of the same file needs no Gemini.
  3. `core` builds the core's own Market objects from the converted data and prices what can be priced
     WITHOUT an LLM: venue-declared outcome sets, Yes + No of each market, date/threshold ladders (the core's
     own extractor) and exact-score => result links inside a game. Report: data/testdata/report.md.

FORMAT (data/testdata/normalized/markets.json):
  {"as_of": ISO time of the snapshot, "source": "...", "markets": [
     {"venue": "polymarket", "id": "...", "title": "...", "rules_text": "...", "close_time_utc": "...",
      "category": "sports", "event_id": "...", "exclusive": false,
      "yes_bids": [[price, size], ...], "yes_asks": [[price, size], ...],
      "start_time_utc": null, "game_id": null, "sport_type": null}]}
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frame.config import DATA, Config, get_config  # noqa: E402

TD = DATA.parent / "testdata"
RAW, NORM, MAPS = TD / "raw", TD / "normalized", TD / "mappings"


# ------------------------------------------------------------------ the mapping Gemini proposes (no numbers)

class FieldMap(BaseModel):
    id: str = Field(description="path to the market id")
    title: str = Field("", description="path to the question/title text; empty if none")
    rules_text: str = Field("", description="path to the resolution rules/description text; empty if none")
    close_time_utc: str = Field("", description="path to the close/end/resolution time; empty if none")
    category: str = Field("", description="path to a category/tag; empty if none")
    event_id: str = Field("", description="path to the event/group id; empty if none")
    venue: str = Field("", description="path to the venue name; empty if the whole file is one venue")
    start_time_utc: str = Field("", description="path to a game kickoff time; empty if none")
    game_id: str = Field("", description="path to a game id; empty if none")
    sport_type: str = Field("", description="path to a sports market type (moneyline, exact score…); empty if none")
    exclusive: str = Field("", description="path to a yes/no flag saying the market belongs to a set of mutually "
                                           "exclusive outcomes of its event (e.g. home/draw/away); empty if none")


class Mapping(BaseModel):
    file_format: Literal["json", "jsonl", "csv"]
    records_path: str = Field("", description="dot path from the JSON root to the list of records; '' if the root "
                                              "is the list, or for jsonl/csv")
    layout: Literal["nested", "best", "rows"] = Field(description=(
        "nested: each record holds whole bid and ask lists; best: each record has only best bid/ask and their "
        "sizes; rows: each record is ONE price level (market id, side, price, size)"))
    fields: FieldMap
    bids_path: str = Field("", description="nested: path to the bids list")
    asks_path: str = Field("", description="nested: path to the asks list")
    level_price: str = Field("0", description="nested: key or index of the price inside one level ('0' for [p, s])")
    level_size: str = Field("1", description="nested: key or index of the size inside one level ('1' for [p, s])")
    best_bid: str = Field("", description="best: path to the best bid price")
    best_ask: str = Field("", description="best: path to the best ask price")
    bid_size: str = Field("", description="best: path to the size at the best bid ('' if none)")
    ask_size: str = Field("", description="best: path to the size at the best ask ('' if none)")
    row_side: str = Field("", description="rows: path to the side column")
    row_bid_value: str = Field("bid", description="rows: the value of the side column that means a bid")
    row_price: str = Field("", description="rows: path to the price")
    row_size: str = Field("", description="rows: path to the size")
    price_scale: Literal["dollars", "cents", "percent"] = Field(description="dollars: 0-1; cents: 0-100; percent: 0-100")
    book_is_for: Literal["yes", "no"] = Field("yes", description="whose book the prices are: the YES or the NO token")
    venue_constant: str = Field("", description="venue name if the whole file is from one venue (polymarket/kalshi/…)")
    as_of_path: str = Field("", description="path to the snapshot time if the file has one")
    notes: str = ""


SYSTEM = """You map a downloaded prediction-market data file to a fixed schema. You receive the file name and a
SAMPLE of its contents. Return ONLY a field mapping: which path/column holds each field. Never write, compute or
estimate a price, size or date yourself; code will copy them using your mapping. Paths are dot-separated keys
(e.g. "market.question"); for CSV use the column name. Use "" for anything the file does not have. If the prices
look like 0-100 numbers, say cents or percent. If bids/asks are lists of [price, size] pairs use level_price "0"
and level_size "1"; if they are objects use their keys. Explain any doubt in notes."""


def _get(obj, path: str):
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _num(x) -> float | None:
    try:
        return float(str(x).strip().rstrip("¢%"))
    except (TypeError, ValueError):
        return None


def _records(text: str, m: Mapping) -> list[dict]:
    if m.file_format == "csv":
        return list(csv.DictReader(io.StringIO(text)))
    if m.file_format == "jsonl":
        return [json.loads(l) for l in text.splitlines() if l.strip()]
    root = json.loads(text)
    recs = _get(root, m.records_path) if m.records_path else root
    return recs if isinstance(recs, list) else []


def _scale(p: float | None, m: Mapping) -> float | None:
    if p is None:
        return None
    return p / 100 if m.price_scale in ("cents", "percent") else p


def _level(level, key: str):
    if isinstance(level, (list, tuple)):
        return level[int(key)] if key.isdigit() and int(key) < len(level) else None
    return level.get(key) if isinstance(level, dict) else None


def apply_mapping(text: str, m: Mapping, venue_default: str = "manual") -> tuple[list[dict], list[str]]:
    """Code, not the LLM, copies every value through the mapping. Returns (markets, problems)."""
    problems: list[str] = []
    recs = _records(text, m)
    by_id: dict[str, dict] = {}
    for r in recs:
        mid = _get(r, m.fields.id)
        if mid in (None, ""):
            problems.append("a record without an id was skipped")
            continue
        mid = str(mid)
        d = by_id.setdefault(mid, {
            "venue": str(_get(r, m.fields.venue) or m.venue_constant or venue_default).lower(), "id": mid,
            "title": str(_get(r, m.fields.title) or mid), "rules_text": str(_get(r, m.fields.rules_text) or ""),
            "close_time_utc": _get(r, m.fields.close_time_utc), "category": _get(r, m.fields.category),
            "event_id": _get(r, m.fields.event_id),
            "exclusive": str(_get(r, m.fields.exclusive) or "").strip().lower() in ("1", "true", "y", "yes"),
            "start_time_utc": _get(r, m.fields.start_time_utc), "game_id": _get(r, m.fields.game_id),
            "sport_type": _get(r, m.fields.sport_type), "yes_bids": [], "yes_asks": []})
        bids, asks = [], []
        if m.layout == "nested":
            for lv in _get(r, m.bids_path) or []:
                bids.append((_scale(_num(_level(lv, m.level_price)), m), _num(_level(lv, m.level_size))))
            for lv in _get(r, m.asks_path) or []:
                asks.append((_scale(_num(_level(lv, m.level_price)), m), _num(_level(lv, m.level_size))))
        elif m.layout == "best":
            bids.append((_scale(_num(_get(r, m.best_bid)), m), _num(_get(r, m.bid_size)) or 100.0))
            asks.append((_scale(_num(_get(r, m.best_ask)), m), _num(_get(r, m.ask_size)) or 100.0))
        else:
            side = str(_get(r, m.row_side) or "").strip().lower()
            lv = (_scale(_num(_get(r, m.row_price)), m), _num(_get(r, m.row_size)))
            (bids if side == m.row_bid_value.strip().lower() else asks).append(lv)
        if m.book_is_for == "no":     # a NO bid at p is a YES ask at 1-p, and a NO ask at p a YES bid at 1-p
            bids, asks = [(None if p is None else round(1 - p, 4), s) for p, s in asks], \
                         [(None if p is None else round(1 - p, 4), s) for p, s in bids]
        d["yes_bids"] += [[p, s] for p, s in bids if p is not None and s]
        d["yes_asks"] += [[p, s] for p, s in asks if p is not None and s]
    out = []
    for d in by_id.values():
        d["yes_bids"].sort(key=lambda l: -l[0])
        d["yes_asks"].sort(key=lambda l: l[0])
        bad = [p for p, _ in d["yes_bids"] + d["yes_asks"] if not 0 < p < 1]
        if bad:
            problems.append(f"{d['id']}: prices outside 0-1 after scaling ({bad[:3]}); skipped")
            continue
        if not d["yes_asks"]:
            problems.append(f"{d['id']}: no asks; skipped")
            continue
        if d["yes_bids"] and d["yes_bids"][0][0] >= d["yes_asks"][0][0]:
            problems.append(f"{d['id']}: best bid is not below best ask (crossed or mis-mapped); skipped")
            continue
        if not d["close_time_utc"]:
            problems.append(f"{d['id']}: no close time; skipped (the core needs it for days and annualised return)")
            continue
        out.append(d)
    return out, problems


# ------------------------------------------------------------------ convert

def _sample(text: str, n: int = 6000) -> str:
    return text[:n] + ("\n…(truncated)" if len(text) > n else "")


def convert(cfg: Config | None = None, llm=None, raw: Path = RAW, out: Path = NORM, maps: Path = MAPS,
            say=print) -> dict:
    cfg = cfg or get_config()
    raw.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(raw.iterdir()) if p.suffix.lower() in (".json", ".jsonl", ".csv") and p.is_file()]
    if not files:
        say(f"No .json/.jsonl/.csv files in {raw}. Put downloaded order books there "
            f"(or run `python -m frame.testdata sample` for an invented example).")
        return {"markets": 0}
    markets, report, as_of = [], [], None
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        try:                                                          # 1. already our format: code only
            doc = json.loads(text)
            if isinstance(doc, dict) and isinstance(doc.get("markets"), list) and \
                    all("yes_asks" in m for m in doc["markets"]):
                markets += doc["markets"]
                as_of = as_of or doc.get("as_of")
                report.append(f"{p.name}: our format, {len(doc['markets'])} markets (no Gemini)")
                continue
        except ValueError:
            pass
        key = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        cache = maps / f"{p.stem}-{key}.json"
        if cache.exists():                                            # 2a. mapping cached: no Gemini
            m = Mapping.model_validate_json(cache.read_text(encoding="utf-8"))
            how = "cached mapping (no Gemini)"
        else:                                                         # 2b. Gemini proposes a mapping
            if llm is None:
                from frame.audit import AuditLog
                from frame.llm import make_llm
                llm = make_llm(cfg, AuditLog(cfg.audit_path), replay=False)
            say(f"{p.name}: asking Gemini how this file is laid out…")
            m = llm.generate("map_testdata", SYSTEM, f"FILE NAME: {p.name}\nSAMPLE:\n{_sample(text)}", Mapping,
                             cfg.rules_model, key=key)
            maps.mkdir(parents=True, exist_ok=True)
            cache.write_text(m.model_dump_json(indent=2), encoding="utf-8")
            how = "Gemini mapping"
        got, problems = apply_mapping(text, m, venue_default=m.venue_constant or "manual")
        if m.as_of_path and not as_of:
            try:
                recs = _records(text, m)
                as_of = _get(recs[0], m.as_of_path) if recs else None
            except Exception:
                pass
        markets += got
        report.append(f"{p.name}: {how}, {len(got)} markets" + (f", {len(problems)} problems" if problems else "")
                      + (f" ({m.notes})" if m.notes else ""))
        report += [f"   - {x}" for x in problems[:10]]
    out.mkdir(parents=True, exist_ok=True)
    doc = {"as_of": as_of or datetime.now(timezone.utc).isoformat(), "source": [p.name for p in files], "markets": markets}
    (out / "markets.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    for line in report:
        say(line)
    say(f"Wrote {len(markets)} markets to {out / 'markets.json'}")
    return doc


# ------------------------------------------------------------------ the core on converted data (no Gemini)

def load_universe(path: Path | dict = NORM / "markets.json"):
    """Converted data (a file, or the dict itself) -> the core's own Market objects (+ the snapshot time)."""
    from frame.adapter import _import_core
    _import_core()
    from arbcheck import demo_data, fees, models
    doc = path if isinstance(path, dict) else json.loads(Path(path).read_text(encoding="utf-8"))

    def when(s):
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    out, sports_meta = [], {}
    for d in doc["markets"]:
        yes, no = demo_data.binary([tuple(l) for l in d["yes_bids"]], [tuple(l) for l in d["yes_asks"]])
        venue = {"polymarket": "Polymarket", "kalshi": "Kalshi", "manifold": "Manifold"}.get(d["venue"], "Polymarket")
        fee = fees.KALSHI_FEE if venue == "Kalshi" else fees.polymarket_fee(d.get("category"))
        out.append(models.Market(str(d["id"]), d["title"], d.get("rules_text") or d["title"], when(d["close_time_utc"]),
                                 yes, no, venue=venue, url="", fee=fee, group=str(d.get("event_id") or ""),
                                 meta={"category": d.get("category"), "exclusive": bool(d.get("exclusive")),
                                       "exhaustive": False, "testdata": True}))
        if d.get("start_time_utc") or d.get("game_id"):
            sports_meta[f"{venue.lower()}:{d['id']}"] = {k: d.get(k) for k in ("start_time_utc", "game_id", "sport_type")}
    return out, when(doc.get("as_of") or datetime.now(timezone.utc).isoformat()), sports_meta


def make_adapter(path: Path | dict = NORM / "markets.json"):
    from frame.adapter import ArbcheckAdapter
    universe, as_of, sports_meta = load_universe(path)
    ad = ArbcheckAdapter(demo=True, universe=universe, now=as_of)
    ad._sports.update(sports_meta)
    return ad


def build_pipeline(cfg: Config | None = None, llm=None, path: Path = NORM / "markets.json"):
    """The app on converted data: candidates and Gemini judging as usual, prices from the files."""
    from frame.pipeline import build
    cfg = cfg or get_config()
    return build(cfg=replace(cfg, watch_path=TD / "watchlist.json"), llm=llm, adapter=make_adapter(path))


def core_report(path: Path = NORM / "markets.json", budget: float = 300.0, say=print,
                report: Path = TD / "report.md") -> list[dict]:
    """Everything the core can price from the files with NO LLM. Returns rows sorted best first."""
    from frame import arbtypes, sports
    from frame.priority import VERDICT_PTS
    ad = make_adapter(path)
    ms = ad.list_open_markets(["polymarket", "kalshi"])
    rows = []

    def add(kind, title, core):
        rows.append({"type": arbtypes.label(kind), "what": title, "verdict": core.verdict, "headline": core.headline,
                     "naive_edge": core.naive_edge, "avg_edge": core.avg_edge, "days": core.days,
                     "approved_profit": core.approved_profit})

    for legs in ad.exclusive_sets(ms, max_sets=10_000):
        add(arbtypes.MULTI_OUTCOME, f"{len(legs)} outcomes of {legs[0].event_id}", ad.price_set(legs, budget))
    for m in ms:
        add(arbtypes.SAME_MARKET, m.title, ad.price_single(m, budget))
    seen = set()
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            if a.event_id and a.event_id == b.event_id and ad.cheap_link(a, b):
                seen.add((a.id, b.id))
                add(arbtypes.LOGICAL, f"{a.title}  /  {b.title}", ad.screen_pair(a, b, budget))
    for a, b, why in sports.game_links(ms):
        add(arbtypes.LOGICAL, why, ad.screen_pair(a, b, budget, why=why))
    rows.sort(key=lambda r: (-VERDICT_PTS.get(r["verdict"], 0), -r["avg_edge"]))
    lines = ["# Core on test data (no LLM)", "", f"Data: {path}  ·  snapshot {ad.now().isoformat()}  ·  "
             f"{len(ms)} markets  ·  {len(rows)} structures priced", "",
             "| verdict | type | what | price-only gap | after costs | days |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['verdict'].replace('_', ' ')} | {r['type']} | {r['what'][:90]} | {r['naive_edge'] * 100:.1f}¢ | "
                     f"{r['avg_edge'] * 100:.1f}¢ | {r['days']:.1f} |")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for r in rows[:25]:
        say(f"{r['verdict']:>13} | {r['type']:<22} | {r['what'][:70]:<70} | gap {r['naive_edge'] * 100:5.1f}¢ "
            f"| after costs {r['avg_edge'] * 100:5.1f}¢")
    say(f"{len(rows)} structures priced by the core, no LLM. Full table: {report}")
    return rows


# ------------------------------------------------------------------ polysim recordings (a known format: no Gemini)

def _recording_root(folder: Path) -> Path:
    """The folder that holds markets.json (zips are often unpacked one level deeper)."""
    folder = Path(folder)
    hits = sorted(folder.rglob("markets.json"), key=lambda p: len(p.parts))
    if not hits:
        raise FileNotFoundError(f"no markets.json under {folder}")
    return hits[0].parent


def _polysim_rounds(root: Path):
    """Yield (ts, {token_id: (bids, asks)}) rounds: the recorder snapshots every token about once a minute."""
    import gzip
    books: dict[str, tuple] = {}
    last_ts = None
    for p in sorted(root.glob("books-*.jsonl*")):
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("type") != "book":
                    continue
                if last_ts is not None and d["ts"] - last_ts > 20 and books:   # a new round starts
                    yield last_ts, dict(books)
                books[d["token_id"]] = (d["bids"], d["asks"])
                last_ts = d["ts"]
    if books:
        yield last_ts, dict(books)


def polysim_doc(folder: Path, books: dict, ts: float) -> dict:
    """One recorded moment -> our normalized format. Rules text and tags from the saved Gamma events."""
    root = _recording_root(folder)
    meta = json.loads((root / "markets.json").read_text(encoding="utf-8"))
    rules, tags, neg = {}, {}, {}
    for ev_file in (root / "events").glob("*.json"):
        ev = json.loads(ev_file.read_text(encoding="utf-8"))
        tags[ev.get("slug")] = [t.get("slug") for t in ev.get("tags") or [] if isinstance(t, dict)]
        neg[ev.get("slug")] = bool(ev.get("negRisk"))
        for m in ev.get("markets") or []:
            rules[m.get("conditionId")] = (m.get("description") or "").strip()
    from frame.categories import canonical
    out = []
    for m in meta:
        yes_tok = next((t for t, o in m["tokens"] if str(o).lower() == "yes"), None)
        if yes_tok not in books or m.get("end_ts", 0) <= ts:
            continue
        bids, asks = books[yes_tok]
        if not asks:
            continue
        ev = m.get("event_slug")
        out.append({"venue": "polymarket", "id": m["market_id"], "title": m["question"],
                    "rules_text": rules.get(m["market_id"]) or m["question"],
                    "close_time_utc": datetime.fromtimestamp(m["end_ts"], timezone.utc).isoformat(),
                    "category": canonical(tags.get(ev) or [m.get("category") or ""]) or "other",
                    "event_id": ev, "exclusive": neg.get(ev, False),
                    "yes_bids": [[float(p), float(s)] for p, s in bids], "yes_asks": [[float(p), float(s)] for p, s in asks]})
    return {"as_of": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "source": f"polysim recording {root.name}",
            "markets": out}


def import_polysim(folder: Path, at: str | None = None, out: Path = NORM, say=print) -> dict:
    """Use a polysim recording as test data: the snapshot nearest `at` (ISO time), else the last one."""
    root = _recording_root(folder)
    want = datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() if at else None
    best = None
    for ts, books in _polysim_rounds(root):
        if want is None or best is None or abs(ts - want) < abs(best[0] - want):
            best = (ts, books)
    doc = polysim_doc(root, best[1], best[0])
    out.mkdir(parents=True, exist_ok=True)
    (out / "markets.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    say(f"Imported {len(doc['markets'])} markets from the polysim recording at {doc['as_of']} (no Gemini needed).")
    return doc


def ladder_study(folder: Path, budget: float = 300.0, say=print, report: Path = TD / "ladder_study.md") -> dict:
    """Replay EVERY recorded round through the core and count how often a date/threshold ladder was an
    arbitrage. This answers 'can 'by Nov 8' vs 'by Nov 9' ever pay?' with our own data, not opinion."""
    import collections
    root = _recording_root(folder)
    stats = collections.Counter()
    best: dict[str, tuple] = {}
    rounds = 0
    for ts, books in _polysim_rounds(root):
        rounds += 1
        ad = make_adapter(polysim_doc(root, books, ts))
        ms = ad.list_open_markets(["polymarket"])
        by_ev: dict[str, list] = {}
        for m in ms:
            by_ev.setdefault(m.event_id or m.id, []).append(m)
        for ev, group in by_ev.items():
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if not ad.cheap_link(a, b):
                        continue
                    core = ad.screen_pair(a, b, budget)
                    stats["pairs checked"] += 1
                    stats[core.verdict] += 1
                    if core.naive_edge > 0:
                        stats["price inversion (before costs)"] += 1
                    key = f"{a.title[:60]} / {b.title[:60]}"
                    if key not in best or core.avg_edge > best[key][0]:
                        best[key] = (core.avg_edge, core.naive_edge, core.verdict,
                                     datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M"))
    top = sorted(best.items(), key=lambda kv: -kv[1][0])[:15]
    lines = ["# Ladder study on the polysim recording (core only, no LLM)", "",
             f"Recording: {root.name} · {rounds} recorded rounds · {len(best)} distinct ladder pairs", "",
             "| outcome | count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in stats.most_common()] + [
             "", "Closest each pair came to an arbitrage (best moment):", "",
             "| pair | after costs | before costs | verdict | when (UTC) |", "|---|---|---|---|---|"] + [
             f"| {k} | {v[0] * 100:.1f}¢ | {v[1] * 100:.1f}¢ | {v[2].replace('_', ' ')} | {v[3]} |" for k, v in top]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines[2:]:
        say(line)
    return {"rounds": rounds, "pairs": len(best), "stats": dict(stats), "top": top}


# ------------------------------------------------------------------ an invented example file

def write_sample(raw: Path = RAW) -> Path:
    """An INVENTED example in a 'foreign' CSV layout (one row per price level, prices in cents), so the
    convert step has something to map. Clearly labelled; not market data."""
    raw.mkdir(parents=True, exist_ok=True)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    k = (t0 + timedelta(hours=2)).isoformat()
    rows = [("market_ref", "question", "closes", "group", "tag", "one_of_set", "side", "px_cents", "qty")]

    def lv(ref, q, closes, grp, tag, levels, excl="N"):
        for side, px, qty in levels:
            rows.append((ref, q, closes, grp, tag, excl, side, px, qty))
    d8, d9 = (t0 + timedelta(days=1)).isoformat(), (t0 + timedelta(days=2)).isoformat()
    lv("ex-marathon-8", "EXAMPLE: Will the streamer finish the marathon by day 8?", d8, "ex-marathon", "culture",
       [("BID", 41, 800), ("BID", 40, 500), ("ASK", 43, 250), ("ASK", 45, 400)])
    lv("ex-marathon-9", "EXAMPLE: Will the streamer finish the marathon by day 9?", d9, "ex-marathon", "culture",
       [("BID", 31, 300), ("ASK", 33, 600), ("ASK", 35, 800)])          # inverted: the later date is cheaper
    for ref, q, bid, ask in (("ex-home", "EXAMPLE: Will Home FC win?", 44, 46), ("ex-draw", "EXAMPLE: Will the game end in a draw?", 25, 27),
                             ("ex-away", "EXAMPLE: Will Away FC win?", 24, 26)):
        lv(ref, q, k, "ex-game", "sports", [("BID", bid, 400), ("ASK", ask, 400)], excl="Y")
    p = raw / "example_invented_books.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return p


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="frame.testdata")
    ap.add_argument("cmd", choices=["convert", "core", "sample", "polysim", "ladder-study"])
    ap.add_argument("folder", nargs="?", help="polysim recording folder (polysim / ladder-study)")
    ap.add_argument("--at", help="polysim: ISO time of the snapshot to use (default: the last one)")
    args = ap.parse_args(argv)
    if args.cmd in ("polysim", "ladder-study"):
        folder = Path(args.folder or next(iter(sorted(Path(".").glob("polysim-recordings-*"))), "recordings"))
        if args.cmd == "polysim":
            import_polysim(folder, args.at)
            print("Next: run.bat option 6 (core on this data) or 7 (the app on it).")
        else:
            ladder_study(folder)
        return
    if args.cmd == "sample":
        print(f"Wrote an INVENTED example to {write_sample()}. Next: run.bat option 5 (convert).")
    elif args.cmd == "convert":
        if not RAW.exists() or not any(p.is_file() for p in RAW.iterdir()):
            print(f"{RAW} is empty, so an invented example file is written there first.")
            write_sample()
        convert()
    else:
        if not (NORM / "markets.json").exists():
            print("No converted data yet: run convert first (run.bat option 5).")
            return
        core_report()


if __name__ == "__main__":
    main()
