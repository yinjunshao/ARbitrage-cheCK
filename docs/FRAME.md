# False-Arb Checker: LLM frame (Gemini) + numeric core (arbcheck)

```
 arbcheck registry / demo universe          frame (Gemini)                      arbcheck core
 discover ──► candidates ──► judge_pair ──► compare_rules ──► [HUMAN CONFIRM] ──► evaluate_case ──► narrate ──► approve/review/kill
 (core's        embeddings     verbatim       verbatim quotes      relation +          worlds, order-book   every number     paper only,
  read-only     top-k + same   evidence       per topic            flags built by      walk, fees, guard's  traced to the    audit hash chain
  client)       event groups   verified       verified             frame/adapter.py    nine checks          core result
```

Two packages, one seam: `frame/adapter.py` is the only file importing `arbcheck`. See `CORE_MAP.md`.

## Run (Windows)

```bat
run.bat            :: set up, run all tests, start the app (live Gemini if .env has a key, else offline replay)
run.bat replay     :: fully offline: arbcheck's invented markets + recorded Gemini outputs
run.bat demo       :: invented markets + live Gemini
run.bat live       :: live Polymarket/Manifold through arbcheck's read-only client + live Gemini
run.bat test       :: 152 core tests + frame tests
run.bat core       :: the core's own app, no LLM frame (http://127.0.0.1:8765)
```

The app is at http://127.0.0.1:8080. Put the Google AI Studio key in `.env` as `GEMINI_API_KEY=` (never in `.env.example`).

## Modules

| Layer | Files | Swappable via |
|---|---|---|
| LLM | `frame/llm.py` (Gemini, Fake, Replay, Recording) | `GEMINI_MODEL`, `FRAME_*_MODEL`, `--replay` |
| Core | `frame/adapter.py` (`ArbcheckAdapter`, `FakeCoreAdapter`) | `FRAME_CORE=arbcheck|fake` |
| Candidates | `frame/candidates.py` (hashing or bge-small embedder) | `FRAME_EMBEDDER=hashing` |
| Judging | `frame/judge.py`, `rulediff.py`, `narrate.py`, `ground.py` | prompts in each file |
| Orchestration | `frame/pipeline.py` (state machine), `audit.py`, `fewshot.py` | — |
| UI | `app_frame/main.py` (NiceGUI) | `--replay/--demo/--live` |

## What is verified

- Core: 152 tests (theirs). Frame: unit tests with a fake LLM and fake core, adapter tests against the real core,
  and a live Gemini smoke test (when a key is set).
- Live Gemini on the core's demo universe (`scripts/record_replay.py`): 26 pairs judged, 13 surfaced and priced by
  the core, and every explanation grounded on the first try. Gemini caught the Helix false arbitrage at the judge
  stage (different resolution sources) and read Atlas as an implication, not the same event.
- **Not run:** live venue data (`run.bat live` is the first real run of the core's venue adapters too).
