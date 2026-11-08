---
tags: [agentverse, repo-doc]
source: docs/CORE_REQUESTS.md
synced: 2026-11-08 13:35
---
> Copy of `docs/CORE_REQUESTS.md` from the repo (branch `integrated/llm-and-core`), synced 2026-11-08 13:35. Edit the repo file, then re-run `python scripts/sync_vault_docs.py`. Index: [[Repo Docs Index]]

# Requests for the core (Debby)

The frame does not edit `arbcheck-project/`. Requests go here.

1. **Public flag helper.** The adapter calls `HeuristicExtractor._flags(a, b, kind, sa, sb)` (private) to union the
   core's deterministic rule flags. A public `rule_flags(a, b, kind)` would make that seam stable.
2. **Category on `Market`.** Polymarket stubs drop the Gamma category, so the frame guesses crypto from the fee
   (`k == 0.07`) and keywords. A `category` field (or `meta["category"]`) would make the no-crypto rule exact.
3. **Kalshi listing.** A `Kalshi.list_open(limit)` (open events, crypto series dropped, `close_time` re-filtered)
   would let the frame's proactive scan include Kalshi for members.
4. **Provider-neutral LLM call.** `relations.anthropic_call` is the only LLM backend. The frame uses Gemini; a
   `call(system, user) -> str` factory is all `LLMExtractor` needs, so `frame.llm` could supply one if useful.
5. **`core_version`.** `arbcheck.__version__` is 0.1.0; bump it when maths or guard rules change so audit records
   can be tied to a version.
6. **URGENT: threshold direction in `HeuristicExtractor` (a false arbitrage the core itself reports).** On
   downward-threshold ladders it orders the implication as if upward. Example from the polysim recording of
   2026-11-07/08 ("What price will WTI hit in November 2026?"): A = "Will WTI hit (LOW) $80 in November?",
   B = "Will WTI hit (LOW) $70 in November?" → the extractor returns `a_implies_b` (LOW $80 ⇒ LOW $70), which is
   backwards (falling to $70 means it passed $80 first, so LOW $70 ⇒ LOW $80). The guard then reports "Rules match and
   the gap survives costs. About $100 locked in on $300" — in **1,636 of 4,600** WTI ladder checks across 46 recorded
   rounds, edges up to ~80¢. Every upward ladder (Bitcoin "reach", "above") and every date ladder was correct.
   Reproduce: `python -m frame.testdata ladder-study <recording folder>` (report: `data/testdata/ladder_study.md`).
   Suggested fix: read direction words ("(LOW)", "below", "dip to", "fall to", "under") and reverse the threshold order.
   Frame workaround meanwhile: `ArbcheckAdapter.screen_pair` blocks such ladders (`threshold_direction_unverified`).
