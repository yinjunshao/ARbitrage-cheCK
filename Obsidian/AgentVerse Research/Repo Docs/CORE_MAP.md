---
tags: [agentverse, repo-doc]
source: docs/CORE_MAP.md
synced: 2026-11-08 13:20
---
> Copy of `docs/CORE_MAP.md` from the repo (branch `integrated/llm-and-core`), synced 2026-11-08 13:20. Edit the repo file, then re-run `python scripts/sync_vault_docs.py`. Index: [[Repo Docs Index]]

# CORE_MAP: frame ⇄ numeric core (arbcheck)

> Written from the real code on branch `arbcheck-submission` (merged into `frame/llm-layer`, 2026-11-07).
> Core lives in `arbcheck-project/arbcheck/` (standard library only, 152 tests). The frame does not edit it.

## The seam

`frame/adapter.py` is the only frame file that imports `arbcheck`. It adds `arbcheck-project/` to `sys.path`.

| Frame call (`CoreAdapter`) | arbcheck code it uses |
|---|---|
| `price(relation, budget) -> CoreResult` | `pipeline.evaluate_case(markets, Relation, Guard(Limits(max_notional=budget)), now, log=DecisionLog)` → `Case.arb: ArbResult`, `Case.decision: Decision` |
| `list_open_markets(venues)` | live: `GET gamma /events` (top by 24h volume, small pages under the client's 5 MB cap) → `polymarket.stubs_from_event`; Kalshi (members) `GET /events?with_nested_markets` → `kalshi.market_from`; all through `compliance.ReadOnlyClient`. Event tags/category kept for the suggestion rules. Tests only: `demo_data.demo_markets()` |
| `cheap_link(a, b)` | `relations.HeuristicExtractor().extract` (no LLM): which pairs inside one event go to the judge |
| `resolve(line)` | `venues.Registry.resolve` (links parsed locally, never fetched; words searched with the ≥35% overlap rule) |
| order books | `Registry.hydrate` at price time (live only) |
| `compare_rules` | not reused: `relations.LLMExtractor` is Anthropic-shaped and fuses relation + flags; the frame's Gemini `rulediff` returns per-topic verified quotes instead |

## Core public types the frame reads

| arbcheck | Fields used |
|---|---|
| `models.Market` (dataclass) | `id, question, rules, end, yes/no: Book, venue, url, fee, tradeable, group, meta` |
| `relations.Relation` | `kind, confidence, flags[MismatchFlag], rationale, extractor, evidence_a/b, verified, problems, basis` |
| `relations.MismatchFlag` | `kind ∈ {deadline_semantics, source, timezone, definition, inclusion, tie_handling, venue_oracle}`, `severity ∈ {low, medium, high}`, `clause_a, clause_b, note` |
| `engine.ArbResult` | `kind, legs, floor, naive_edge, top_edge, avg_edge, shares, cost, fee_cost, net_profit, ret, ann_ret, days, fees[Fee], scenarios` |
| `guard.Decision` | `status ∈ {OK, WARN, BLOCKED, NO_ARB}, headline, checks[Check], approved_*, haircut_per_share, risk_adj_edge, risk_adj_ann, clipped` |

Units: prices and edges are dollars per share (0.01 = 1¢); `ret`/`ann_ret` are fractions.

## Mappings (frame → arbcheck)

| Frame | arbcheck |
|---|---|
| relation `same_event` / `a_implies_b` / `b_implies_a` / `mutually_exclusive` / `exhaustive_set` | kind `equivalent` / `a_implies_b` / `b_implies_a` / `mutually_exclusive` / `partition` |
| rule topic `data_source` / `yes_definition` / `time_zone` / `deadline` / `tie_break` / `other` | flag `source` / `definition` / `timezone` / `deadline_semantics` / `tie_handling` / `definition` |
| severity `blocker` / `material` / `cosmetic` | `high` / `medium` / `low` |
| judge confidence `low` / `medium` / `high`; human-edited | `0.5` / `0.75` / `0.9`; `0.75` (guard minimum is 0.75) |
| evidence verified and not edited | `basis="text"`; otherwise `basis="human"` (rationale names the confirmer) |
| any rule-diff quote unverified | `verified=False` → guard BLOCKS |
| guard status `OK` / `WARN` / `BLOCKED` / `NO_ARB` | verdict `worth_a_look` / `look_closely` / `blocked` / `nothing_to_do` |
| budget (UI "Max notional $") | `Limits.max_notional` |

## Caution only ever increases

- Gemini's rule differences become flags: a `blocker` becomes a `high` flag, and the guard blocks.
- The core's own deterministic flags are unioned in (`HeuristicExtractor._flags`) for equivalent/implication
  pairs, and `venue_oracle` for every cross-venue pair, matching the core's "cross-venue always carries rule risk".
- `tighten()` can move a verdict towards `blocked`, never away. The UI cannot approve `blocked`/`nothing_to_do`.

## Verified (tests/frame/test_arbcheck_adapter.py, offline)

Core demo outcomes reproduced through the adapter: Orion and Vega worth a look; Helix blocked; Polar and Zephyr
look closely; Kestrel and Marlow nothing to do. Also: blocker → blocked; unverified quote → blocked; low confidence
→ blocked; budget clips size; links resolve through the core registry; manual legs priced; full pipeline with the
real core passes and the audit chain verifies.

## Not verified

- Live venue calls (core README: adapters were only tested on canned JSON). `--live` is the first real run.
- Kalshi live: only via links/search (arbcheck has no Kalshi "list all" call), and only with `--kalshi-member`.
- Categories come from Polymarket event tags and Kalshi event categories; markets reached by link/search get
  their event's tags with one extra lookup. A market whose tags map to nothing is "unknown" and rule R4 refuses it.
