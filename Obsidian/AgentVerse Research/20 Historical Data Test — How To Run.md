---
tags: [agentverse, evaluation, data, how-to]
created: 2026-11-08
---
# 20 · Testing on historical Polymarket data: what we found, and how to run it

Related: [[19 Recording Study — 7–8 Nov]] · [[18 Smart Loop, Sports & Test Data]] · [[17 Run, Config & Operations]] · [[16 Live Data Sources]].

## The dataset
[SII-WANGZJ/Polymarket_data](https://github.com/SII-WANGZJ/Polymarket_data), hosted on [Hugging Face](https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data). The repo is MIT; Polymarket's terms still apply.
- **About 1.27 billion trades.** We use `quant.parquet`, the cleaned file normalised to the Yes token, about 21 GB. There is also market metadata.
- **Trades only, no order books.** That is the big limitation, since our core prices executable arbitrage from book depth.
- **Coverage is uneven:**
  - about 2.5% of rows are before Jan 2025, so the **2024 US election night is nearly absent** (≈50 trades per 10 min);
  - about 85% are Jan–Sep 2026.
- **Layout, measured by us:** rows are in day order, and **within a day grouped by market**, with the markets of one event next to each other. So you can't cut a "10-minute window" cheaply, but a small contiguous chunk holds whole days of sibling markets. That is what the sampler uses.

## Sampling, not scanning
`frame/hfdata.py` never downloads the files:
- **Finding a day** takes a binary search of about 15 cheap remote lookups. Positions are cached in `data/hfdata/index.json`.
- **Reading** pulls random chunks of 150k rows from Hugging Face over HTTP: about 2–10 s each.
- **The sampling frame** is days known for volatility, plus random days as a baseline:
  - the Oct 2025 crypto crash;
  - Super Bowl LX;
  - the March 2026 Fed decision;
  - the Premier League's final day, 24 May 2026;
  - the World Cup final.
- **Scoring** is a cheap screen on trade prices, only to choose windows:
  - exclusive outcomes of one event that don't add up to $1;
  - ladder rungs out of order (an earlier date, or a harder threshold, trading above a later or easier one).

  It only counts prices traded **within 15 minutes of each other**. Without that rule, hours-old last trades dominated the ranking.
- **Export** writes an hour around a find as a recording with **trade-implied books**: taker buys become asks and taker sells become bids. That is liquidity that really traded, not the resting book.

## What we found (8 Nov)
- **Scout:** 80 candidate events from 14 chunks. The strongest trade-price "gaps" were:
  - "Next leader out of power before 2027?": outcomes adding up to 2.88 among those traded;
  - **AZ vs AFC Ajax (8 Feb 2026)**: all 3 outcomes traded within 15 min, adding up to 1.689;
  - weather "highest temperature" buckets.
- **Replayed through the core** (no LLM):
  - AZ–Ajax: 41 set checks and 157 Yes + No checks, **0 executable**.
  - "Next leader": **0 executable**.
- **Why the AZ–Ajax gap wasn't real:** in about 3 minutes the "AZ win" price fell from about 0.81 to 0.05. The scout had added prices from either side of that move. The books show you couldn't have traded every leg at once; the draw had no bids at all.
- **So:**
  - the program **correctly refuses** these trade-price "gaps", consistent with [[19 Recording Study — 7–8 Nov]];
  - **this dataset cannot prove a positive**, because without order books we can't show a historical gap was executable;
  - to see the program flag a real one, use the invented test-data example (menu 5 → 6), which plants an inversion and is flagged "worth a look", or record live in-play games with polysim.
- **Bug fixed along the way:** historical game markets also have `endDate` = kickoff, so the export now uses when the market really closed (`closedTime`), or kickoff + 2 h 15 min.

## How to run it yourself
One-time setup:
```
uv pip install --python .venv\Scripts\python.exe -r requirements-data.txt
```

**1 · Find candidate windows** (a few minutes):
```
.venv\Scripts\python.exe -m frame.hfdata scout
.venv\Scripts\python.exe -m frame.hfdata scout --random 6 --chunks 4
```
The first command covers the curated days, 3 chunks each. The second adds 6 random days at 4 chunks per day. Both print the top finds with a number (`#0`, `#1`, …) and save them all to `data/hfdata/scout.json`. Use `--seed N` for a different random sample.

**2 · Export a find as a recording:**
```
.venv\Scripts\python.exe -m frame.hfdata export 2 --minutes 60
```
This writes `recordings/hf-<time>-<event>/`, an hour centred on the strongest signal. That folder is gitignored.

**3 · Test it with the program:**
- **Core only, every minute** (no Gemini):
  ```
  .venv\Scripts\python.exe -m frame.testdata study recordings\hf-<folder>
  ```
  The report is `data/testdata/recording_study.md`. Its "Executable arbitrage moments" line is the answer.
- **One moment in the full app** (Gemini judges, you confirm):
  ```
  .venv\Scripts\python.exe -m frame.testdata polysim recordings\hf-<folder> --at 2026-02-08T15:23:00Z
  ```
  Then `run.bat` → **7** (the app on test data) or **6** (core only).

**What to look for:**
- In the study report: rows marked *worth a look* or *look closely*, and the "Closest each structure came" table.
- A big gap *before costs* with *nothing to do* after costs is the false arbitrage the product exists to catch.

## Scan checkboxes (same day)
On the Check page you now tick **sources**, **kinds of market** and **sports**:
- **Sources:** Polymarket, Kalshi (members only), Manifold (signal only), PredictIt and Betfair (not connected yet).
- **Kinds of market:** crypto, economics, politics, sports, science and tech, climate, culture, health, other.
- **Sports:** Premier League (ticked by default), Champions League, La Liga, NBA, NFL and more.

The Opportunities page has a **Sport** filter in place of the old "Premier League only" switch. Defaults come from `.env` (`FRAME_VENUES`, `FRAME_CATEGORIES`, `FRAME_SPORTS_LEAGUES`) and are used by the monitor as well.
