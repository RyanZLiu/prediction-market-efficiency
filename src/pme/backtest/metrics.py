from __future__ import annotations

import math
from statistics import mean, pstdev

from pme.models import PaperTrade


def max_drawdown(values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return abs(worst)


def summarize_trades(trades: list[PaperTrade]) -> dict[str, float]:
    if not trades:
        return {
            "trades": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
            "pseudo_sharpe": 0.0,
            "max_drawdown": 0.0,
            "avg_net_edge": 0.0,
        }
    pnls = [t.pnl for t in trades]
    cumulative: list[float] = []
    running = 0.0
    for pnl in pnls:
        running += pnl
        cumulative.append(running)
    sigma = pstdev(pnls) if len(pnls) > 1 else 0.0
    sharpe = mean(pnls) / sigma * math.sqrt(len(pnls)) if sigma > 0 else 0.0
    return {
        "trades": float(len(trades)),
        "total_pnl": sum(pnls),
        "avg_pnl": mean(pnls),
        "win_rate": sum(p > 0 for p in pnls) / len(pnls),
        "pseudo_sharpe": sharpe,
        "max_drawdown": max_drawdown(cumulative),
        "avg_net_edge": mean(t.net_edge for t in trades),
    }
