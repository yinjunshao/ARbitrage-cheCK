---
tags: [agentverse, build, ops]
created: 2026-11-07
---
# 17 · Run, configure and operate

Related: [[14 Built App — How It Works]] · [[15 Suggestion Rules & Log]] · [[16 Live Data Sources]].

## Running it (Windows, from the repo root)
**Double-click `run.bat`** for a numbered menu; it shows what is already running and pauses at the end so the window stays open.

**What makes it smarter** (`run.bat` + `scripts/launcher.py`, updated 8 Nov):
- **One copy at a time:** `app` first stops any old copy of the app or monitor, so a stale server can never answer on your port.
- **Free port:** it uses 8080, or the next free port if something else holds it, and prints the address.
- **Tests only when needed:** it re-runs them only if the code changed since they last passed. `run.bat test` always runs them. Otherwise the app starts at once.
- **Browser opens when ready:** it waits until the server answers, then opens the Board.
- **Automatic set-up** (`.venv`, packages, `.env`) that does nothing when everything is in place.
- **`S` in the menu, or `run.bat stop`**, stops our servers; `run.bat status` lists them. They only ever touch the app, monitor, the core's app and the `run.bat` windows that launched them.

| # | Mode | Gemini? | Mode name for `run.bat <mode>` |
|---|---|---|---|
| 1 | **App on LIVE markets**, opens the Board, http://127.0.0.1:8080 | judging and explanations | `app` |
| 2 | App, Kalshi on for this run (members only) | yes | `kalshi` |
| 3 | Monitor: the smart watch loop, no browser | for new pairs | `monitor` |
| 4 | Monitor with Kalshi | yes | `monitor-kalshi` |
| 5 | Test data: convert files in `data/testdata/raw` | maps the layout only | `testdata-convert` |
| 6 | Test data: the core on the converted data | **no** | `testdata-core` |
| 7 | Test data: the app on the converted data | judging and explanations | `testdata-app` |
| 8 | Every test | no | `test` |
| 9 | The core's own app, http://127.0.0.1:8765 | no | `core` |
| S | **Stop all servers** | no | `stop` (also `status`, `help`) |

`run.bat` prints whether Kalshi is on or off. Needs a Google AI Studio key in `.env`.

