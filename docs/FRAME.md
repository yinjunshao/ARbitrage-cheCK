# False-Arb Checker on live data: LLM frame (Gemini) + numeric core (arbcheck) + polysim

```
 live venues (arbcheck read-only client)          frame                                       arbcheck core
 discover ──► candidates ──► SUGGESTION RULES ──┬─► ladders / in-game links ──► core SCREEN (no LLM) ──► watch list
 top events   within-event   R0 same/settled/   │       only a price inversion ──┐                       (smart loop)
 by volume +  ladders,       closed             │                                ▼
 Premier      cross-event,   R1 no Manifold     └─► other pairs ──► Gemini judge ──► compare_rules ──► [HUMAN CONFIRM]
 League games cross-venue,   R2 crypto/same coin                                                        ──► evaluate_case ──► narrate
              game links     R3 shared category      venue sets + Yes/No of one market ──► core (no LLM) ──► watch list
                             R4 category known
                             R5 closes ≤ 45 days     every step ──► data/frame/suggestions.jsonl (hash chain)
                             R6 same-question pairs
                                close ≤ 14 days apart
```

## Run (Windows)

Double-click `run.bat` for a numbered menu:

| # | Mode | Gemini? |
|---|---|---|
| 1 | App on live markets (tests first) | judging + explanations |
| 2 | App, Kalshi on for this run (members only) | yes |
| 3 | Monitor: the smart watch loop, no browser | yes, for new pairs |
| 4 | Monitor with Kalshi | yes |
| 5 | Test data: convert downloaded files in `data/testdata/raw` | maps the file layout only |
| 6 | Test data: the core on the converted data | **no** |
| 7 | Test data: the app on the converted data | judging + explanations |
| 8 | Every test | no (one live smoke test if a key is set) |
| 9 | The core's own app | no |

`run.bat <mode>` still works (`app`, `kalshi`, `monitor`, `monitor-kalshi`, `testdata-convert`, `testdata-core`,
`testdata-app`, `test`, `core`). Needs `GEMINI_API_KEY` in `.env` (never in `.env.example`).

## The smart loop (`frame/watch.py`, `Pipeline.watch_tick`)

Everything checked that shows no gap YET (venue sets, Yes + No checks, ladders, in-game links, confirmed pairs)
joins a watch list and is re-checked by the **core only, no LLM**, on a schedule that tightens near resolution:

| time to close | > 7 days | 2–7 days | 1–2 days | 2–24 h | < 2 h, or game in play |
|---|---|---|---|---|---|
| re-check every | 6 h | 1 h | 20 min | 5 min | 1 min |

A check within 1¢ of a gap halves the interval. Closed items drop off. The monitor does a full scan for NEW
candidates every 5 min and the due re-checks every 30 s; the app runs the same re-check loop in the background
(`FRAME_APP_WATCH=0` turns it off when the monitor is running). A re-check that finds a gap is logged as
`watch_hit` and shown as an alert at the top of the Opportunities page. Kalshi prices are never written to the
watch-list file.

## Sports (`frame/sports.py`)

Premier League games (`FRAME_SPORTS_LEAGUES`) are pulled separately for the next `FRAME_SPORTS_DAYS`.
- **In-game links by code:** an exact score fixes the result ("0-1" implies "Liverpool win"), so these pairs are
  derived and screened by the core with no LLM.
- **A game is open until kickoff + 2 h 15 min** (Polymarket sets `endDate` to kickoff, but games trade in play).
- **Live state** comes from Polymarket's own game events, with optional football-data.org / ESPN feeds
  (see `.env.example` and `docs/RESEARCH_NOTES.md`).

## Test data (`frame/testdata.py`)

Put downloaded files (JSON, JSONL, CSV) in `data/testdata/raw`.
- Gemini maps each unfamiliar layout to our format and returns **field names only**, cached per file.
- Code copies and validates every number.
- `testdata-core` prices everything that needs no LLM and writes `data/testdata/report.md`.

## What to scan (checkboxes)

The Check page has three rows of checkboxes; `.env` sets the defaults, which the monitor also uses:
- **Sources** (`FRAME_VENUES`): Polymarket, Kalshi, Manifold, PredictIt and Betfair Exchange.
  - Kalshi can only be ticked when `FRAME_KALSHI_MEMBER=1`.
  - Manifold is play money, so it's a signal only.
  - PredictIt and Betfair are shown but not connected yet.
