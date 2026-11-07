# CORE_MAP: frame ⇄ numeric core

> **Status 2026-11-07: NOT VERIFIED.** The core (`core/checker`, Debby) is not in this repo or on `origin` yet
> (`git ls-remote origin` showed no branches). Nothing below has been read from real code. The frame runs against
> `FakeCoreAdapter` until this page is rewritten from the actual source.

## The seam

`frame/adapter.py` is the only file that imports the core. The frame needs four things from it:

| Frame call | Purpose | Status |
|---|---|---|
| `price(relation, size) -> CoreResult` | floor payout, order-book walk, fees, annualised return, guard verdict | **Assumed** to wrap `fac_core.evaluate(rel, size) -> Opportunity` (note 12) |
| `list_open_markets(venues) -> list[Market]` | discovery through the core's compliant, allowlisted, rate-spaced client | **Not wired.** Should call the `compliance.py` venue adapters (note 13) |
| `parse_link(url) -> Market` | link parsing for the Check page | **Not wired.** Should call `venues.py` |
| `compare_rules(a, b) -> VerifiedRuleDiff \| None` | reuse `relations.py --llm` quote extractor | **Not wired.** Returns `None`, so the frame's own `rulediff.py` runs |

## Known conflicts between the two planning notes

| Topic | Note 12 (architecture) | Note 13 (hand-off) |
|---|---|---|
| Package | `src/fac_core/` with `api.py`, `constraints.py`, `fees.py`… | top-level `venues.py`, `engine.py`, `guard.py`, `finder.py`… |
| Entry point | `evaluate(rel, size) -> Opportunity` | "find the single function that takes markets + relation and returns a priced verdict" |
| Verdict | not in `Opportunity` field list | `guard.py` returns worth a look / look closely / blocked / nothing to do |
| Relation types | complement, exhaustive, implication, cross-venue | implication, same event cross-venue, mutually exclusive set, Yes+No |
| LLM vendor in core | Anthropic | core `--llm` mode uses Claude; **frame uses Gemini** |

`RealCoreAdapter` tries `fac_core`, then `core`, then `engine`, and maps fields by the note-12 names. Every
mapping is marked `TODO(core)`.

## Frame-side assumptions to check against real code

1. `CoreResult` fields (`frame/schemas.py`) match `Opportunity`, plus a `verdict` from the guard and `guard_checks`.
2. `fees` is a dict per venue (the adapter converts a list if needed).
3. The frame's `RelationType` maps onto the core's relation names: `same_event` → cross-venue; `a_implies_b` /
   `b_implies_a` → implication with legs ordered; `exhaustive_set` → exhaustive; `mutually_exclusive` → ?
4. All fractions (edges, returns) are 0–1, not percent. The grounding check accepts either `x` or `100·x`.
5. The core is deterministic and does no I/O inside `evaluate` (note 12). Discovery I/O goes through its client.

## Frame-side guarantees regardless of the core

- The core only ever receives a `Relation` built after a human confirms.
- The frame can only tighten the core's verdict (incompatible rules → `blocked`), never loosen it.
- Narratives are rejected if they contain a number not in the core result or a verified quote.
