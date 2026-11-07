# polysim: paper-trading simulator for Polymarket

polysim takes a trade idea and plays it out **on paper** against real Polymarket order books, so we can see what would actually have happened after fees, thin books and delays, all the way to resolution.

> **Paper trading only.** polysim has no code that places, signs or cancels a real order, and it holds no keys, wallets or logins. It only sends read-only GET requests to Polymarket's public endpoints. Every number it prints is a simulated result, not real-world performance.

## Who it is for and where it fits

The project's user is a Polymarket trader (in a country where Polymarket is available) who sees a "risk-free" arbitrage posted online and has to decide whether to take it.

- **arbcheck** (the numeric core) decides whether a gap is worth a look: it works out the guaranteed payout, walks the books and applies fees and risk limits.
- **frame** (the LLM layer) reads the markets' rules, quotes the clauses that differ, and has a human confirm before anything is priced.
- **polysim** is the next step: it takes a trade that was approved, fills it on paper with a delay against the books as they really were, holds it until the markets resolve and reports what we would actually have made or lost.

That supplies the last two numbers of our evaluation: how many flagged arbitrages still paid out at resolution, and how much a naive price-only checker would have lost on the ones we blocked.

## Quick start

```bash
pip install -e ".[dev]"
pytest

# Example strategies on an invented market (no network needed)
polysim backtest --strategy buy_and_hold --seed 1
polysim backtest --strategy mean_reversion --param window=20 --param size=30
polysim backtest --strategy market_maker --fill-mode through --fee-category crypto --latency 5

# Real data (needs access to gamma-api.polymarket.com / clob.polymarket.com)
polysim markets --limit 20
polysim record <market-slug> --interval 5 --duration 3600 --out books.jsonl
polysim fetch <market-slug> --fidelity 60 --out events.jsonl --markets-out markets.json
polysim backtest --strategy mean_reversion --events events.jsonl --markets markets.json
```

Every backtest starts with a label line, for example
`PAPER RESULT on SYNTHETIC data (invented market): simulated fills, not real-world performance`.

## How it works, step by step

1. **Get the data (`data/polymarket.py`, `data/feeds.py`).**
   It reads Polymarket's public Gamma API (markets, rules, results) and CLOB API (order books, price history). No account is needed.
   `record` polls the order books every few seconds and saves each snapshot to a JSONL file. Polymarket keeps 1-minute price history for only about 7 days, so we record forward ourselves.
   `fetch` downloads price history and, for closed markets, how each one resolved.
   If a source has prices but no book, `BookSynthesizer` (`engine.py`) builds a stand-in book around the price. Results from it are less reliable than results from recorded books.

2. **Or invent a market (`data/synthetic.py`).**
   The Yes price follows a random walk with a slow drift towards a pre-drawn outcome. The same seed always gives the same market. These results are always labelled as synthetic.

3. **Replay time (`engine.py`).**
   Events play back in time order: a new book, a new price, or a market resolving.
   A strategy only sees books it has already been shown, so it can never trade on the future.

4. **Fill orders (`exchange.py`).**
   An order that takes liquidity walks the book level by level, so a big order on a thin book gets a worse average price. It never fills at the mid.
   Liquidity we took stays gone until the next snapshot, so the same size can't be filled twice.
   With `--latency`, an order arrives that many seconds late and fills against the book at that later moment.
   FOK, FAK and GTC are supported. Prices must be on the market's tick and orders above its minimum size.
   A resting order fills at its own price when a later snapshot reaches it (`touch`) or trades through it (`through`, stricter).
   If cash runs short, the order fills only what you can afford.

5. **Charge fees (`fees.py`).**
   Fee = rate × price × (1 − price) × shares, as in Polymarket's fee docs. For example, 100 crypto shares at 50¢ cost $1.75.
   Only the crypto rate (0.07) is entered so far. Any other category stops with an error asking for the rate, rather than charging nothing.

6. **Keep the account (`portfolio.py`).**
   It tracks cash, positions, average cost and profit, both realised and on paper. Fees are taken from cash and totalled separately, so the average cost is the price paid before fees.
   A resting order holds back its cash or shares until it fills or is cancelled.
   No leverage and no naked shorts: to bet against Yes you buy No, and you can't sell what you don't own.
   When a market resolves, its open orders are cancelled and shares pay what Gamma's `outcomePrices` says ($1 or $0).

