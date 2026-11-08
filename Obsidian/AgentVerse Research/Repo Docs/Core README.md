---
tags: [agentverse, repo-doc]
source: arbcheck-project/README.md
synced: 2026-11-08 11:58
---
> Copy of `arbcheck-project/README.md` from the repo (branch `integrated/llm-and-core`), synced 2026-11-08 11:58. Edit the repo file, then re-run `python scripts/sync_vault_docs.py`. Index: [[Repo Docs Index]]

# Arbcheck: is the arbitrage you spotted real?

You type the prediction-market events you think are an arbitrage (links or plain words). Arbcheck
answers **yes, no, or look closely**, shows exactly where the profit goes or why it vanishes,
then searches for related opportunities you did not ask about. Read-only: it never places orders.

Built for the UCL Agentverse hackathon, Finance track ("Help people make better money decisions").
The user problem: a price gap that looks like free money is often not, because the contracts' rules
differ, spreads and thin order books eat it, or fees do. People lose money believing it is an arbitrage.

## Run it

```bash
python -m arbcheck serve --demo        # the app on invented markets, no internet needed -> http://127.0.0.1:8765
python -m arbcheck serve               # live Polymarket + Manifold (needs internet)
python -m arbcheck serve --kalshi-member   # also Kalshi, ONLY if you are a Kalshi member, on your own computer
python -m arbcheck check --demo "event one" "event two"   # same check in the terminal
python -m arbcheck demo                # static report page on the five demo pairs -> report.html
python -m pytest -q                    # 152 tests
```

Optional: `ANTHROPIC_API_KEY=... python -m arbcheck serve --llm` lets Claude read the rules (pip install anthropic).

## Arbitrage types covered

| Type | Example | How |
|---|---|---|
| Cross-platform | same event on Polymarket and Kalshi | `equivalent` relation, buy Yes on one and No on the other; cross-venue rule-risk flag always applied |
| Logical: nested deadlines | "by June" vs "by December" | earlier implies later |
| Logical: price thresholds | "above 5,500" vs "above 5,000" | harder implies easier |
| Multi-outcome sets | 4 teams, one winner | venue says mutually exclusive: buy No on all, pays at least N-1 |
| Same market | Yes + No cost under $1 | complement |
| Signal only | Manifold (play money) | shown as a price gap, never a leg |

One idea powers all of them: a relation rules out some outcome combinations ("worlds"); a basket's
floor is its smallest payout over the worlds left; if floor beats cost after fees, profit is locked in.

## How it fits the judging criteria

| Criterion | What to point at |
|---|---|
| Traceable reasoning 25% | Every verdict is a ticket: the plain-English answer, a bar chart of where each cent went (price gap, spread, depth, fees, rule risk), the clauses that differ quoted from both rules, payoff in every outcome, nine named checks, and a JSONL audit log with a SHA-256 of the rules (`--log`). The LLM must quote rules verbatim and code verifies each quote. |
| Testing on unseen data 25% | 152 tests incl. hand-checked numbers and adversarial inputs. Honest gap: no labelled real-market evaluation yet. `evaluate_extractor` and `collect` exist for it; see next steps. The false-arbitrage demos show the tool catching cases a price-only scanner would trade. |
| Risk controls and oversight 15% | Exposure caps, kill switch, minimum edge/confidence/annualised return, rule-risk haircuts, fail-safe LLM that can only veto, read-only network layer with an allowlist, Kalshi off by default, Host check and CSP on the local app. |
| Relevance and impact 20% | Targets a real retail mistake (false arbitrage) and answers the exact question the user has. |
| Ambition and technical quality 15% | N-leg order-book walking, per-venue fee models (quadratic, Kalshi cent rounding), multiple arbitrage families, three venues, standard-library only core. |

## Code map

```
compliance.py  the ONLY network code: GET-only, allowlist, rate limit, cache, backoff, Kalshi gate, venue terms text
polymarket.py / kalshi.py / manifold.py   read-only adapters; parse links, search, siblings, order books
venues.py      Registry: turns what the user typed into matched markets; failures isolated per venue
fees.py        fee models per venue
relations.py   rule reading: heuristic baseline + LLM extractor, verbatim-quote verification, mismatch flags
engine.py      pure maths: worlds, baskets, order-book walk, fees, annualised return
guard.py       limits, nine checks, decision, audit log
pipeline.py    markets -> relation -> maths -> guard = a Case
finder.py      the workflow: check the user's idea, then look around (siblings, other venues, Yes+No)
ui.py / web.py the local web app (stdlib server), report.py the ticket renderer
persistence.py Kaplan-Meier survival of gaps; demo_data.py invented markets
```

## Terms and compliance (summary, not legal advice)

- Only documented public data endpoints, GET only, no login, no orders, no scraping, descriptive User-Agent,
  slow request spacing, 60 s in-memory cache, backoff on 429/5xx. Enforced and tested in `compliance.py`.
- **Kalshi:** its Developer Agreement limits API use to a member's own trading and bars storing the data
  for other purposes or sharing it. So Kalshi is off unless you pass `--kalshi-member`, it only runs when the
  app listens on this computer, and nothing from Kalshi is written to disk. Do not publish screenshots of Kalshi
  prices. If you demo publicly, use `--demo`. Publicly presenting a product built on Kalshi's API may need
  Kalshi's approval: check section 6 of the agreement.
- **Polymarket:** reading is open; trading is geo-restricted. Fee schedules change: verify before trading.
- **Manifold:** play money, signal only; stay within its API limits; no training AI models on its data.
- Links: Polymarket https://polymarket.com/tos, Kalshi https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf,
  Manifold https://docs.manifold.markets/terms. The app shows these in "What data we read".

## What is verified, and what is not

Verified here (152 tests): maths for every relation type, fees, order-book walking, guard decisions, clause
verification, LLM failure handling, the compliance layer (allowlist, GET-only, Kalshi gate, rate limit, retries),
all three adapters against **canned JSON**, the finder on a synthetic universe, and the web app (escaping,
headers, Host check, kill switch).

**Not run:** live calls to Polymarket, Kalshi or Manifold (the build sandbox blocks them), live Claude calls.
Adapter response shapes follow each venue's docs; check on first run. Demo markets are invented and labelled.
The heuristic reader cannot understand differently worded rules, so cross-venue pairs always carry a
"who settles it" warning and usually surface as "look closely" rather than "worth a look".

## Next steps

1. Run `python -m arbcheck collect` to gather real order-book snapshots for the persistence study.
2. Hand-label 100 to 150 resolved pairs (including cross-venue); score heuristic vs LLM on markets created after the model's cutoff.
3. Backtest a price-only scanner against this tool with the same fills and costs; report net PnL and false-arbitrage rate.
4. Verify current fee schedules for each venue.

Not financial advice.
