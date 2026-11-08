# Research notes: date ladders, a Polymarket sandbox, live sports data

Researched 2026-11-08. Third-party claims are flagged; verify before quoting on a slide.

## 1. Do "same event, different date" pairs qualify for arbitrage? (e.g. Kai Cenat finishes by Nov 8 / by Nov 9)

**Logically yes, practically rarely.** "By Nov 8" implies "by Nov 9", so the earlier date can never be worth more:
P(by Nov 8) ≤ P(by Nov 9). The only arbitrage is a **price inversion**: if the earlier date's Yes bid is above the
later date's Yes ask (by more than fees), buying *No on Nov 8 + Yes on Nov 9* costs under $1 and pays at least
$1 in every outcome (and $2 if it happens exactly on Nov 9).

- The gap between the two prices is the market's estimate that it happens *exactly on Nov 9*; it is normally
  positive, so ladders are normally consistent. Every live ladder we priced (Iran blockade, Rubio, BTC thresholds)
  came back "nothing to do".
- The main Polymarket arbitrage study (Saguillo et al., AFT 2025, ~$40M extracted over a year) **does not study
  date ladders**: it pairs markets only when they share an end date, and names temporal dependencies as future work.
  Its combinatorial (cross-market) arbitrage was small: 13 dependent pairs found, value extracted in 5, about $95K.
- Real risk in ladders is **wording, not price**: a clarification can break the implication (the Strategy BTC
  case in note 09), and two markets created on different days can measure different windows (Gemini flagged
  exactly this on the Rubio ladder).

**Our own data agrees.** We replayed the team's polysim recording (Polymarket books for 151 markets in 11 ladder
events, 2026-11-07 19:12 to 2026-11-08 11:27 UTC, 46 rounds) through the core with no LLM:
`python -m frame.testdata ladder-study <folder>`.
- **21,911 ladder checks, 493 distinct pairs: no genuine ladder was ever an arbitrage after costs.**
  That includes the Kai Cenat "finishes by" ladder: 1,862 checks, zero hits.
- 748 moments showed a small price inversion *before* costs; spreads and fees always removed it.
- The study also exposed a **false arbitrage in the core itself**. On the WTI "hit (LOW) $X" ladder the core's
  extractor ordered the thresholds as if upward, and reported up to ~80¢ of "locked-in profit" in 1,636 checks.
  The frame now blocks such ladders (`threshold_direction_unverified`), and the fix is requested from the core
  owner (`docs/CORE_REQUESTS.md` #6). This is a live example of exactly what the product exists to catch.

**Full replay (later the same day):** `python -m frame.testdata study recordings/<folder>`.
- **What it covered:** 178 snapshots (about 3 h of usable data). It added nested windows across events, derived by code: "reach $90k Nov 2–8" ⇒ "in November" ⇒ "by Dec 31". It used real Yes and No books, and dropped stale or non-simultaneous books.
- **Result: 0 executable moments.** That covers 77,493 ladder checks, 3,672 cross-event window checks and 24,899 Yes + No checks.
- A price-only scanner would have flagged about 7,200 moments.
- An earlier 106 "Yes + No under $1" moments were all books recorded ≥ 5 s apart, so recording artefacts.

**What the app does now:** ladders are not sent to Gemini by default. The core screens them with its own
deterministic extractor (no LLM); only an inversion goes to Gemini and a human. Every ladder joins the watch list
and is re-screened more often as the earlier date approaches, which is when an inversion is most likely.

## 2. Polymarket "sandbox" for the judging demo

- **No official Polymarket sandbox or testnet** for the main exchange was found. Guides say to test with tiny
  real orders, which this project never does. The official CLOB clients accept the Amoy testnet chain id, but we
  did not verify that any Amoy order books are live.
- **Polymarket US** runs a sandbox, but only for approved participants and partners.
- **Kalshi** has a public demo environment (`demo-api.kalshi.com`).
- The "sandbox" repos on GitHub are **community paper traders on live data**, for example
  `agent-next/polymarket-paper-trader` and `mjunaidca/polymarket-skills` (unaudited by us).

**Recommendation for judging:** read **live Polymarket data directly** (what the app does, read-only and allowlisted)
and keep execution paper-only through **our own polysim**, which already has fees, risk limits, a kill switch and an
audit log. For a deterministic demo that cannot be spoiled by a quiet market, use the **test-data mode**
(`run.bat` → 5/6/7) on a recorded or downloaded snapshot. A third-party sandbox would add a dependency with
unknown fill logic and give nothing the judges can verify.

## 3. Live sports data (Premier League)

- **Polymarket's own game events are the primary source**: each Premier League game has `startTime` (kickoff),
  `gameId`, and `live` / `score` / `period` / `elapsed` fields, plus linked events (result, half-time result,
  second-half result, exact score). Same allowlisted host, no new dependency.
- **football-data.org** (official, free key): Premier League fixtures and tables; the **free tier is delayed**,
  live scores need a paid plan (third-party sources; check the pricing page). Set `FOOTBALL_DATA_API_KEY`.
- **ESPN scoreboard JSON** (`site.api.espn.com/.../soccer/eng.1/scoreboard`): live, with goal/card details, but
  **unofficial, no published terms**; off unless `FRAME_SPORTS_ESPN=1`, personal use only.
- **TheSportsDB**: livescores need a paid key.

**Why sports matter here:** Cheng et al. (arXiv:2605.00864, Polymarket NBA) found 290 executable combinatorial
arbitrage episodes, **279 in-game**, concentrated in the final minutes, median life **16 seconds**, median yield
about 101 bps (Polymarket charged no NBA fees then). Our polling (every 60 s while a game is in play) can find and
explain such gaps but cannot capture 16-second ones; that would need the venues' WebSocket feeds.

## Sources
- [Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets (arXiv 2508.03474)](https://arxiv.org/html/2508.03474)
- [Arbitrage Analysis in Polymarket NBA Markets (arXiv 2605.00864)](https://arxiv.org/html/2605.00864v1)
- [Executable Arbitrage and Market Efficiency in Prediction Markets (arXiv 2608.00666)](https://arxiv.org/pdf/2608.00666) (not read: PDF text unavailable)
- [polymarket-paper-trader](https://github.com/agent-next/polymarket-paper-trader) · [polymarket-skills](https://github.com/mjunaidca/polymarket-skills) · [py-clob-client-v2](https://github.com/Polymarket/py-clob-client-v2)
- [Polymarket US developer resources](https://www.polymarketexchange.com/developers.html) · [Polymarket API guide (Dwellir)](https://www.dwellir.com/guides/polymarket-api-guide)
- [football-data.org free tier limits (third party)](https://www.thestatsapi.com/blog/football-data-org-free-tier-limits-2026) · [ESPN hidden API guide (third party)](https://zuplo.com/blog/2024/10/01/espn-hidden-api-guide) · [TheSportsDB (third party)](https://sportsapis.dev/apis/thesportsdb)