7. **Report (`metrics.py`, `cli.py`).**
   Return, worst drawdown, Sharpe ratio, number of fills (and how many as maker), volume and fees paid.
   `--json` saves the metrics, the equity curve and the final portfolio for later analysis.

## Data sources and licences

| Data | Where it comes from | Licence and how we use it |
|---|---|---|
| Market metadata, rules, results | Polymarket Gamma API, `gamma-api.polymarket.com` (public, no auth) | [Polymarket Terms of Service](https://polymarket.com/tos) (effective 11 Aug 2026). They grant a personal, limited, revocable licence and bar reselling, redistributing or commercially exploiting their data, including via the API, without written consent. Scraping with bots is banned. We call only documented API endpoints, for non-commercial hackathon research. |
| Order books, price history | Polymarket CLOB API, `clob.polymarket.com` (public, no auth) | Same terms. Recorded snapshots stay on our own machines. `*.jsonl` is in `.gitignore`, so they are never published in this repo. |
| Synthetic markets | Generated by `data/synthetic.py` | Our own code, no third-party data. Always labelled as synthetic. |
| Test fixtures (`tests/polysim/fixtures/`) | Written by hand in the shape of API responses | Invented values (e.g. "Will it rain in London tomorrow?"), not copied from Polymarket. |

Not used: **Kalshi** (its data terms bar AI/ML use, public display and storage) and any account, wallet or personal data.

Polymarket's terms list the United Kingdom among the places where **trading** is not permitted. People there may only use the content features. Our team is in the UK, which is one more reason polysim is paper-only by construction.

This is an engineering summary, not legal advice. The venue's own terms are the authority, and they change, so re-check them before relying on this.

## Assumptions and known limits

Where we had to guess, we chose the cautious option, unless a limit below says otherwise. These are the places where the simulator is **not yet** as strict as our spec, or is optimistic:

- **Maker fills are optimistic.** There is no queue position: a resting order fills as soon as the price reaches it, even if others were ahead of us. Use `--fill-mode through` for a stricter test. A queue model needs trade prints, which we don't record yet.
- **Open positions are marked at the mid**, not the best bid, so unrealised profit before resolution is flattering. Realised profit at resolution is not affected.
- **Fees** are not rounded to 5 decimals as Polymarket does, and maker rebates are ignored (conservative).
- **Consumed liquidity** returns with the next snapshot, not after a fixed time. With frequent snapshots this can let the same size refill too soon.
- **Disputed results** are paid exactly as Gamma reports them. Anything not cleanly 0 or 1 should be checked by hand.
- **No risk limits, kill switch, human approval step or audit log inside polysim yet.** In the combined project these come from arbcheck's guard and frame's hash-chained audit log; polysim should only ever receive trades that already passed both.
- **No multi-leg basket with unwind yet.** Legs of an arbitrage are separate orders today, so leg risk (one leg fills, the next doesn't) is not measured.

## Writing a strategy

```python
from polysim import Backtester, Strategy, TimeInForce
from polysim.data import generate_binary_market

class Momentum(Strategy):
    def __init__(self):
        self.last = {}

    def on_book(self, ctx, book):
        prev, self.last[book.token_id] = self.last.get(book.token_id), book.mid
        if prev is None or book.mid is None:
            return
        held = ctx.position(book.token_id).qty
        if book.mid - prev > 0.02 and held == 0:
            ctx.buy(book.token_id, 50, price=book.best_ask, tif=TimeInForce.FAK)
        elif book.mid < prev and held > 0:
            ctx.sell(book.token_id, held)  # no price = market order (FAK)

market, events = generate_binary_market(seed=42)
print(Backtester(events, [market], Momentum(), initial_cash=1_000).run().summary())
```

## Next steps

In priority order, matching the hackathon judging:

1. **Basket execution with unwind**: fill an approved arbitrage leg by leg with a delay between legs, unwind if a leg fails, and report the leg-risk cost.
2. **Use the shared pieces**: arbcheck's fee table (all categories) and read-only client, and frame's hash-chained audit log for every paper fill.
3. **Record trades as well as books**, then add a queue model for maker orders.
4. **Mark at best bid**, round fees like Polymarket, and flag disputed results for manual settlement.
5. **Evaluation script**: tune on an earlier recorded period, test once on a later one, and report the funnel (naive flags → after fees → after depth → after rule check → realised at resolution) with uncertainty.