### Real data, by default
- **No demo data in the app.** It scans **live** markets by itself: once at start (unless the saved scan is under 15 minutes old), then every 15 minutes (`FRAME_AUTOSCAN`, `FRAME_AUTOSCAN_EVERY_S`).
- **The Board fills fast:** the scan has a **quick lane** that reads only the game markets first (Novig and Polymarket's games of the ticked sports) and prices their outcome sets and same-match pairs at once; the full discovery, structure, screens and Gemini follow. Real rows appear within about a minute. A "● SCANNING LIVE" line shows the current step and a "● LIVE DATA · last full scan N min ago" line shows the age.
- **Saved scan:** the last scan is kept in `data/frame/board_snapshot.json`, so the Board isn't empty after a restart. Those rows are marked "saved" with their real age and are replaced when the new scan finishes. **Kalshi rows are never saved.** The file is local and gitignored.
- **Invented markets** exist only inside the tests and the explicit test-data mode, which shows a red "TEST DATA" banner. The old offline design preview script was deleted.

### Test-data and recording commands (no Gemini unless noted)
| Command | What it does |
|---|---|
| `python -m frame.testdata sample` | writes an invented example file into `data/testdata/raw` |
| `python -m frame.testdata convert` | converts files in `data/testdata/raw`; Gemini maps unknown layouts once, and the mapping is cached |
| `python -m frame.testdata core` | the core on the converted data → `data/testdata/report.md` |
| `python -m frame.testdata polysim recordings/<folder> [--at ISO]` | one moment of a polysim recording as test data (then menu 6 or 7) |
| `python -m frame.testdata study recordings/<folder>` | replays the **whole** recording through the core → `data/testdata/recording_study.md` (see [[19 Recording Study — 7–8 Nov]]) |
| `python -m frame.testdata ladder-study recordings/<folder>` | the earlier, ladder-only version |
| `python -m frame.hfdata scout [--random N --chunks K]` | samples the historical Hugging Face dataset for candidate windows (see [[20 Historical Data Test — How To Run]]) |
| `python -m frame.hfdata export <find #>` | turns a scout find into a recording (trade-implied books) for `study` |

Recordings live in `recordings/`, which is gitignored. Polymarket data may not be redistributed, so never commit or post them.

## `.env` settings (copy `.env.example`; never commit `.env`)
| Key | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | Google AI Studio key |
| `GEMINI_MODEL` | `gemini-4-argon` | placeholder id; confirm in AI Studio. Per-call overrides: `FRAME_JUDGE_MODEL`, `FRAME_RULES_MODEL`, `FRAME_NARRATE_MODEL` |
| `FRAME_CORE` | `arbcheck` | `fake` = placeholder numbers, tests only |
| `FRAME_KALSHI_MEMBER` | `0` | **Kalshi switch.** `1` only if a Kalshi member runs it on their own computer |
| `FRAME_LLM_TIMEOUT_S` | `60` | per Gemini request; a stalled call is skipped instead of hanging the scan |
| `FRAME_MAX_JUDGE_PER_SCAN` | `30` | Gemini judgements per scan |
| `FRAME_MAX_SINGLES_PER_SCAN` | `30` | "Yes + No under $1" checks per scan (2 book requests each, no LLM) |
| `FRAME_MAX_DAYS_TO_CLOSE` | `45` | rule R5 |
| `FRAME_MAX_CLOSE_GAP_DAYS` | `14` | rule R6 |
| `FRAME_MIN_SIMILARITY` | `0.45` | candidate cut-off |
| `FRAME_MONITOR_EVERY_S` | `300` | monitor cycle |
| `FRAME_EMBEDDER` | bge if installed | `hashing` skips the model download |
| `FRAME_AUTOSCAN` | `1` | the app scans live data by itself (`0` = only when you press Scan now) |
| `FRAME_AUTOSCAN_EVERY_S` | `900` | how often it rescans |
| `FRAME_MAX_SCREENS_PER_SCAN` | `40` | ladders and in-game links screened by the core per scan (no LLM) |
| `FRAME_WATCH_TICK_S` | `30` | how often the watch loop looks for due re-checks |
| `FRAME_APP_WATCH` | `1` | `0` turns the app's own background watch off (when the monitor runs) |
| `FRAME_DATE_WINDOW_DAYS` | = R6 (14) | cross-event candidate window |
| `FRAME_SPORTS_LEAGUES` | `premier-league` | default ticked sports (Polymarket tag slugs) |
| `FRAME_VENUES` | `polymarket,kalshi` | default ticked sources |
| `FRAME_CATEGORIES` | `all` | default ticked kinds of market, e.g. `crypto,sports,politics` |
| `FRAME_SPORTS_DAYS` | `3` | how far ahead games are pulled |
| `FOOTBALL_DATA_API_KEY` | — | optional football-data.org feed (free tier = delayed) |
| `FRAME_SPORTS_ESPN` | `0` | optional ESPN scoreboard (unofficial, personal use) |

## The check-a-pair flow (user-driven)
Paste two links (Polymarket, Kalshi if on, Manifold) or describe two events in words. A link to a multi-outcome event checks **each outcome against its closest match** on the other side. The app asks Gemini, shows the pair for review, and after confirmation the core prices it on fresh books. Manual entry needs a Yes ask, a depth and a close time.

## Fixed during the live debugging (lessons)
| Problem                                                | Cause                                                                   | Fix                                                                             |
| ------------------------------------------------------ | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Scan "stuck" for 10+ minutes                           | **Gemini requests had no timeout**; one stalled call blocked everything | 60 s timeout, failure skips that pair                                           |
| 888 s scan                                             | the log re-read its whole file on every append                          | cached the chain tail (scan went to ~100 s)                                     |
| 10 MB of log per scan                                  | every candidate rewritten every scan                                    | one row per pair                                                                |
| App crashed on start                                   | two processes corrupted the shared log                                  | file lock plus tolerant reader                                                  |
| "response too large"                                   | core caps responses at 5 MB                                             | pages of 25 that halve on error                                                 |
| Implication pairs blocked                              | nested deadline flagged as a blocker                                    | rule comparison now knows the relation                                          |
| Wrong outcome matches (Fed)                            | naive matching                                                          | token + synonym + one-to-one matching, with a regression test                   |
| Narratives with raw floats                             | Gemini copied unrounded numbers                                         | numbers rounded before narrating                                                |
| Old app after a code change                            | an app started earlier keeps old code                                   | restart after pulling changes                                                   |
| Games vanished at kickoff                              | Polymarket sets a game's `endDate` = kickoff                            | game open until kickoff + 2 h 15 min                                            |
| Same 40 ladders screened every scan                    | screening budget spent on already-watched pairs                         | watched pairs left to the watch loop                                            |
| R6 depended on argument order                          | whole-day rounding                                                      | exact time                                                                      |
| Core reported 80¢ "locked in" on WTI "(LOW)" ladders   | the core's extractor orders downward thresholds backwards               | frame blocks them; CORE_REQUESTS #6                                             |
| Garbled "…" in `pipeline.py`                           | a PowerShell rewrite read UTF-8 as ANSI                                 | repaired; edit files with Python or the editor, not `Get-Content`/`Set-Content` |
| Recording replay merged snapshots (46 instead of ~170) | rounds split on a 20 s gap that never happens                           | a round ends when a token comes round again                                     |

## Git and the vault
- Branch **`integrated/llm-and-core`** holds the LLM frame, the core and polysim together. `frame/llm-layer` is the frame alone. Never push to `main`.
- Commits on the integrated branch:

| Commit | What |
|---|---|
| 998f661 | live data by default, rules |
| a6b6ff0 | Gemini timeout, safe log, progress, Kalshi via `.env` |
| 807d19f | time rules and the new result page |
| ca1bf0d | vault notes 14–17 |
| 4e1fbba | arbitrage types, balanced judging, sorting, loading dialog |
| 32d181a | smart watch loop, Premier League, test-data mode, rules v3, the guard for the core's false arbitrage |

- Code commits include the vault notes written for them. Your own staged files (PDFs, screenshots, other notes, `00 Home`) stay staged and uncommitted until you commit them.

## Tests
150 frame + polysim tests (including a live Gemini test that skips without a key) and 152 core tests. Rule tests use dates relative to today, so they don't rot.

## Open questions
- **Kalshi:** `.env` now has it ON. Make sure that machine belongs to a Kalshi member.
- Are 45 days (R5) and 14 days (R6) the right limits? Both are one-line changes.
- Confirm the Gemini model id and verify venue fee schedules and resolution dates before any number goes on a slide.
- Should the core's direction bug (CORE_REQUESTS #6) be fixed by Debby, or on a branch for her review?
- R2 keeps a Bitcoin-tagged politics event away from a Kalshi politics market: intended?
