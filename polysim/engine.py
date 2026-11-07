"""Event-driven backtest loop.

For each event (in timestamp order):

1. deliver latency-delayed orders that have arrived by ``event.ts``;
2. update the book (resting orders may fill) or settle a resolved market;
3. notify the strategy of new fills, then of the event itself;
4. deliver fills caused by orders the strategy just sent (zero latency);
5. record an equity snapshot marked at mid prices.

Orders a strategy sends while handling an event are matched against the book
it just saw (or, with latency, the book in effect when they arrive) – the
engine never lets a strategy trade on a future snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

from . import metrics
from .events import BookEvent, Event, PriceEvent, ResolutionEvent
from .exchange import FillMode, SimExchange
from .fees import FeeModel
from .models import Fill, Market, Order, OrderBook
from .portfolio import Portfolio
from .strategy import Strategy, StrategyContext


@dataclass(slots=True)
class BookSynthesizer:
    """Turns a bare price observation into an L2 book (for price-history-only data)."""

    spread: float = 0.02
    levels: int = 5
    size_per_level: float = 500.0

    def make(self, token_id: str, ts: float, price: float, tick: float) -> OrderBook:
        return OrderBook.from_mid(
            token_id,
            ts,
            price,
            spread=self.spread,
            levels=self.levels,
            size_per_level=self.size_per_level,
            tick=tick,
        )


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: list[tuple[float, float]]
    fills: list[Fill]
    orders: list[Order]
    final_portfolio: dict
    resolutions: list[ResolutionEvent] = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"final equity   {m['final_equity']:>12.2f} USDC  (start {m['initial_cash']:.2f})",
            f"pnl            {m['pnl']:>12.2f} USDC  ({m['return_pct']:+.2f}%)",
            f"realized pnl   {m['realized_pnl']:>12.2f} USDC",
            f"max drawdown   {m['max_drawdown_pct']:>12.2f} %",
            f"sharpe/step    {m['sharpe_per_step']:>12.4f}",
            f"fills          {m['n_fills']:>12d}  (maker {m['maker_fills']})",
            f"volume         {m['volume_usdc']:>12.2f} USDC",
            f"fees           {m['fees_paid']:>12.4f} USDC",
        ]
        return "\n".join(lines)


class Backtester:
    def __init__(
        self,
        events: Iterable[Event],
        markets: Iterable[Market],
        strategy: Strategy,
        *,
        initial_cash: float = 1_000.0,
        fee_model: FeeModel | None = None,
        latency: float = 0.0,
        fill_mode: FillMode | str = FillMode.TOUCH,
        synthesizer: BookSynthesizer | None = None,
    ) -> None:
        self.events = events
        self.markets: dict[str, Market] = {m.market_id: m for m in markets}
        self.strategy = strategy
        self.portfolio = Portfolio(initial_cash)
        self.exchange = SimExchange(
            self.portfolio,
            self.markets.values(),
            fee_model=fee_model,
            latency=latency,
            fill_mode=FillMode(fill_mode),
        )
        self.synthesizer = synthesizer or BookSynthesizer()
        self.ctx = StrategyContext(self)
        self.marks: dict[str, float] = {}
        self.equity_curve: list[tuple[float, float]] = []
        self.resolutions: list[ResolutionEvent] = []

    def equity(self) -> float:
        return self.portfolio.equity(self.marks)

    def run(self) -> BacktestResult:
        self.strategy.on_start(self.ctx)
        last_ts = None
        for ev in self.events:
            if last_ts is not None and ev.ts < last_ts:
                raise ValueError(f"events out of order: {ev.ts} < {last_ts}")
            last_ts = ev.ts
            self._step(ev)
        self.strategy.on_end(self.ctx)
        self._dispatch_fills()
        return self._result()

    # ------------------------------------------------------------------ internals

    def _step(self, ev: Event) -> None:
        ex = self.exchange
        ex.advance_time(ev.ts)
        self._dispatch_fills()

        if isinstance(ev, PriceEvent):
            ev = BookEvent(ev.ts, self.synthesizer.make(ev.token_id, ev.ts, ev.price, ex.tick_size(ev.token_id)))

        if isinstance(ev, BookEvent):
            ex.on_book(ev.book)
            if ev.book.mid is not None:
                self.marks[ev.token_id] = ev.book.mid
            self._dispatch_fills()
            self.strategy.on_book(self.ctx, ex.book(ev.token_id))
        elif isinstance(ev, ResolutionEvent):
            self._resolve(ev)
            self.strategy.on_resolution(self.ctx, ev)
        else:
            raise TypeError(f"unsupported event {ev!r}")

        self._dispatch_fills()
        self.equity_curve.append((ev.ts, self.equity()))

    def _resolve(self, ev: ResolutionEvent) -> None:
        self.resolutions.append(ev)
        self.exchange.cancel_all(ev.payouts.keys())
        for token_id, payout in ev.payouts.items():
            self.portfolio.settle(token_id, payout)
            self.marks[token_id] = payout
        m = self.markets.get(ev.market_id)
        if m is not None:
            closed = replace(m, closed=True, active=False)
            self.markets[ev.market_id] = closed
            self.exchange.add_market(closed)

    def _dispatch_fills(self) -> None:
        # on_fill may place orders that fill immediately; loop until quiet.
        while fills := self.exchange.drain_fills():
            for f in fills:
                self.strategy.on_fill(self.ctx, f)

    def _result(self) -> BacktestResult:
        eq = [v for _, v in self.equity_curve]
        return BacktestResult(
            metrics=metrics.summarize(
                self.portfolio.initial_cash,
                eq,
                self.exchange.fills,
                self.portfolio.realized_pnl,
                self.portfolio.fees_paid,
            ),
            equity_curve=self.equity_curve,
            fills=list(self.exchange.fills),
            orders=list(self.exchange.orders.values()),
            final_portfolio=self.portfolio.snapshot(self.marks),
            resolutions=self.resolutions,
        )
