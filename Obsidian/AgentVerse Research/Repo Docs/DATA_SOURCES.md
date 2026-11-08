---
tags: [agentverse, repo-doc]
source: docs/DATA_SOURCES.md
synced: 2026-11-08 13:20
---
> Copy of `docs/DATA_SOURCES.md` from the repo (branch `integrated/llm-and-core`), synced 2026-11-08 13:20. Edit the repo file, then re-run `python scripts/sync_vault_docs.py`. Index: [[Repo Docs Index]]

# Real data sources for the False-Arb Checker

Researched 2026-11-07. Most third-party claims come from vendor pages: **verify coverage, price and licence
before paying or building on them.** Not legal advice.

## Wired in now

| Source | What | Access | How we use it |
|---|---|---|---|
| **Polymarket Gamma API** `gamma-api.polymarket.com` | events (with tags → our categories), markets, rules text, `negRisk` sets | free, no key | `frame/adapter.py` pulls top events by 24h volume through arbcheck's `ReadOnlyClient` (allowlisted GETs, rate-spaced, 60 s cache, 5 MB cap) |
| **Polymarket CLOB API** `clob.polymarket.com/book` | live order books per token | free, no key | arbcheck hydrates books when a confirmed pair is priced |
| **Kalshi REST** `api.elections.kalshi.com/trade-api/v2` | events (with `category`), markets, rules, order books (bids only) | public market data needs no key | **only with `--kalshi-member`**: its Developer Agreement limits API use to a member's own trading and bars storing/sharing the data |
| **Manifold** `api.manifold.markets` | play-money prices | free | signal only, never a leg |
| **Novig** `api.novig.com/v3/public` | US sports exchange: events, match-result markets, order books | public, no key | `frame/novig.py`; terms (Novig Sweeps, 21+, US states) silent on data: internal use, never redistributed |
| **prediction.com** `prediction.com/api/v2` | another scanner's cross-venue "arbitrage" list | `/v2/arb` = Dev tier ($49/mo) + key | `frame/predictioncom.py`: their claims are CHECKED by our pipeline; no scraping (their terms) |

## Record forward (for evaluation on unseen data)

| Source | Notes |
|---|---|
| **polysim `record` / `fetch`** (merged on this branch) | polls Polymarket books every few seconds to JSONL; `fetch` pulls price history and resolutions. Polymarket keeps 1-minute history only ~7 days, so record forward. |
| **Polymarket CLOB market WebSocket** | live book/price stream without polling. Our notes say no key; one vendor blog claims premium WebSocket access costs $99/month. Check the docs. |
| **Kalshi WebSocket** | order book, trades, tickers; documented in `docs.kalshi.com` (AsyncAPI spec). Needs a signed key; same member-only terms. |

## Historical data (backtests, labelled pairs)

| Source | Coverage (as claimed) | Cost |
|---|---|---|
| **Dune** curated prediction-market tables | Polymarket from Nov 2022, Kalshi from Jun 2021, resolved outcomes included; SQL | free tier, check limits |
| **Polygon on-chain `OrderFilled` logs** | every Polymarket settlement since inception: the settlement record | free (RPC/indexer cost) |
| **Polymarket Data API `/trades`** | recent trades | free; reported cap of 10,000 rows per query (offset limit) |
| **Telonex** | tick trades, book snapshots, quotes as daily Parquet; trades from Oct 2025, fills from Nov 2022 | paid, small free trial |
| **Oddpool** | historical books, top-of-book, trades from Mar 2026 | paid |
| **Hugging Face / Kaggle dumps** (e.g. `od2961/polymarket-full-market-dataset`, `ImpliedData/prediction-markets`) | community snapshots; Kaggle sample has active markets only | free; check each licence |
| **Mendeley "Kalshi and Polymarket C1"** | metadata and terminal prices for reproducibility | free, academic |

## Cross-venue aggregators (to add venues without new adapters)

