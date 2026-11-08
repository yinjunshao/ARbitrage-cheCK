---
tags: [agentverse, build, sports, ops]
created: 2026-11-08
---
# 18 · Smart loop, sports, test data, rules v3

Related: [[14 Built App — How It Works]] · [[15 Suggestion Rules & Log]] · [[16 Live Data Sources]] · [[17 Run, Config & Operations]]. Repo docs: `docs/FRAME.md`, `docs/RESEARCH_NOTES.md`.

## Smart loop (find, then keep listening)
- Everything checked that shows **no gap yet** goes on a **watch list**: venue sets, Yes + No checks, date ladders, in-game links and confirmed pairs. The core re-checks it with **no LLM**, more often as the deadline nears:

| time to close | > 7 days | 2–7 days | 1–2 days | 2–24 h | < 2 h or game in play |
|---|---|---|---|---|---|
| re-check | 6 h | 1 h | 20 min | 5 min | 1 min |

- A check within 1¢ of a gap halves the interval; closed items drop off.
- **Monitor** (`run.bat` → 3): a full scan for new candidates every 5 min, and due re-checks every 30 s.
- **App**: the same loop runs in the background. A hit appears as an alert at the top of Opportunities and as `watch_hit` in the log.
- Kalshi prices are never written to the watch file.

## Date ladders ("by Nov 8" vs "by Nov 9")
- **Logically a link, practically rarely an arbitrage.** The earlier date can't be worth more. The only arbitrage is a **price inversion**: buy No on the earlier date plus Yes on the later one for under $1.
- The price gap normally reflects "it happens exactly on Nov 9", so ladders are normally consistent. Every live ladder we priced came back "nothing to do".
- The main Polymarket arbitrage study does **not** cover ladders: it pairs only markets with the same end date and leaves this as future work.
- **Change:** ladders no longer use the Gemini budget. The core screens them; only an inversion goes to Gemini and a human, and the rest are watched.
- The invented test-data example plants an inversion, and the core flags it "worth a look" (4.2¢ after costs).
- **Measured on our own recording** (polysim, 151 markets, 11 ladder events, 7–8 Nov, 46 snapshots; `python -m frame.testdata ladder-study <folder>`):
  - **21,911 ladder checks with no genuine arbitrage after costs.** This includes **Kai Cenat: 1,862 checks, 0 hits.**
  - 748 small inversions appeared *before* costs; spreads and fees removed every one.
  - ⚠️ **The core itself reported a false arbitrage:** on WTI "hit (LOW) $X" it ordered downward thresholds as if upward, giving "≈80¢ locked in" 1,636 times.
  - The frame now blocks these, and the fix is requested from Debby (`docs/CORE_REQUESTS.md` #6). This makes a strong demo story: the checker caught a false arbitrage in its own engine.

## Sports (Premier League first)
- **Why sports:** an NBA study on Polymarket (Cheng et al., arXiv:2605.00864) found 279 of 290 executable episodes **in-game**, mostly in the final minutes, with a median life of **16 s**.
- **Honest limit:** our 60 s polling can find and explain such gaps, but not capture them; that needs WebSockets.
- Games are pulled separately (`FRAME_SPORTS_LEAGUES=premier-league`, the next 3 days). A live run found 5 games, 335 markets and 180 links.
- **Exact score ⇒ result** links are derived by code, not the LLM, and screened by the core.
- **Bug found live:** Polymarket sets a game's `endDate` to kickoff, but games trade in play. A game now counts as open until kickoff + 2 h 15 min.
- **Live state:** Polymarket's own game events carry `live`, `score` and `elapsed`. Optional extra feeds:
  - football-data.org: free key, but **delayed** scores;
  - ESPN's scoreboard: unofficial, with no published terms.
- Opportunities page: a games panel, a "Premier League only" switch, and LIVE badges.

## Smarter sorting
The default is **Smart**: a score with its reasons on hover. In order of weight:
1. The core's verdict and edge.
2. How soon it resolves; a game in play counts most.
3. Gemini's confidence, verified quotes, and whether any rule difference blocks it.
4. The type (cross-platform first).
5. Watched items that came close before.

These weights only order the list; verdict numbers still come only from the core.

## Candidate cards
- Each side now shows **only the sentences that differ**, usually the date or threshold.
- The shared rules appear once, in a collapsed block.
- Markets with no prices yet say so instead of "None/None".

## run.bat menu
Double-click it for a numbered menu:

| # | Mode |
|---|---|
| 1 | App |
| 2 | App with Kalshi |
| 3 | Monitor |
| 4 | Monitor with Kalshi |
| 5 | Convert test data |
| 6 | Core on test data |
| 7 | App on test data |
| 8 | Tests |
| 9 | The core's own app |
| 0 | Exit |

## Test-data mode
- Drop downloaded order books (JSON, JSONL or CSV) into `data/testdata/raw`.
- **Gemini maps the layout only** (which field is the price, the size, the close time; cents or dollars). Code copies every number and rejects bad rows: prices outside 0–1, crossed books, no close time.
- The mapping is cached per file, so the second run uses no Gemini.
- **Option 6 runs the core with no Gemini at all.** It prices outcome sets, Yes + No, ladders and in-game links, and writes `data/testdata/report.md`.

## Polymarket sandbox for judging?
- There is **no official Polymarket sandbox or testnet**. Polymarket US has one for approved partners only, and Kalshi has a demo environment.
- The GitHub "sandboxes" are community paper traders on live data.
- **Recommendation:** read live Polymarket directly and execute only on paper via our own polysim. For a demo that can't be spoiled by a quiet market, use test-data mode on a recorded snapshot.

## Rules review (subagent) → rules v3
| Finding | Fix |
|---|---|
| R5 let already-closed markets through | Refused (R5) |
| R6 depended on argument order (whole-day rounding) | Exact time |
| R6 refused genuine nested-deadline links | R6 is for same-question pairs only; ladders are screened |
| Many tags/categories unmapped, first match only | Tags added; R3 matches any shared category |
| A silent 7-day candidate window overrode R6 | Window = R6's 14 days |
| A time-zone crash in candidate dates | Fixed |
| No check for the same market twice or a settled price | New R0 |

**Not changed** (your call):
- A Polymarket event tagged politics + bitcoin counts as crypto, so R2 refuses its Kalshi politics twin. This keeps the "crypto only with crypto" rule.
- Kalshi **titles and ids** are kept in the suggestion log; prices never are. Whether titles count as "stored Kalshi data" is unclear.
