---
tags: [agentverse, evaluation, data]
created: 2026-11-08
---
# 19 · Recording study: was there any arbitrage on 7–8 Nov?

Related: [[18 Smart Loop, Sports & Test Data]] · [[16 Live Data Sources]] · [[17 Run, Config & Operations]] · [[14 Built App — How It Works]].

> **Data is private.** The recording's README says Polymarket's terms don't allow redistributing their data. This note holds **counts and conclusions only**. The full report, with prices, is `data/testdata/recording_study.md`, which is gitignored like `recordings/`.

## The recording, organised
- Moved from `polysim-recordings-20261108-1127Z/polysim-recordings-20261108-1127Z/`, which was nested twice, to **`recordings/polysim-20261108-1127Z/`**. That is where its README says it belongs, and the folder is gitignored. The original zip is untouched.
- **Contents:**
  - Polymarket order books (top 20 levels) for **151 markets** in **11 events**, both the Yes and the No token, about once a minute;
  - the raw Gamma events, with rules text and tags;
  - **18 resolutions**;
  - the recorder log.
- **Events:**
  - Bitcoin price ladders: on Nov 7, Nov 2–8, in November, before 2027, and above on Nov 8;
  - WTI in November;
  - Kai Cenat GTA marathon "by" dates;
  - Anthropic IPO "by" dates;
  - Puffpaw FDV;
  - Israel–Iran ceasefire;
  - the Iranian blockade.
- **Coverage:** **178 snapshots.** About 2.5 h continuous on 7 Nov (19:12–21:43 UTC) and about 0.5 h on 8 Nov (10:58–11:27), so **~3 h of usable data**. The laptop slept in between, leaving only scattered snapshots, which the study discards as stale.

## How it was tested (core only, no LLM)
`python -m frame.testdata study recordings/polysim-20261108-1127Z`. Every snapshot was rebuilt and priced by the core:

| Structure | How the pairs were found | Checks |
|---|---|---|
| Ladders inside one event (dates, thresholds) | the core's own extractor, 493 pairs | 77,493 |
| …of which downward thresholds ("hit (LOW)") | guarded: the core orders them backwards (CORE_REQUESTS #6) | 6,205 blocked |
| **Nested windows across events** (new): "reach $90k Nov 2–8" ⇒ "in November" ⇒ "by Dec 31" | derived by code (`frame/windows.py`), 23 links | 3,672 |
| Same market, Yes + No | each market, with the **real recorded No book** | 24,899 |

**Honest-data safeguards added during this study:**
1. **A snapshot is a recorder round.** The first reader merged rounds and saw 46; there are 178.
2. **A book older than 180 s is not used.** Laptop sleep made some snapshots mix fresh and hours-old books.
3. **The real No book is used only if it was recorded within 5 s of the Yes book.** The first pass found 106 "Yes + No under $1" moments. Every one paired books recorded 5 s or more apart, often minutes. Books recorded within 5 s of each other never showed a gap, so those moments were recording artefacts.

## Result
**No executable arbitrage at any moment in the recording. The core never said "worth a look" or "look closely".**

| Structure | Checks | Executable | "Gap" a price-only scanner would see |
|---|---|---|---|
| Ladders inside one event | 77,493 | 0 | 3,117 |
| Nested windows across events | 3,672 | 0 | 0 |
| Same market (Yes + No) | 24,899 | 0 | 4,101 |

- **What the last column means:**
  - **The pitch in one line:** a naive scanner comparing prices would have flagged about **7,200 moments**. The core's fills, fees and depth showed **none** were tradeable, and that is the false-arbitrage problem we solve.
  - **Ladders (3,117):** the gap is at mid prices.
  - **Yes + No (4,101):** the mids add up to slightly under $1, from tick rounding. That is not tradeable.
- **Closest calls:**
  - A Puffpaw FDV threshold pair showed 3.5¢ apart at mid and about 1¢ at the top of the book. The core found **0 executable shares** after fees and the minimum order size.
  - Kai Cenat date ladders were never more than a fraction of a cent from consistent.
- **Kai Cenat "by Nov 8 / Nov 9 / …":** consistent the whole time, which confirms note 18's conclusion on ladders.
- **The WTI "(LOW)" ladders:** without the guard, the core would have reported large "locked-in" profits there. With it they are blocked. This is the false arbitrage inside the core (CORE_REQUESTS #6).
- **Resolutions:** 18 markets resolved during the recording. Mostly Nov 7 Bitcoin day-markets resolved "No", plus Kai Cenat "by Nov 7" (No) and Bitcoin "reach $76k Nov 2–8" (Yes). Their last recorded prices were already near 0 or 1, or consistent with the outcome. There was no visible resolution-lag gap, but the laptop-sleep gap covers most resolution times, so this is not conclusive.

## What it means for the pitch
1. **On this sample, real arbitrage was absent and fake arbitrage was everywhere.** That's the product's point, now shown on our own data.
2. **The checker caught a false arbitrage in its own engine:** the downward-threshold bug.
3. **Where to look instead:** the research points to **live sports in the final minutes**, where a Polymarket NBA study found 279 of 290 episodes in-game. Next, record a Premier League match day with polysim and replay it the same way.

## How to repeat
- **The whole recording:** `python -m frame.testdata study recordings/<folder>`, report in `data/testdata/recording_study.md`.
- **One moment:**
  1. `python -m frame.testdata polysim recordings/<folder> --at 2026-11-07T20:30:00Z`
  2. `run.bat` → 6 (core only) or 7 (the app on that moment).
- **A new recording:** `polysim record` (see `polysim/README`), then the same commands.
