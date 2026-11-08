---
tags: [agentverse, build, app]
created: 2026-11-07
---
# 14 · The built app: how it works

> Status as of Sat 7 Nov 2026, branch `integrated/llm-and-core` (LLM frame + Debby's numeric core + polysim, together). Follows the brief in [[13 Claude Code Handoff — LLM Frame]] and the design in [[12 Architecture & Stack]]. Rules: [[15 Suggestion Rules & Log]] · data: [[16 Live Data Sources]] · running it: [[17 Run, Config & Operations]].

## One-paragraph summary
The app scans **live** Polymarket (and Kalshi, if switched on) for related markets, throws out pairs that are not allowed or not near their deadline, asks Gemini whether the rest are logically related, lets a **human confirm**, then hands the confirmed relation to the **numeric core** which prices it on real order books. The LLM never produces a number that ends up in a verdict; a plain-English explanation is written by the LLM but every figure in it must trace back to the core's output.

## Pipeline (fixed, code-orchestrated)
```
live venues ─► candidate pairs ─► suggestion rules R1–R6 ─► Gemini judge ─► Gemini rule-compare
   (9 s)          (~17 s)            refusals logged           (30/scan)        (quotes verified)
                                                                                      │
                          human confirms the relation ◄───────────────────────────────┘
                                         │
                                         ▼
                    core prices it (order-book walk, fees, guard) ─► Gemini narrates ─► human approves (paper)
```
Three LLM calls only: `judge_pair`, `compare_rules`, `narrate`. Everything else is code.

## The hard rules (never relaxed)
1. **LLM interprets, code computes.** `ground.py` rejects any narrative number not present in the core result and falls back to a template.
2. **Quotes are verified.** Evidence quoted by Gemini must appear verbatim in the market text, otherwise the verdict is downgraded to "unclear".
3. **The frame can only tighten a verdict, never loosen it** (rule differences can turn "worth a look" into "look closely" or "blocked", never the reverse).
4. **A human confirms every relation** before the core prices it. Pairs the user types in are always shown for review.
5. **Paper only.** Nothing places an order. No keys other than the Gemini key; `.env` is gitignored.
6. **Never edit core files.** Requests to Debby go in `docs/CORE_REQUESTS.md` (5 so far).
7. **Manifold is never a leg**, and **Kalshi data is never stored or shared** (see [[16 Live Data Sources]]).

## Where the code lives (all under the repo root)
| Area | Files |
|---|---|
| LLM frame | `frame/` — `judge.py`, `rulediff.py`, `narrate.py`, `ground.py`, `llm.py` (Gemini client, 60 s timeout, one retry), `fewshot.py`, `discover.py` |
| Rules and log | `frame/rules.py`, `frame/categories.py`, `frame/suggestions.py`, `frame/audit.py` |
| Candidates | `frame/candidates.py` (within-event, cross-event, cross-venue) |
| Orchestration | `frame/pipeline.py`, `frame/monitor.py` |
| Core connection | `frame/adapter.py` — **the only file that imports the core** (`arbcheck-project/`) |
| Core | `arbcheck-project/` (Debby's, stdlib only) |
| Paper trading | `polysim/` (simulator, risk limits, kill switch, `evaluate`) |
| UI | `app_frame/main.py` (NiceGUI) |
| Docs | `docs/` — `FRAME.md`, `CORE_MAP.md`, `CORE_REQUESTS.md`, `COMPLIANCE.md`, `PROTOCOL.md`, `DATA_SOURCES.md` |

## Screens
- **Check** (`/`): "Scan venues" button with live progress and a spinner (one scan at a time); paste two links or words to check a pair; manual entry with depth.
- **Candidates** (`/candidates`): what the scan surfaced, with category, how it was found, and any rule warning.
- **Result** (`/result/…`): see below.
- **Suggestions** (`/suggestions`): the full log with hash-chain check and filters. Details in [[15 Suggestion Rules & Log]].
- **Log** (`/log`): the audit trail of every LLM call and decision.

### The result page: short by default, detail on demand
Top of the page is a plain-English report:
- verdict in words — *Worth a closer look* / *Possible gap, with risks to check* / *Do not trade this* / *No profit here*;
- the two markets and how they relate ("these are the same event");
- **In short**: what you could make on what size over how many days;
- **Why the gap exists**, **What could go wrong** (top 3), **Check before approving**;
- Approve (paper) / Review later / Kill.

Below it, one collapsed section — **"Detailed report: statistics, checks and workings"** — opened with an arrow:
1. where the edge goes per share (mid → best ask → after depth and fees → after the rule-risk haircut);
2. size and cost (shares, cost, profit, fees, days, annualised return);
3. the guard checks;
4. payoff in every still-possible world;
5. how the explanation was produced and how Gemini judged the pair.

## Update 8 Nov: what changed since this note was written
Full detail in [[18 Smart Loop, Sports & Test Data]] and [[19 Recording Study — 7–8 Nov]].
- **Pipeline now has three lanes:**
  1. Code-derived logical pairs (date and threshold ladders, exact score ⇒ result) are **screened by the core with no LLM**. Only a real gap goes to Gemini and a human.
  2. Venue sets and Yes + No are priced by the core directly.
  3. Everything else goes through Gemini and a human, as before.
- **Watch list and smart loop:** anything checked with no gap yet is re-checked by the core as its deadline nears, every minute in the last 2 h or while a game is in play.
- **Premier League games** are pulled separately; live state comes from Polymarket's own game data.
- **Smart sort:** a priority score with reasons shown on hover. Candidate cards show only the rule sentences that differ.
- **Test-data mode:** Gemini maps the file layout; code copies the numbers; the core runs with no Gemini. A whole polysim recording can be replayed.
- **A guard for a false arbitrage in the core:** downward-threshold ladders ("(LOW)") are blocked (CORE_REQUESTS #6).
- New code: `frame/watch.py`, `frame/sports.py`, `frame/priority.py`, `frame/testdata.py`.

## Arbitrage types (named as the core names them)
| Type | What | How it is found |
|---|---|---|
| **Cross-platform** | same or linked event on two venues | candidates → Gemini → human confirms → core |
| **Logical link** | one outcome forces another (nested deadlines, thresholds) | the same |
| **Multi-outcome set** | outcomes of one event that can't all win | venue-declared sets, priced by the core, **no LLM** |
| **Same market (Yes + No)** | Yes + No of one market under $1 | ~30 markets per scan (needs real books), priced by the core, **no LLM** |

The last two need no confirmation, so they show up at once as "priced from structure alone" with a template explanation. The **Opportunities page** filters by type / category / confidence / source and sorts suggestions (confidence, similarity, closing soonest, category, type) and priced results (verdict, edge after costs, price-only gap, category).
**Balanced judging:** the Gemini budget (30 a scan) is shared across categories, cross-venue pairs first, so crypto (the biggest category) can't take it all. First live check: results spread over economics, sports, politics, culture, science and weather.
**Loading:** slow steps (pricing with the core, checking a pair) open a "working" dialog with the live step and elapsed seconds. Finding: the core itself is quick; most of the wait was Gemini writing the explanation.

## How candidates are found
- **Within one event**: only pairs the core's own heuristic links as *implies* (nested deadlines, thresholds). Outcomes of one event are not sent in pairs.
- **Venue-declared exclusive sets** (Polymarket `negRisk`, Kalshi `mutually_exclusive`) are priced directly by the core as "at most one", **no LLM needed**.
- **Across events and venues**: nearest neighbours by a hashing embedder (optional bge-small), minimum similarity 0.45.
- **Outcome matching** for multi-outcome links (e.g. Fed December on both venues): one-to-one greedy match on title tokens with synonyms (hike/increase → up, cut/decrease → down, maintain/no → hold) and a "more than" marker.
- A **budget of 30 Gemini judgements per scan**; within-event pairs first, then by similarity. Cached verdicts are free.

## What has been checked, and what has not
Checked:
- Live run: 3,499 markets (Polymarket + Kalshi), a scan in about 2 minutes; Bitcoin threshold pairs surfaced; the user's Fed December links paired outcome by outcome and priced (about 1¢ gap, eaten by spreads: nothing to do).
- One case recomputed by hand matched the core. 131 frame + polysim tests pass, plus 152 core tests.

**Not verified** (say so in the pitch):
- venue fee schedules against the venues' own pages;
- Polymarket `endDate` versus the true resolution time (it drives days and annualised return);
- no real positive arbitrage has been seen yet;
- the full chain from the Gemini judgement to the core was only partly exercised end to end on live data;
- the "gemini-4-argon" model id is a placeholder to confirm in AI Studio.

## Known limits
- The backlog of unjudged candidate pairs is large (≈4,900 after the time rules) against 30 judgements per scan; it clears over many monitor cycles.
- Kalshi is off unless the user is a member (see [[17 Run, Config & Operations]]).
- Result-page wording and layout were checked in a browser on test data, not yet on a live result.
