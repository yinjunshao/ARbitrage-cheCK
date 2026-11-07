---
tags: [agentverse, build, ops]
created: 2026-11-07
---
# 17 · Run, configure and operate

Related: [[14 Built App — How It Works]] · [[15 Suggestion Rules & Log]] · [[16 Live Data Sources]].

## Running it (Windows, from the repo root)
| Command | What it does |
|---|---|
| `run.bat` | sets up the `.venv`, runs the tests, starts the app on live data at **http://127.0.0.1:8080** |
| `run.bat monitor` | scans live markets every 5 minutes, logs every suggestion, re-prices confirmed pairs, prints progress |
| `run.bat kalshi` / `run.bat monitor-kalshi` | the same with Kalshi on for that run, whatever `.env` says |
| `run.bat test` | all tests (core, polysim, frame) then stops |
| `run.bat core` | the core's own app with no LLM frame (http://127.0.0.1:8765) |

`run.bat` prints whether Kalshi is on or off at start. Needs a Google AI Studio key in `.env`.

## `.env` settings (copy `.env.example`; never commit `.env`)
| Key | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | Google AI Studio key |
| `GEMINI_MODEL` | `gemini-4-argon` | placeholder id; confirm in AI Studio. Per-call overrides: `FRAME_JUDGE_MODEL`, `FRAME_RULES_MODEL`, `FRAME_NARRATE_MODEL` |
| `FRAME_CORE` | `arbcheck` | `fake` = placeholder numbers, tests only |
| `FRAME_KALSHI_MEMBER` | `0` | **Kalshi switch.** `1` only if a Kalshi member runs it on their own computer |
| `FRAME_LLM_TIMEOUT_S` | `60` | per Gemini request; a stalled call is skipped instead of hanging the scan |
| `FRAME_MAX_JUDGE_PER_SCAN` | `30` | Gemini judgements per scan |
| `FRAME_MAX_DAYS_TO_CLOSE` | `45` | rule R5 |
| `FRAME_MAX_CLOSE_GAP_DAYS` | `14` | rule R6 |
| `FRAME_MIN_SIMILARITY` | `0.45` | candidate cut-off |
| `FRAME_MONITOR_EVERY_S` | `300` | monitor cycle |
| `FRAME_EMBEDDER` | bge if installed | `hashing` skips the model download |

## The check-a-pair flow (user-driven)
Paste two links (Polymarket, Kalshi if on, Manifold) or describe two events in words. A link to a multi-outcome event checks **each outcome against its closest match** on the other side. The app asks Gemini, shows the pair for review, and after confirmation the core prices it on fresh books. Manual entry needs a Yes ask, a depth and a close time.

## Fixed during the live debugging (lessons)
| Problem | Cause | Fix |
|---|---|---|
| Scan "stuck" for 10+ minutes | **Gemini requests had no timeout**; one stalled call blocked everything | 60 s timeout, failure skips that pair |
| 888 s scan | the log re-read its whole file on every append | cached the chain tail (scan went to ~100 s) |
| 10 MB of log per scan | every candidate rewritten every scan | one row per pair |
| App crashed on start | two processes corrupted the shared log | file lock plus tolerant reader |
| "response too large" | core caps responses at 5 MB | pages of 25 that halve on error |
| Implication pairs blocked | nested deadline flagged as a blocker | rule comparison now knows the relation |
| Wrong outcome matches (Fed) | naive matching | token + synonym + one-to-one matching, with a regression test |
| Narratives with raw floats | Gemini copied unrounded numbers | numbers rounded before narrating |
| Old app after a code change | an app started earlier keeps old code | restart after pulling changes |

## Git and the vault
- Branch **`integrated/llm-and-core`** holds the LLM frame, the core and polysim together. `frame/llm-layer` is the frame alone. Never push to `main`.
- Commits so far on the integrated branch: live data by default and rules (998f661) → Gemini timeout, safe log, progress, Kalshi via `.env` (a6b6ff0) → time rules and the new result page (807d19f).
- The Obsidian notes, PDFs and screenshots are kept out of the code commits so far (they sit staged, not committed). Commit them separately when ready.

## Tests
131 frame + polysim tests (including a live Gemini test that skips without a key) and 152 core tests. Rule tests use dates relative to today, so they don't rot.

## Open questions
- Is anyone on the team a **Kalshi member**? If yes, set `FRAME_KALSHI_MEMBER=1` on that person's machine.
- Are 45 days (R5) and 14 days (R6) the right limits? Both are one-line changes.
- Confirm the Gemini model id and verify venue fee schedules and resolution dates before any number goes on a slide.
