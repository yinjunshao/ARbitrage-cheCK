# polysim — Polymarket 交易模拟 / 回测框架

基于事件驱动的 Polymarket outcome token（YES/NO 份额）交易模拟器。当前为**基础架构**版本：数据接入、撮合、账户、策略接口、回测引擎、指标和 CLI 都已打通，后续可以在此之上迭代撮合模型和策略。

只使用 Polymarket 的**公开只读接口**，不会下真实订单。

## 快速开始

```bash
pip install -e ".[dev]"
pytest

# 合成市场上跑示例策略（不需要网络）
polysim backtest --strategy buy_and_hold --seed 1
polysim backtest --strategy mean_reversion --param window=20 --param size=30
polysim backtest --strategy market_maker --fill-mode through --taker-bps 100 --latency 5

# 真实数据（需要能访问 gamma-api.polymarket.com / clob.polymarket.com）
polysim markets --limit 20
polysim fetch <market-slug> --fidelity 60 --out events.jsonl --markets-out markets.json
polysim record <market-slug> --interval 5 --duration 3600 --out books.jsonl
polysim backtest --strategy mean_reversion --events events.jsonl --markets markets.json
```

## 架构

```
            ┌──────────────── data/ ────────────────┐
            │ PolymarketClient (Gamma + CLOB REST)  │
            │ record_books → JSONL 盘口快照          │
            │ fetch  → JSONL 价格历史 + 结算         │
            │ synthetic → 可复现的合成二元市场        │
            └───────────────┬───────────────────────┘
                            │ Event 流 (BookEvent / PriceEvent / ResolutionEvent)
                            ▼
┌──────────── Backtester (engine.py) ─────────────┐
│ 按时间推进 → SimExchange 撮合 → Portfolio 记账   │
│          ↕ StrategyContext (buy/sell/cancel)     │
│        Strategy.on_book / on_fill / on_resolution│
└───────────────┬──────────────────────────────────┘
                ▼
     BacktestResult: 权益曲线、成交、订单、指标
```

| 模块 | 职责 |
| --- | --- |
| `polysim/models.py` | `Market`、`Token`、`OrderBook`、`Order`、`Fill` 及枚举 |
| `polysim/events.py` | 事件类型与 JSON 序列化 |
| `polysim/exchange.py` | 模拟 CLOB：吃单逐档成交、挂单 maker 成交、FOK/FAK/GTC、延迟、资金/持仓冻结 |
| `polysim/portfolio.py` | 现金、持仓、均价、已实现/未实现 PnL、结算 |
| `polysim/fees.py` | Polymarket 费率公式 `rate × min(p, 1−p) × shares` |
| `polysim/engine.py` | 回测主循环；`BookSynthesizer` 把纯价格数据转换成盘口 |
| `polysim/strategy.py` | `Strategy` 基类 + `StrategyContext` |
| `polysim/metrics.py` | 收益、最大回撤、Sharpe、成交量、费用 |
| `polysim/data/` | API 客户端、JSONL 读写、盘口录制、合成数据 |
| `polysim/strategies/` | 示例：`buy_and_hold`、`mean_reversion`、`market_maker` |

## 模拟规则（当前假设）

- **价格/份额**：价格是 (0,1) 内的概率，按市场 `tick_size` 校验；赢的份额结算为 1 USDC，输的为 0。
- **只做多**：Polymarket 不能裸卖空，想做空 YES 就买 NO。卖出数量不能超过可用持仓。
- **吃单**：沿对手盘逐档成交，可设价格上限；被吃掉的流动性在下一张快照到来前不会恢复，避免重复吃同一份量。现金不足时按可负担量部分成交。
- **挂单**：录制的盘口里没有我们的订单，所以当后续快照中对手价到达（`touch`）或穿过（`through`，更保守）我们的价格时，按我们的挂单价以 maker 成交。没有排队位置模型。
- **延迟**：`latency > 0` 时订单在 `提交时间 + latency` 到达，并与那一刻生效的盘口撮合。
- **冻结**：GTC 挂单会冻结现金（买）或份额（卖），撤单/成交后释放。
- **结算**：`ResolutionEvent` 撤销该市场全部挂单，按 `payouts` 兑付持仓，并把市场标记为关闭。
- **防前视**：策略在处理事件时下的单只会与它已看到的盘口撮合。

## 写一个策略

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
            ctx.sell(book.token_id, held)  # 不给价格 = 市价 (FAK)

market, events = generate_binary_market(seed=42)
print(Backtester(events, [market], Momentum(), initial_cash=1_000).run().summary())
```

## 后续可以做的

- WebSocket 实时盘口 + 逐笔成交（基于成交的挂单成交与排队模型）
- YES/NO 互补撮合（买 YES @p ≈ 卖 NO @1−p）与 mint/merge
- 多市场组合、仓位/风险限额
- 参数扫描、结果可视化、paper trading 模式（同一 Strategy 接实时数据）
