# polysim: paper-trading simulator for Polymarket

polysim takes a trade idea and plays it out **on paper** against real Polymarket order books, so we can see what would actually have happened after fees, thin books and delays, all the way to resolution. Every order passes risk limits and an approval step, and every step goes into a tamper-evident audit log.

> **Paper trading only.** polysim has no code that places, signs or cancels a real order, and it holds no keys, wallets or logins. It only sends read-only GET requests to Polymarket's public endpoints. Every number it prints is a simulated result, not real-world performance.

## Who it is for and where it fits

The project's user is a Polymarket trader (in a country where Polymarket is available) who sees a "risk-free" arbitrage posted online and has to decide whether to take it.

- **arbcheck** (the numeric core) decides whether a gap is worth a look: it works out the guaranteed payout, walks the books and applies fees and risk limits.
- **frame** (the LLM layer) reads the markets' rules, quotes the clauses that differ, and has a human confirm before anything is priced.
- **polysim** is the next step: it takes a trade that was approved, buys it on paper as one basket with a delay between legs, holds it until the markets resolve and reports what we would actually have made or lost, including what a failed attempt costs.

That supplies the last two numbers of our evaluation: how many flagged arbitrages still paid out at resolution, and how much a naive price-only checker would have lost on the ones we blocked.

## Quick start

```bash
pip install -e ".[dev]"
pytest

# Every control triggering on screen, on invented data (no network needed): the live-demo script
polysim demo

# Our checker vs a naive price-only checker, on a SYNTHETIC dataset (no network needed)
polysim evaluate --demo

# Example strategies on an invented market
polysim backtest --strategy buy_and_hold --seed 1
polysim backtest --strategy market_maker --fill-mode through --fee-category crypto --latency 5 \
    --max-order-notional 50 --max-drawdown-pct 5 --audit audit.jsonl
polysim audit-verify audit.jsonl

# Ask a person before every order instead of auto-approving
polysim backtest --strategy buy_and_hold --approve cli --approver-name "Your Name"

# Real data (needs access to gamma-api.polymarket.com / clob.polymarket.com)
# Record whole ladders (every open market of each event) until stopped; see "Recording" below
caffeinate -i polysim record-events anthropic-ipo-by what-price-will-bitcoin-hit-in-november-2026
polysim resolutions                       # later: add results for markets that have resolved
polysim evaluate --candidates candidates.jsonl --events recordings --markets recordings/markets.json \
    --split-ts 2026-11-12
polysim markets --limit 20
polysim record <market-slug> --interval 5 --duration 3600 --out books.jsonl
polysim fetch <market-slug> --fidelity 60 --out events.jsonl --markets-out markets.json
polysim backtest --strategy mean_reversion --events events.jsonl --markets markets.json
```

Every backtest starts with a label line, for example
`PAPER RESULT on SYNTHETIC data (invented market): simulated fills, not real-world performance`.

## What `polysim demo` shows

Four short paper backtests on invented markets, each one triggering a control:

1. **A real ladder arbitrage.** "X by Nov 30" implies "X by Dec 31", so buying No on the first and Yes on the second always pays at least $1. The basket is approved once, both legs fill, and after fees it locks in +3.05 USDC on 100 baskets.
2. **The same basket going wrong.** With a 2-second delay, the second leg's sellers disappear before it arrives. Fill-or-kill fails, the first leg is sold straight back (pre-approved as part of the plan), and the **leg-risk cost** of 3.99 USDC is reported: what a "risk-free" arbitrage can still lose.
3. **Risk limits in code.** An order over the 150 USDC limit is refused. A price crash takes the account 5.2% below its peak, so the **kill switch** trips: buys are refused (reduce-only), closing the position is still allowed, a reset by `auto` is refused, and a reset by a named person works.
4. **The audit log.** The hash chain verifies; editing one line in a copy is caught at that record.

## Evaluation: `polysim evaluate`

This answers the judging question "tested on unseen data, with real costs, against a fitting benchmark, with uncertainty stated".

**Benchmark.** A naive checker that flags any basket whose guaranteed payout is above the sum of the **mid** prices. It ignores fees, depth and the rules: the conventional program.

**Input.** A JSONL file of claimed arbitrages from the checker (arbcheck / frame), one per line:

```json
{"id": "c1", "ts": 1767225600, "token_ids": ["A-NO", "B-YES"], "floor": 1.0,
 "rule_check": "match", "relation": "a_implies_b", "decision_id": "..."}
```

