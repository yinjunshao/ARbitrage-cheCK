"""Score rule checkers on REAL cross-venue pairs (Polymarket vs Kalshi).

Put this file in the repo's scripts/ folder and real_pairs.json in data/, then from the repo root:

    python scripts/eval_real_pairs.py                 # title-only baseline + Arbcheck's offline rule reader
    python scripts/eval_real_pairs.py --gemini        # also Gemini's rule comparison (needs GEMINI_API_KEY in .env)

What it measures: for each pair a person has labelled the rules as
  material  - a plausible outcome makes the two contracts pay differently
  edge      - they differ only in rare cases (delays, cancellations, ties)
  match     - no difference
A checker "catches" a pair if it says the difference is serious. We report hits (material pairs
caught), misses (material pairs passed as safe) and false alarms (edge/match pairs called serious),
as plainly as each other. Small n: say so on the slide.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arbcheck-project"))
sys.path.insert(0, str(ROOT))

from arbcheck.models import Book, Market  # noqa: E402
from arbcheck.relations import HeuristicExtractor  # noqa: E402

VENUE_NAME = {"polymarket": "Polymarket", "kalshi": "Kalshi", "manifold": "Manifold"}


def core_market(side: dict, pid: str, tag: str) -> Market:
    # Prices are not needed to compare rules; the deadline is a placeholder shared by both sides.
    end = datetime(2026, 12, 31, tzinfo=timezone.utc)
    return Market(id=f"{pid}-{tag}", question=side["title"], rules=side["rules"], end=end,
                  yes=Book(), no=Book(), venue=VENUE_NAME.get(side["venue"], side["venue"]), url=side["url"])


def baseline(_: dict) -> tuple[bool, str]:
    """A title-only matcher: if the titles name the same event it treats the pair as the same bet."""
    return False, "titles match, so it treats them as the same bet"


def offline_reader(p: dict) -> tuple[bool, str]:
    a, b = core_market(p["a"], p["id"], "a"), core_market(p["b"], p["id"], "b")
    rel = HeuristicExtractor().extract(a, b)
    if rel.kind == "none":
        return False, "could not link the two titles, so it raised nothing"
    serious = [f for f in rel.flags if f.severity == "high"]
    kinds = ", ".join(sorted({f.kind for f in rel.flags})) or "none"
    return bool(serious), f"link={rel.kind}; flags: {kinds}"


def gemini_checker():
    from frame.config import get_config
    from frame.llm import GeminiClient
    from frame.rulediff import compare_rules
    from frame.schemas import Market as FMarket
    cfg = get_config()
    if not cfg.gemini_api_key:
        sys.exit("No GEMINI_API_KEY found. Copy .env.example to .env and add your key.")
    llm = GeminiClient(cfg)

    def run(p: dict) -> tuple[bool, str]:
        fa = FMarket(venue=p["a"]["venue"], id=p["id"] + "-a", title=p["a"]["title"], rules_text=p["a"]["rules"], url=p["a"]["url"])
        fb = FMarket(venue=p["b"]["venue"], id=p["id"] + "-b", title=p["b"]["title"], rules_text=p["b"]["rules"], url=p["b"]["url"])
        out = compare_rules(llm, cfg.rules_model, fa, fb)
        diffs = out.diff.differences
        serious = [d for d in diffs if d.severity in ("blocker", "material")]
        return bool(serious), f"overall={out.diff.overall}; " + "; ".join(f"{d.topic}:{d.severity}" for d in diffs)
    return run


def score(name: str, fn, pairs: list[dict]) -> dict:
    hits = misses = false_alarms = 0
    rows = []
    for p in pairs:
        caught, why = fn(p)
        truth = p["label"]
        if truth == "material":
            hits += caught
            misses += not caught
        elif caught:
            false_alarms += 1
        rows.append((p["id"], truth, "serious" if caught else "passed", why))
    n_mat = sum(p["label"] == "material" for p in pairs)
    n_other = len(pairs) - n_mat
    print(f"\n== {name}")
    for r in rows:
        print(f"  {r[0]:<20} truth={r[1]:<9} said={r[2]:<8} {r[3]}")
    print(f"  material caught {hits}/{n_mat}, missed {misses}, false alarms {false_alarms}/{n_other}")
    return {"checker": name, "hits": hits, "material": n_mat, "misses": misses,
            "false_alarms": false_alarms, "non_material": n_other}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "real_pairs.json"))
    ap.add_argument("--gemini", action="store_true", help="also run Gemini's rule comparison")
    ap.add_argument("--out", default="", help="write the summary as JSON here")
    args = ap.parse_args()
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    pairs = data["pairs"]
    print(f"{len(pairs)} real pairs; rules verbatim: {data.get('rules_verbatim')}; labeller: {data.get('labeller')}")
    results = [score("Title-only baseline", baseline, pairs),
               score("Arbcheck offline rule reader", offline_reader, pairs)]
    if args.gemini:
        results.append(score("Gemini rule comparison", gemini_checker(), pairs))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
