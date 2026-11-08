# False-Arb Checker on live data: LLM frame (Gemini) + numeric core (arbcheck) + polysim

```
 live venues (arbcheck read-only client)        frame (Gemini)                            arbcheck core
 discover ──► candidates ──► SUGGESTION RULES ──► judge_pair ──► compare_rules ──► [HUMAN CONFIRM] ──► evaluate_case ──► narrate
 top events    within-event   R1 no Manifold leg   verbatim       verbatim quotes                       order books, fees,   every number
 by volume,    (core-linked)  R2 crypto only with  evidence       per topic                             worlds, guard        traced to core
 with venue    + cross-event  crypto, same coin    verified
 categories    similarity     R3 same category                              every step ──► data/frame/suggestions.jsonl (hash chain)
                              R4 category known                                            ──► data/frame/audit.jsonl
```

## Run (Windows)

```bat
run.bat                 :: tests, then the app on LIVE Polymarket data: http://127.0.0.1:8080
run.bat kalshi          :: same plus Kalshi (Kalshi members only, on your own computer)
run.bat monitor         :: scan live markets every 5 min, log every suggestion, re-price confirmed pairs
run.bat monitor-kalshi  :: the monitor plus Kalshi
run.bat test            :: arbcheck + polysim + frame tests
run.bat core            :: arbcheck's own app (no LLM)
```

Needs `GEMINI_API_KEY` in `.env` (never in `.env.example`). There is no demo mode in the app any more; the
invented markets exist only as test fixtures.

## Suggestion rules (`frame/rules.py`, version `suggest-rules/1`)

| Rule | Meaning |
|---|---|
| R1 | Manifold is play money: never a leg |
| R2 | Crypto pairs only with crypto, and only the same coin (BTC with BTC) |
| R3 | Both markets must be the same type (same category: economics, politics, sports, …) |
| R4 | A category must be known on both sides; unknown is not assumed to match |
| R5 | Both markets must close within 45 days (`FRAME_MAX_DAYS_TO_CLOSE`): arbitrage only works near the deadline, when prices are pinned by resolution and capital comes back quickly. A missing close date is refused |
| R6 | The two must close within 14 days of each other (`FRAME_MAX_CLOSE_GAP_DAYS`): the same question over another timeframe or year is a different bet, not a price gap |

Categories come from the venues themselves (Polymarket event tags, Kalshi event category), mapped to one
vocabulary in `frame/categories.py`. Rules apply to **suggestions**. A pair you type in yourself is still
checked, but a rule failure is shown as a warning instead of a refusal.

## Arbitrage types (`frame/arbtypes.py`)

Named as the numeric core names them, and labelled the same before and after pricing:

| Type | What | How it is found |
|---|---|---|
| **Cross-platform** | the same or a linked event on two venues | candidate pairs + Gemini judge + your confirmation |
| **Logical link** | one outcome forces another on one venue (nested deadlines, thresholds) | the same |
| **Multi-outcome set** | outcomes of one event that can't all win | venue-declared sets (Polymarket `negRisk`, Kalshi `mutually_exclusive`); priced by the core, **no LLM** |
| **Same market (Yes + No)** | Yes and No of one market cost under $1 | a few markets per scan (`FRAME_MAX_SINGLES_PER_SCAN`, default 30), spread over categories; priced by the core, **no LLM** |

The last two need nothing confirmed, so they appear straight away as "priced from structure alone" with a template
explanation. The Opportunities page filters by type, category, confidence and source, and sorts suggestions (confidence,
similarity, closing soonest, category, type) and priced results (verdict, edge after costs, price-only gap, category).

**Balanced judging:** the per-scan Gemini budget is shared out evenly across categories (cross-venue pairs first inside
each), so a category with many markets, like crypto, cannot use all of it.

## Suggestion log (`frame/suggestions.py`)

Every pair the frame considers is logged with a reason: `candidate`, `rule_refused` (rule + reason),
`deferred` (over the per-scan LLM budget, retried next scan), `judged`, `surfaced`/`filtered`, `human`
(confirmed/edited/rejected/approved/killed), `priced`, `repriced`. Hash-chained; the app's **Suggestions** page
verifies the chain and filters by status. Kalshi prices and core numbers are never written to it.

## Modules

| Layer | Files |
|---|---|
| LLM | `frame/llm.py` (Gemini; Fake/Replay only for tests) |
| Core seam | `frame/adapter.py` (live discovery, link resolution, pricing via `arbcheck`) |
| Suggestions | `frame/candidates.py`, `frame/categories.py`, `frame/rules.py`, `frame/suggestions.py` |
| Judging | `frame/judge.py`, `rulediff.py`, `narrate.py`, `ground.py` |
| Orchestration | `frame/pipeline.py`, `frame/monitor.py`, `audit.py`, `fewshot.py` |
| UI | `app_frame/main.py` |
| Paper trading | `polysim/` (merged from `claude/lucid-hawking-s7cu9z`) |

Data sources and what to add next: `docs/DATA_SOURCES.md`.