`floor` is the guaranteed payout per basket if the relation holds, and `rule_check` is `match`, `mismatch` or `unclear`. Both come from the checker; the evaluation does not re-derive them. It also needs the books and results recorded with `record` / `fetch`.

**What happens to each candidate, step by step.**

1. **Naive flag**: floor > sum of mids?
2. **Fees**: still positive at the best asks plus taker fees?
3. **Depth**: walk the books; at least `--min-shares` baskets fillable, with an average edge of at least `min_edge` per share after fees?
4. **Rules**: the rule check said `match`? `unclear` counts as a fail.

Our checker takes a candidate only if it passes all four. Then **both** checkers' trades are executed on paper with polysim (fill-or-kill legs, 2 s latency, automatic unwind) and held to resolution.

**Scoring.** P&L counts only what the arbitrage claimed to guarantee: floor × baskets − everything paid. If an outcome pays more than the floor (e.g. both legs win), the extra is luck, not arbitrage, so it is listed separately and not counted. If the relation breaks, the whole loss counts.

**Outcomes** for our checker: **win** (took it, made money), **false alarm** (took it, didn't), **miss** (blocked it, the naive trade made money), **correct block** (blocked it, the naive trade lost). False alarms and misses are listed one by one, next to the wins.

**Unseen data.** With `--split-ts`, `min_edge` is chosen on the earlier period only, then used **once** on the later period. Without a split, the report says it is in-sample.

**Uncertainty.** 95% bootstrap intervals for the P&L difference, our precision and the naive hit rate. Under 30 resolved candidates, the report warns that the intervals are wide.

**Bad or manipulated inputs** are rejected before evaluation and listed with the reason: malformed JSON, missing fields, an impossible floor (e.g. "$3 guaranteed" on two legs), the same token twice, unknown markets, stale or crossed books, and claims made after a market had already resolved.

```bash
polysim evaluate --candidates candidates.jsonl --events books.jsonl --markets markets.json \
    --split-ts 2026-11-20 --json report.json
```

**About `--demo`.** It runs on an invented dataset of 60 ladder pairs: real gaps, gaps eaten by fees, thin books, rule mismatches, rules that couldn't be confirmed, and sellers who vanish during the delay, plus 6 deliberately bad inputs. It shows how the evaluation behaves. **It is not evidence that the checker works**; the result is built into the data. Only results on recorded data count.

## Recording real data: `polysim record-events`

Polymarket keeps fine-grained history for only about a week, and it never keeps full order books, so we record forward ourselves. The later part of a recording is data the checker has never seen.

`polysim record-events <event-slug> ...` records **every open market of each event**, which is what a ladder is: one event with a market per date or price level. Once a minute, for each token, it saves the top 20 levels of the book. Files go to `recordings/`:

- `books-YYYYMMDD.jsonl.gz`: one gzip file per UTC day, written once per round, so a crash loses at most one minute.
- `markets.json`: every market seen, with its fee schedule and its rung in the ladder (e.g. "December 31", "↑ 100,000").
- `events/<slug>.json`: the raw event, including each market's rules text.
- `resolutions.jsonl`: each market's result as soon as it resolves. The recorder refreshes events every 30 minutes, adds new markets and stops recording closed ones. `polysim resolutions` fills in any results after recording stops.
- `recorder.log`: progress and errors. A failed request is logged and skipped, never fatal.

It sends at most 5 requests a second, far below Polymarket's documented limits, and backs off on HTTP 429. It reads only public GET endpoints and identifies itself as a read-only research tool. `recordings/` is in `.gitignore`, because Polymarket's terms bar redistributing their data. On a Mac, `caffeinate -i` keeps the computer from sleeping while it runs. Restarting it with the same folder carries on where it left off.

`--events` in `evaluate` and `backtest` accepts recorder folders as well as files.

## How it works, step by step

1. **Get the data (`data/polymarket.py`, `data/feeds.py`).**
   It reads Polymarket's public Gamma API (markets, rules, fee schedule, results) and CLOB API (order books, price history). No account is needed.
   `record` polls the order books every few seconds and saves each snapshot to a JSONL file. Polymarket keeps 1-minute price history for only about 7 days, so we record forward ourselves.
   `fetch` downloads price history and, for closed markets, how each one resolved.
   If a source has prices but no book, `BookSynthesizer` (`engine.py`) builds a stand-in book around the price. Results from it are less reliable than results from recorded books.

2. **Or invent a market (`data/synthetic.py`, `demo.py`).**
   The Yes price follows a random walk with a slow drift towards a pre-drawn outcome. The same seed always gives the same market. These results are always labelled as synthetic.

3. **Replay time (`engine.py`).**
   Events play back in time order: a new book, a new price, or a market resolving.
   A strategy only sees books it has already been shown, so it can never trade on the future.

4. **Check every order before it is sent (`risk.py`, `approval.py`).**
   A strategy cannot reach the exchange directly. Each order goes through the same gate: **risk limits, then approval, then the exchange**.
   Limits: maximum order size, maximum cost per token, maximum total exposure (positions plus cash held by resting orders), and maximum orders per day. Market orders are sized by walking the book, and any size the book can't cover is priced at $1, so the check never understates the risk.
   The **kill switch** trips on a daily loss or a drawdown limit. It looks at the new prices *before* the strategy can react to them. Once it trips, resting buys are cancelled and the account is **reduce-only**. Only a named person can reset it.
   **Approval**: the engine asks an approver for every order or basket. Backtests use `auto_approve`, and the log says so. `--approve cli` asks a person and records their name.

5. **Buy an arbitrage as one basket (`basket.py`).**
   A basket of legs is checked against the limits and approved **once**, as a whole.
   The legs are then sent one after another as fill-or-kill buys, each subject to the delay, so the book can move between legs.
   If a leg fails, the legs already bought are sold back at once. That is part of the approved plan, so it needs no second approval. The loss from this round trip is the **leg-risk cost**, and any shares the unwind couldn't sell are reported.

6. **Fill orders (`exchange.py`).**
   An order that takes liquidity walks the book level by level, so a big order on a thin book gets a worse average price. It never fills at the mid.
   Liquidity we took stays gone until the next snapshot, so the same size can't be filled twice.
   With `--latency`, an order arrives that many seconds late and fills against the book at that later moment.
   FOK, FAK and GTC are supported. Prices must be on the market's tick and orders above its minimum size.
   A resting order fills at its own price when a later snapshot reaches it (`touch`) or trades through it (`through`, stricter).
   If cash runs short, the order fills only what you can afford.

7. **Charge fees (`fees.py`).**
   Fee = rate × price × (1 − price) × shares, rounded to 5 decimals, as in Polymarket's fee docs. For example, 100 crypto shares at 50¢ cost $1.75. Makers pay nothing.
   The rate is taken from each market's own `feeSchedule` in the Gamma API (e.g. politics: 0.04, taker only). If a market has no schedule, its category is looked up in the documented table (crypto 0.07; sports, economics, culture, weather, other, combos 0.05; finance, politics, mentions, tech 0.04; geopolitics 0). `--fee-category` or `--taker-rate` override it for what-ifs.

8. **Keep the account (`portfolio.py`).**
   It tracks cash, positions, average cost and profit, both realised and on paper. Fees are taken from cash and totalled separately, so the average cost is the price paid before fees.
   A resting order holds back its cash or shares until it fills or is cancelled.
   No leverage and no naked shorts: to bet against Yes you buy No, and you can't sell what you don't own.
   Open positions are valued at the **best bid**, what they could actually be sold for (`--mark mid` to compare).
   When a market resolves cleanly, its open orders are cancelled and shares pay $1 or $0. If the reported result is anything else (a dispute or a split), nothing is paid out automatically: the market is flagged and waits for `settle_manually(..., by=<person>)`.

9. **Log everything (`audit.py`).**
   Every risk check (with a hash of the book it saw), approval, order, rejection, fill, basket step, kill-switch event and settlement is appended to a JSONL log. Each record is tagged `paper_trading: true`, carries the `decision_id` that links it to the checker's reasoning, and is chained to the one before by a SHA-256 hash. `polysim audit-verify` finds the first record that was edited, deleted or reordered. The format matches frame's audit log, so the two can be merged.

10. **Report (`metrics.py`, `cli.py`).**
    Return, worst drawdown, Sharpe ratio, fills (and how many as maker), volume, fees, risk rejections, kill-switch state, baskets filled or unwound with their leg-risk cost, markets awaiting manual settlement, and whether the audit chain verifies.
    `--json` saves the metrics, the equity curve, the final portfolio, the risk state and every basket.

## Data sources and licences

| Data | Where it comes from | Licence and how we use it |
|---|---|---|
| Market metadata, rules, fee schedules, results | Polymarket Gamma API, `gamma-api.polymarket.com` (public, no auth) | [Polymarket Terms of Service](https://polymarket.com/tos) (effective 11 Aug 2026). They grant a personal, limited, revocable licence and bar reselling, redistributing or commercially exploiting their data, including via the API, without written consent. Scraping with bots is banned. We call only documented API endpoints, for non-commercial hackathon research. |
| Order books, price history | Polymarket CLOB API, `clob.polymarket.com` (public, no auth) | Same terms. Recorded snapshots stay on our own machines. `*.jsonl` is in `.gitignore`, so they are never published in this repo. |
| Synthetic markets | Generated by `data/synthetic.py` and `demo.py` | Our own code, no third-party data. Always labelled as synthetic. |
| Test fixtures (`tests/polysim/fixtures/`) | Written by hand in the shape of API responses | Invented values (e.g. "Will it rain in London tomorrow?"), not copied from Polymarket. |

Not used: **Kalshi** (its data terms bar AI/ML use, public display and storage) and any account, wallet or personal data.

Polymarket's terms list the United Kingdom among the places where **trading** is not permitted. People there may only use the content features. Our team is in the UK, which is one more reason polysim is paper-only by construction.

This is an engineering summary, not legal advice. The venue's own terms are the authority, and they change, so re-check them before relying on this.

## Assumptions and known limits

Where we had to guess, we chose the cautious option, except where a limit below says otherwise. These are the places where the simulator is **not yet** as strict as our spec, or is optimistic:

- **Maker fills are optimistic.** There is no queue position: a resting order fills as soon as the price reaches it, even if others were ahead of us. Use `--fill-mode through` for a stricter test. A queue model needs trade prints, which we don't record yet.
- **Consumed liquidity** returns with the next snapshot, not after a fixed time. With frequent snapshots this can let the same size refill too soon.
- **Maker rebates are ignored** (conservative). The fee `exponent` in Gamma's schedule is 1 on every market we have seen. Any other value is applied as rate × (p(1 − p))^exponent, which is our assumption, not documented.
- **Markets with no fee information** (only invented ones in practice) are fee-free unless you pass `--fee-category` or `--taker-rate`.
- **Baskets are buy-only** and their legs are sent in the order given. polysim does not check that a basket is an arbitrage; that is arbcheck's job.
- **The kill switch is checked on each event**, so between two snapshots it can't react. Daily limits use simulated days from the data's timestamps.

## Writing a strategy

```python
from polysim import Backtester, BasketLeg, RiskLimits, Strategy, TimeInForce
from polysim.data import generate_binary_market

class Momentum(Strategy):
    def __init__(self):
        self.last = {}

    def on_book(self, ctx, book):
        prev, self.last[book.token_id] = self.last.get(book.token_id), book.mid
        if prev is None or book.mid is None or ctx.kill_switch_on:
            return
        held = ctx.position(book.token_id).qty
        if book.mid - prev > 0.02 and held == 0:
            ctx.buy(book.token_id, 50, price=book.best_ask, tif=TimeInForce.FAK)
        elif book.mid < prev and held > 0:
            ctx.sell(book.token_id, held)  # no price = market order (FAK)

    def on_kill_switch(self, ctx, reason):
        print("kill switch:", reason)

market, events = generate_binary_market(seed=42)
limits = RiskLimits(max_order_notional=40, max_drawdown_pct=10)
print(Backtester(events, [market], Momentum(), initial_cash=1_000, risk_limits=limits).run().summary())
```

For an arbitrage, call `ctx.execute_basket([BasketLeg(token_a, 100, max_price=0.56), BasketLeg(token_b, 100, max_price=0.41)], decision_id=...)` and read the outcome in `on_basket`.

## Next steps

In priority order, matching the hackathon judging:

1. **Record real books now** for ladder pairs, so there is a later period the checker has never seen.
2. **Connect to arbcheck and frame**: export their candidates (with `floor`, `rule_check` and `decision_id`) as `polysim evaluate` input, use arbcheck's read-only client, and write paper fills into frame's audit log.
3. **Hand-label 100–200 market pairs** to measure the LLM's relation detection (precision first) alongside this evaluation.
4. **Record trades as well as books**, then add a queue model for maker orders.
5. **"What would change the verdict"**: report the price or depth at which a basket stops being worth it.
