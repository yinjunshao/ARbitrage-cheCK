# polysim: Polymarket trade simulation / backtesting framework

An event-driven simulator for trading Polymarket outcome tokens (YES/NO shares). This is the **foundation** release: data ingestion, order matching, the account, the strategy interface, the backtest engine, metrics and the CLI all work end to end, so the matching model and strategies can be iterated on top of it.

It only uses Polymarket's **public read-only endpoints** and never places real orders. All results are paper results.

## Quick start

```bash
pip install -e ".[dev]"
pytest

# Example strategies on a synthetic market (no network needed)
polysim backtest --strategy buy_and_hold --seed 1
polysim backtest --strategy mean_reversion --param window=20 --param size=30
polysim backtest --strategy market_maker --fill-mode through --fee-category crypto --latency 5

# Real data (needs access to gamma-api.polymarket.com / clob.polymarket.com)
polysim markets --limit 20
polysim fetch <market-slug> --fidelity 60 --out events.jsonl --markets-out markets.json
polysim record <market-slug> --interval 5 --duration 3600 --out books.jsonl
polysim backtest --strategy mean_reversion --events events.jsonl --markets markets.json
```

## Architecture

```
            ┌──────────────── data/ ─────────────────┐
            │ PolymarketClient (Gamma + CLOB REST)   │
            │ record_books → JSONL book snapshots    │
            │ fetch → JSONL price history + results  │
            │ synthetic → reproducible binary markets│
            └───────────────┬────────────────────────┘
                            │ Event stream (BookEvent / PriceEvent / ResolutionEvent)
                            ▼
┌──────────── Backtester (engine.py) ──────────────┐
│ advance time → SimExchange matches → Portfolio   │
│          ↕ StrategyContext (buy/sell/cancel)     │
│        Strategy.on_book / on_fill / on_resolution│
└───────────────┬──────────────────────────────────┘
                ▼
     BacktestResult: equity curve, fills, orders, metrics
```

| Module | Responsibility |
| --- | --- |
| `polysim/models.py` | `Market`, `Token`, `OrderBook`, `Order`, `Fill` and enums |
| `polysim/events.py` | Event types and JSON serialisation |
| `polysim/exchange.py` | Simulated CLOB: takers walk the book level by level, maker fills, FOK/FAK/GTC, latency, cash/share reservation |
| `polysim/portfolio.py` | Cash, positions, average cost, realised/unrealised PnL, settlement |
| `polysim/fees.py` | Polymarket fee formula `feeRate × p × (1−p) × shares`, rate chosen by category (e.g. crypto 0.07: 100 shares @0.50 → $1.75). Only the crypto rate is entered; other categories raise an error asking for `taker_rate` |
| `polysim/engine.py` | Backtest main loop; `BookSynthesizer` turns price-only data into order books |
| `polysim/strategy.py` | `Strategy` base class + `StrategyContext` |
| `polysim/metrics.py` | Return, max drawdown, Sharpe, volume, fees |
| `polysim/data/` | API client, JSONL read/write, book recording, synthetic data |
| `polysim/strategies/` | Examples: `buy_and_hold`, `mean_reversion`, `market_maker` |

## Simulation rules (current assumptions)

- **Prices and shares**: a price is a probability in (0, 1), validated against the market's `tick_size`. Winning shares settle at 1 USDC, losing shares at 0.
- **Long only**: Polymarket has no naked shorting, so to bet against YES you buy NO. You cannot sell more than you hold.
- **Taker orders**: walk the opposite side level by level, with an optional price limit. Liquidity we consume does not come back until the next snapshot arrives, so the same size cannot be filled twice. If cash runs short, the order fills as much as it can afford.
- **Maker orders**: recorded books do not contain our orders, so a resting order fills at its own price, as a maker, when a later snapshot's opposite price reaches it (`touch`) or trades through it (`through`, more conservative). There is no queue-position model.
- **Latency**: with `latency > 0`, an order arrives at `submit time + latency` and matches against the book in force at that moment.
- **Reservation**: a resting GTC order reserves cash (buys) or shares (sells), released on cancel or fill.
- **Settlement**: a `ResolutionEvent` cancels every open order in that market, pays positions according to `payouts` and marks the market closed.
- **No look-ahead**: orders a strategy places while handling an event only match against books it has already seen.

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

## Possible next steps

- WebSocket live books + trade prints (trade-based maker fills and a queue model)
- YES/NO complement matching (buy YES @p ≈ sell NO @1−p) and mint/merge
- Multi-market portfolios, position and risk limits
- Parameter sweeps, result visualisation, a paper-trading mode (the same Strategy on live data)