- **Kinds of market** (`FRAME_CATEGORIES`, default `all`): crypto, economics, politics, sports, science and tech, climate and weather, culture, health, other.
- **Sports** (`FRAME_SPORTS_LEAGUES`, default `premier-league`): Premier League, Champions League, La Liga, Bundesliga, Serie A, NBA, NFL, MLB, NHL, UFC, tennis, F1, cricket. Games of ticked sports are pulled separately. A sports market whose league can't be told (e.g. Kalshi's) is kept.

## Novig (`frame/novig.py`)

Novig is a US sports exchange. The checker uses only its **public, unauthenticated** endpoints (`api.novig.com/v3/public/…`: events, markets, order books), with the same client rules as the core: GET only, allowlisted, rate-spaced, cached. It never uses the signed trading API.
- **Which markets:** match results of the ticked sports. For football that's `MONEYLINE_3_WAY_WIN` and `MONEYLINE_3_WAY_DRAW`, both Yes/No, plus `MONEY` for two-way sports.
- **How the books are built:** the ask on Yes is 1 − the best No bid, and contracts are 1¢, so 100 contracts make one $1 share.
- **Fee:** Novig publishes a coefficient of 0.06, charged in-play only. We always apply 0.06 × p × (1 − p), to be cautious.
- **Rules text:** Novig publishes none, so the checker writes a labelled summary of the market type instead.
- **Same-match pairs:** `sports.cross_venue_games` matches the same match result across venues by code (team names with aliases, kickoff within 3 h). Those pairs are judged first, but still go to Gemini and a human before the core prices them.
- **Pasted links:** a Novig link (any URL containing a Novig market or event id) works in *Check a pair*.

## prediction.com (`frame/predictioncom.py`)

prediction.com's scanner can be **checked** by ours: each listed opportunity becomes a pair in our pipeline, and their claimed ROI is shown next to our core's verdict.
- **Paid route:** `GET /v2/arb` needs their **Dev tier** and `PREDICTION_API_KEY`.
- **Free route:** paste an opportunity's two "Open market" links into *Check a pair*.
- **Their terms:** no scraping outside the API, no redistributing their data, personal or internal use only.

## Historical trades (`frame/hfdata.py`, optional `requirements-data.txt`)

The [SII-WANGZJ/Polymarket_data](https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data) dataset holds about 1.27 billion trades. It has **no order books**, and about 85% of its rows fall in Jan–Sep 2026; 2024 is nearly absent.

The file's layout, as measured:
- rows are in day order;
- within a day they are grouped by market;
- markets of one event sit next to each other.

The sampler never scans the file:
1. **`scout`** finds a day by binary search on row number, then reads a few random 150k-row chunks straight from Hugging Face.
2. It scores each event with a cheap screen on trade prices: exclusive outcomes not adding up to $1, or ladder rungs out of order, using only prices traded within 15 minutes of each other.
3. **`export <find>`** writes an hour around the strongest signal as a recording with **trade-implied books**: taker buys become asks, taker sells become bids. That is liquidity that really traded, not the resting book.
4. **`python -m frame.testdata study`** replays it through the core.

## Smart ordering (`frame/priority.py`)

The default sort is a score with its reasons shown on hover. In order of weight:
1. What the core said.
2. How soon it resolves; a game in play scores highest.
3. How trustworthy the link is.
4. The type of arbitrage.
5. Whether a watched item came close before.

These weights only order the list; every number in a verdict still comes from the core.

## Suggestion rules (`frame/rules.py`, version `suggest-rules/3`)

| Rule | Meaning |
|---|---|
| R0 | Not the same market twice; neither leg effectively settled (displayed price ≥ 0.98 or ≤ 0.02) |
| R1 | Manifold is play money: never a leg |
| R2 | Crypto pairs only with crypto, and only the same coin (BTC with BTC) |
| R3 | Both markets must share a category (an event tagged politics + trade-war counts as both) |
| R4 | A category must be known on both sides; unknown is not assumed to match |
| R5 | Both markets must close within 45 days (`FRAME_MAX_DAYS_TO_CLOSE`) and neither may have closed already; a missing close date is refused |
| R6 | Pairs claimed to be the same question must close within 14 days of each other (`FRAME_MAX_CLOSE_GAP_DAYS`). Not applied to nested ladders inside one event, which the core screens instead |

Changes from `/2` came from a rules review:
- R0 is new.
- R5 refuses markets that have already closed.
- R5 and R6 measure exact time; whole-day rounding had made R6 depend on argument order.
- R3 accepts any shared category, and more tags are mapped.
- R6 no longer applies to nested ladders.
- The candidate date window equals R6, so refusals are logged rather than silently filtered.

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
