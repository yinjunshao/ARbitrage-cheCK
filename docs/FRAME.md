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

Categories come from the venues themselves (Polymarket event tags, Kalshi event category), mapped to one
vocabulary in `frame/categories.py`. Rules apply to **suggestions**. A pair you type in yourself is still
checked, but a rule failure is shown as a warning instead of a refusal.

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
