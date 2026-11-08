---
tags: [agentverse, data, compliance]
created: 2026-11-07
---
# 16 · Live data: what the app reads, and where else to get it

Related: [[14 Built App — How It Works]] · [[15 Suggestion Rules & Log]] · [[17 Run, Config & Operations]] · [[12 Architecture & Stack]]. Full version with sources: `docs/DATA_SOURCES.md`. Third-party claims come mostly from vendor pages — **verify coverage, price and licence before paying or building on them.** Not legal advice.

## Why the first version showed "fake" companies
`run.bat` started in **demo mode**, whose test venue only accepts fake `*.example` links. So the real links the team pasted were refused ("We only read links from…") and the venue scan only ever saw invented markets. Demo mode has been removed from the app; the invented markets exist only inside the tests. The app is **live-only** now.

## Wired in now (all through the core's read-only client)
The client is GET-only, allowlisted to specific public endpoints, rate-spaced, with a 60 s cache and a 5 MB response cap. The app never talks to the internet any other way.

| Source | What | Use |
|---|---|---|
| **Polymarket Gamma API** | events (with tags → categories), markets, rules text, `negRisk` sets | top 200 open events by 24h volume, read in pages of 25 (halves on "response too large") |
| **Polymarket CLOB `/book`** | live order books | fresh books every time a confirmed pair is priced |
| **Kalshi REST** `trade-api/v2` | events (with `category`), markets, rules, books | **only if Kalshi is switched on** (below) |
| **Manifold** | play-money prices | signal only, never a leg |

Timing: pulling about 2,200 Polymarket and 1,700 Kalshi markets takes about 9 s; a whole scan about 1–2 minutes.

## Kalshi: why it is a switch
Kalshi's Developer Agreement limits API use to **a member's own trading** and bars collecting, caching, storing or sharing its data. So:
- Kalshi is **off by default**; the team turns it on with `FRAME_KALSHI_MEMBER=1` in `.env` (or `run.bat kalshi` for one run) **only if a member runs it on their own computer**;
- nothing from Kalshi is written to disk (confirmed-pair files strip prices; the suggestion log redacts them; the core decision log is skipped when a live Kalshi leg is present);
- do not post screenshots of Kalshi prices.

## Update 8 Nov
- **Sports:** Polymarket's own Premier League game events have kickoff, game id and live score, and are the primary live source. Optional extra feeds:
  - football-data.org: official, but the free tier is delayed;
  - ESPN's scoreboard: live, but unofficial, so personal use only.
  - TheSportsDB's livescores are paid.
- **Polymarket sandbox:** none officially. Polymarket US has one for partners only, Kalshi has `demo-api.kalshi.com`, and the GitHub "sandboxes" are community paper traders. For judging, use live data read-only, paper execution via polysim, and a recording as backup.
- **Our own recording** (`recordings/polysim-20261108-1127Z`, 7–8 Nov) is now usable as test data and was replayed in full: [[19 Recording Study — 7–8 Nov]]. It is private and must not be redistributed.
- Sources and details: `docs/RESEARCH_NOTES.md`.

## Update 8 Nov (later)
- **Novig** is now a venue (public API, sports match results).
- **prediction.com**'s arbitrage list can be checked by our pipeline.

Details and how to run: [[21 Novig & prediction.com]].

## Record forward (for evaluation on unseen data)
- **polysim `record` / `fetch`**: polls Polymarket books to JSONL and pulls price history and resolutions. Polymarket keeps 1-minute history only about 7 days, so record forward.
- **Polymarket CLOB WebSocket**: live book stream (check current terms; one vendor claims a paid tier).
- **Kalshi WebSocket**: needs a signed key and the same member-only terms.

## Historical data (backtests, labelled pairs)
| Source | Coverage (claimed) | Cost |
|---|---|---|
| **Dune** prediction-market tables | Polymarket from Nov 2022, Kalshi from Jun 2021, with outcomes; SQL | free tier |
| **Polygon on-chain `OrderFilled` logs** | every Polymarket settlement | RPC/indexer cost |
| **Polymarket Data API `/trades`** | recent trades; reported cap ~10,000 rows | free |
| **Telonex** | tick trades, book snapshots, quotes (Parquet) | paid, small trial |
| **Oddpool** | historical books and trades from Mar 2026 | paid |
| **Hugging Face / Kaggle dumps** | community snapshots | free; check each licence |
| **Mendeley "Kalshi and Polymarket C1"** | metadata and terminal prices | free, academic |

## Cross-venue aggregators (more venues without new adapters)
FinFeedAPI (Polymarket, Kalshi, Myriad, Manifold) · Prediction Hunt v2 · Predictefy · PolyRouter (open beta) · Tatum · pmxt (open source, but it also executes: **we must not use that part**). Dome was acquired by Polymarket in early 2026. Anything added sits behind `frame/adapter.py` and keeps the same rules: read-only, allowlisted, no Kalshi storage, Manifold never a leg.

## UK-regulated venues (later, if the firm wants real UK trading)
- **Betfair Exchange API**: a free **delayed** development key on production data; one-off £299 activation fee for the live key; commercial use costs extra.
- **Smarkets**: has an API; its terms were not found. Check directly.

## Recommended next steps
1. Leave `run.bat monitor` running to build a forward record of suggestions and re-priced confirmed pairs.
2. Run `polysim record` on the markets of confirmed pairs, so `polysim evaluate` has real books to replay.
3. Use **Dune** to pull resolved Polymarket/Kalshi pairs for the labelled evaluation set (`docs/PROTOCOL.md`).
4. If Kalshi coverage matters and nobody is a member, check whether an aggregator's licence allows our use.
