"""Command line.

    python -m arbcheck demo                      # offline demo on synthetic markets -> report.html
    python -m arbcheck demo --kill-switch        # see the kill switch block everything
    python -m arbcheck live --limit 300 --llm    # live Polymarket data (needs network, API key for --llm)
    python -m arbcheck serve --demo              # the app, on invented markets, no internet needed
    python -m arbcheck serve                     # the app on live Polymarket + Manifold data
    python -m arbcheck serve --kalshi-member     # also Kalshi, if YOU are a Kalshi member (local only)
    python -m arbcheck check "event one" "event two" [--demo]   # same check, printed in the terminal
    python -m arbcheck eval                      # score the extractor on the hand-labelled demo pairs
    python -m arbcheck collect --every 60        # record live edges for the persistence study
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from . import polymarket
from .demo_data import DEMO_LABELS, DEMO_NOW, demo_pairs, demo_snapshots
from .guard import DecisionLog, Guard, Limits
from .persistence import SnapshotStore, episodes, kaplan_meier, median_survival
from .pipeline import candidate_pairs, scan
from .relations import HeuristicExtractor, LLMExtractor, anthropic_call, evaluate_extractor
from .report import render_report


def _limits(args) -> Limits:
    return Limits(max_notional=args.max_notional, max_total_notional=args.max_total,
                  min_edge=args.min_edge_cents / 100, kill_switch=args.kill_switch)


def _extractor(args):
    if getattr(args, "llm", False):
        return LLMExtractor(anthropic_call(args.model), name=f"llm:{args.model}")
    return HeuristicExtractor()


def survival_summary(store: SnapshotStore, pair_id: str, poll_s: float, synthetic: bool) -> dict | None:
    eps = episodes(store.series(pair_id), poll_s)
    if not eps:
        return None
    curve = kaplan_meier([e.duration_s for e in eps], [not e.ongoing for e in eps])
    med = median_survival(curve)
    return {"curve": curve, "n_episodes": len(eps), "n_open": sum(e.ongoing for e in eps),
            "median_min": med / 60 if med is not None else None, "synthetic": synthetic}


def cmd_demo(args) -> None:
    guard = Guard(_limits(args))
    log = DecisionLog(args.log) if args.log else None
    reports = scan(demo_pairs(), _extractor(args), guard, DEMO_NOW, fee_rate=args.fee, log=log)
    store = SnapshotStore()
    demo_snapshots(store, "demo")
    sv = survival_summary(store, "demo", poll_s=60, synthetic=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render_report(reports, guard.limits, survival=sv, demo=True, generated=DEMO_NOW))
    for r in reports:
        print(f"{r.decision.status:8} {r.a.question[:58]:58} {r.decision.headline}")
    print(f"\nWrote {args.out}")


def cmd_live(args) -> None:
    now = datetime.now(timezone.utc)
    stubs = polymarket.fetch_stubs(args.limit)
    pairs = candidate_pairs(stubs)
    print(f"{len(stubs)} markets, {len(pairs)} candidate pairs")
    seen = {}
    for a, b in pairs:                       # fetch order books only for markets in a pair
        for m in (a, b):
            if m.id not in seen:
                seen[m.id] = polymarket.hydrate(m)
    guard = Guard(_limits(args))
    log = DecisionLog(args.log) if args.log else None
    reports = scan(pairs, _extractor(args), guard, now, fee_rate=args.fee, log=log)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render_report(reports, guard.limits, generated=now))
    print(f"Wrote {args.out}")


def cmd_collect(args) -> None:
    """Poll live order books and record each pair's executable edge, so the survival curve
    in the report is built from real data. Run it in the background for hours or days.
    Relations are extracted once per pair (rules do not change) and cached."""
    import time
    from .engine import evaluate_pair
    store = SnapshotStore(args.db)
    pairs = candidate_pairs(polymarket.fetch_stubs(args.limit))
    ex = _extractor(args)
    relations: dict[str, object] = {}
    print(f"collecting {len(pairs)} pairs every {args.every}s into {args.db}")
    for cycle in range(args.cycles):
        now = datetime.now(timezone.utc)
        for a, b in pairs:
            pid = f"{a.id}|{b.id}"
            polymarket.hydrate(a)
            polymarket.hydrate(b)
            rel = relations.setdefault(pid, ex.extract(a, b))
            arb = evaluate_pair(a, b, rel.kind, now, args.fee)
            store.record(pid, now, arb.avg_edge, arb.shares)
        print(f"cycle {cycle + 1}/{args.cycles} done")
        if cycle + 1 < args.cycles:
            time.sleep(args.every)


def cmd_serve(args) -> None:
    from .web import App, serve
    llm = _extractor(args) if args.llm else None
    app = App(demo=args.demo, kalshi_member=args.kalshi_member, host=args.host, llm=llm, log_path=args.log)
    server = serve(app, args.host, args.port)
    names, skipped = app.venue_status()
    print(f"Arbcheck is running at http://{args.host}:{server.server_port}/  (Ctrl+C to stop)")
    print("Demo mode: all markets are invented." if args.demo else f"Reading: {', '.join(names)}")
    for k, v in skipped.items():
        print(f"  {k}: {v}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_check(args) -> None:
    from .compliance import ReadOnlyClient, is_loopback
    from .demo_data import build_demo_registry
    from .finder import find
    from .venues import build_registry
    now = DEMO_NOW if args.demo else datetime.now(timezone.utc)
    reg = build_demo_registry() if args.demo else build_registry(ReadOnlyClient(), args.kalshi_member, True)
    log = DecisionLog(args.log) if args.log else None
    f = find(args.events, reg, _extractor(args), Guard(_limits(args)), now, args.fee, log)
    for res in f.inputs:
        print(f"> {res.line}")
        for m in res.matches[:6]:
            print(f"    matched {m.market.venue}: {m.market.question}")
        for n in res.notes:
            print(f"    {n}")
    print(f"\nVerdict: {f.verdict.upper()}")
    for c in f.yours:
        print(f"  yours   [{c.decision.status}] {c.family_label}: {c.decision.headline}")
    for c in f.related:
        if c.decision.status != "NO_ARB":
            print(f"  related [{c.decision.status}] {c.family_label}: {c.decision.headline}")
    print(f"\nChecked {f.checked} combinations.")


def cmd_eval(args) -> None:
    labelled = [(a, b, lab) for (a, b), lab in zip(demo_pairs(), DEMO_LABELS)]
    print(evaluate_extractor(_extractor(args), labelled))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="arbcheck")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("demo", cmd_demo), ("live", cmd_live), ("eval", cmd_eval), ("collect", cmd_collect),
                     ("serve", cmd_serve), ("check", cmd_check)):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        s.add_argument("--out", default="report.html")
        s.add_argument("--log", default=None, help="append a JSONL audit trail here")
        s.add_argument("--llm", action="store_true", help="use Claude to read the rules (needs ANTHROPIC_API_KEY)")
        s.add_argument("--model", default="claude-sonnet-5-5")
        s.add_argument("--max-notional", type=float, default=300.0)
        s.add_argument("--max-total", type=float, default=1000.0)
        s.add_argument("--min-edge-cents", type=float, default=1.0)
        s.add_argument("--fee", type=float, default=0.0, help="fee as a fraction of notional (check the venue)")
        s.add_argument("--kill-switch", action="store_true")
        if name in ("serve", "check"):
            s.add_argument("--demo", action="store_true", help="use invented markets (works offline)")
            s.add_argument("--kalshi-member", action="store_true",
                           help="I am a Kalshi member and am running this on my own computer")
        if name == "serve":
            s.add_argument("--host", default="127.0.0.1")
            s.add_argument("--port", type=int, default=8765)
        if name == "check":
            s.add_argument("events", nargs="+", help="links or events in words")
        if name in ("live", "collect"):
            s.add_argument("--limit", type=int, default=200)
        if name == "collect":
            s.add_argument("--db", default="snapshots.db")
            s.add_argument("--every", type=float, default=60.0, help="seconds between polls")
            s.add_argument("--cycles", type=int, default=60)
    args = p.parse_args(argv)
    if getattr(args, "llm", False) and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("--llm needs ANTHROPIC_API_KEY to be set")
    args.fn(args)


if __name__ == "__main__":
    main()