| Service | Venues (claimed) | Note |
|---|---|---|
| **FinFeedAPI** | Polymarket, Kalshi, Myriad, Manifold | one endpoint |
| **Prediction Hunt v2** | Kalshi, Polymarket, PredictIt, ProphetX, Opinion | |
| **Predictefy** | Polymarket, Kalshi + 14 more | |
| **PolyRouter** | Kalshi, Polymarket, … | open beta |
| **Tatum** | Polymarket, Kalshi | 38 endpoints |
| **pmxt** | multiple | open source; also does execution (we must not use that part) |
| ~~Dome~~ | | acquired by Polymarket in early 2026, no longer independent |

Any aggregator would sit behind `frame/adapter.py` (or a new arbcheck venue adapter) and must keep the same
rules: read-only, allowlisted, no Kalshi data stored, Manifold never a leg.

## UK-regulated venues (if the firm wants real UK trading later)

- **Betfair Exchange API**: two app keys per account (one live, one **delayed** key on production data for
  development); one-off £299 activation fee; commercial use costs extra.
- **Smarkets**: has an API, but this research found nothing on its terms. Check directly.

## Recommended next steps

1. Keep `run.bat monitor` running to build a forward record of suggestions and re-priced confirmed pairs.
2. Run `polysim record` on the markets in confirmed pairs, so `polysim evaluate` has real books to replay.
3. Use **Dune** (free) to pull resolved Polymarket/Kalshi pairs for the labelled evaluation set (docs/PROTOCOL.md).
4. If Kalshi coverage matters and nobody is a member, test whether an aggregator's licence allows our use.

## Sources

- [The Top Prediction Market APIs in 2026 (Medium)](https://medium.com/@samuel.tinnerholm/the-top-prediction-market-apis-in-2026-ecb02baae641)
- [Polymarket, Kalshi & Aggregator APIs: Developer Guide](https://sportsgameodds.com/blog/aggregator-apis-advanced-developer-guide)
- [Prediction Market Aggregator Integration (Interexy)](https://interexy.com/how-to-integrate-prediction-market-aggregators)
- [Tatum prediction market API](https://tatum.io/blog/prediction-market-api)
- [Awesome-Prediction-Market-Tools](https://github.com/aarora4/Awesome-Prediction-Market-Tools)
- [Best Prediction Market APIs (NYC Servers)](https://newyorkcityservers.com/blog/best-prediction-market-apis)
- [PolyRouter](https://polymark.et/product/polyrouter)
- [Best API for Prediction Markets 2026 (Prediction Hunt)](https://www.predictionhunt.com/blog/best-api-for-prediction-markets)
- [Predictefy API](https://predictefy.com/en/api)
- [Polymarket historical data (Dwellir)](https://www.dwellir.com/blog/polymarket-historical-data)
- [Oddpool docs](https://docs.oddpool.com/polymarket/overview)
- [Telonex data coverage](https://telonex.io/data/polymarket)
- [Apify polymarket-api actor](https://apify.com/dirtycode/polymarket-api.md)
- [Kalshi API: KYC and fees (Allium)](https://allium.so/blog/kalshi-api-what-kyc-and-fees-mean-for-access/)
- [Kalshi on APIEvangelist](https://providers.apievangelist.com/providers/kalshi/)
- [Kalshi API (QuickNode)](https://www.quicknode.com/nl/builders-guide/tools/kalshi-api-by-kalshi-inc)
- [Kalshi docs](https://docs.kalshi.com/welcome)
- [Polymarket docs index](https://docs.polymarket.com/llms.txt)
- [Betfair developer docs](https://betfair-developer-docs.atlassian.net/wiki/x/fjYp)
- [Dune prediction markets](https://dune.com/data/prediction-markets)
- [Dune: Kalshi and Polymarket unified](https://dune.com/blog/kalshi-and-polymarket-data-unified-on-dune-a-macro-and-equity-signal-source)
- [Hugging Face: polymarket-full-market-dataset](https://huggingface.co/datasets/od2961/polymarket-full-market-dataset)
- [Hugging Face: ImpliedData/prediction-markets](https://huggingface.co/datasets/ImpliedData/prediction-markets)
- [Free Polymarket dataset on Kaggle (DEV)](https://dev.to/manja316/free-polymarket-dataset-on-kaggle-13963-active-markets-100k-price-sample-may-2026-4j4k)
- [Mendeley: Kalshi and Polymarket C1](https://data.mendeley.com/datasets/t2jzsc82gw/1)
