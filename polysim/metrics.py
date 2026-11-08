"""Performance metrics computed from an equity curve and fills."""

from __future__ import annotations

import math
from typing import Sequence

from .models import Fill


def max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough decline as a fraction of the peak (0.25 == -25%)."""
    peak, mdd = -math.inf, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def sharpe(equity: Sequence[float], periods_per_year: float | None = None) -> float:
    """Sharpe of per-step simple returns (risk-free = 0). Annualised if periods_per_year given."""
    rets = [b / a - 1 for a, b in zip(equity, equity[1:]) if a > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return 0.0
    s = mean / math.sqrt(var)
    return s * math.sqrt(periods_per_year) if periods_per_year else s


def summarize(
    initial_cash: float,
    equity: Sequence[float],
    fills: Sequence[Fill],
    realized_pnl: float,
    fees: float,
) -> dict:
    final = equity[-1] if equity else initial_cash
    return {
        "initial_cash": initial_cash,
        "final_equity": final,
        "pnl": final - initial_cash,
        "return_pct": (final / initial_cash - 1) * 100 if initial_cash else 0.0,
        "realized_pnl": realized_pnl,
        "max_drawdown_pct": max_drawdown(equity) * 100,
        "sharpe_per_step": sharpe(equity),
        "n_fills": len(fills),
        "volume_usdc": sum(f.notional for f in fills),
        "maker_fills": sum(1 for f in fills if f.liquidity.value == "MAKER"),
        "fees_paid": fees,
    }
