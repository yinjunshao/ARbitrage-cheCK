"""Monitor live markets on a loop.  Paper only: nothing here places orders.

    python -m frame.monitor                    # every FRAME_MONITOR_EVERY_S seconds (default 300)
    python -m frame.monitor --once             # one cycle, then exit
    python -m frame.monitor --kalshi-member    # include Kalshi for this run (or FRAME_KALSHI_MEMBER=1 in .env)

Each cycle:
  1. scan: pull open events through arbcheck's read-only client, generate candidates, apply the
     suggestion rules, judge up to FRAME_MAX_JUDGE_PER_SCAN new pairs with Gemini (cached verdicts are free);
  2. re-price every human-confirmed relation on fresh order books with the core (no LLM);
  3. write everything to data/frame/suggestions.jsonl (hash-chained) and print a summary.
New suggestions still need a human to confirm them in the app before the core prices them.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frame.config import get_config  # noqa: E402
from frame.pipeline import build  # noqa: E402


def cycle(p, budget: float) -> None:
    t0 = time.time()
    s = p.scan()
    print(f"[{s.scan}] {s.markets} markets | {s.candidates} candidates | {s.rule_refused} refused by rules | "
          f"{s.judged} judged | {s.surfaced} for review | {s.deferred} deferred ({time.time() - t0:.0f}s)", flush=True)
    for n in s.notes:
        print(f"   note: {n}", flush=True)
    print(f"   {s.sets} venue-declared exclusive sets priced by the core (no LLM)", flush=True)
    for h in s.set_hits:
        print(f"   SET {h}", flush=True)
    for c in p.review_queue():
        v = c.judged.verdict
        print(f"   suggest [{v.relation}/{v.confidence}] {c.judged.a.title[:60]}  <->  {c.judged.b.title[:60]}", flush=True)
    for rel, core, err in p.reprice_confirmed(budget):
        names = " <-> ".join(l.title[:45] for l in rel.legs)
        print(f"   reprice {names}: " + (f"{core.verdict} | {core.headline}" if core else f"error: {err}"), flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="frame.monitor")
    ap.add_argument("--every", type=int, default=None, help="seconds between cycles")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--kalshi-member", action="store_true")
    ap.add_argument("--budget", type=float, default=300.0, help="max notional per re-priced relation ($)")
    args = ap.parse_args(argv)
    cfg = get_config()
    if args.kalshi_member:
        cfg = replace(cfg, kalshi_member=True)
    every = args.every or cfg.monitor_every_s
    p = build(cfg=cfg)
    p.on_status = lambda t: print(f"   … {t}", flush=True)
    print(f"Monitoring live markets (paper only). Kalshi {'ON' if cfg.kalshi_member else 'off'}. "
          f"Judge model {cfg.judge_model}. Log: {cfg.suggestions_path}", flush=True)
    while True:
        try:
            cycle(p, args.budget)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # a bad cycle never kills the monitor
            print(f"   cycle failed: {type(e).__name__}: {e}", flush=True)
        if args.once:
            break
        time.sleep(every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
