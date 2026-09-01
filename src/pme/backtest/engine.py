from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pme.models import Opportunity, PaperTrade


@dataclass(frozen=True)
class BacktestConfig:
    min_net_edge: float = 0.005
    max_contracts: float = 100.0
    cooldown_seconds: int = 300
    min_available_size: float = 1.0


class PaperBacktester:
    def __init__(self, config: BacktestConfig | None = None):
        self.config = config or BacktestConfig()

    def run(self, opportunities: list[Opportunity]) -> list[PaperTrade]:
        trades: list[PaperTrade] = []
        last_trade: dict[tuple[str, str], object] = {}
        cooldown = timedelta(seconds=self.config.cooldown_seconds)

        for opp in sorted(opportunities, key=lambda x: x.timestamp):
            if opp.net_edge < self.config.min_net_edge:
                continue
            if opp.available_size is not None and opp.available_size < self.config.min_available_size:
                continue
            key = (opp.canonical_event_id, opp.canonical_outcome)
            previous = last_trade.get(key)
            if previous is not None and opp.timestamp - previous < cooldown:
                continue
            quantity = self.config.max_contracts
            if opp.available_size is not None:
                quantity = min(quantity, opp.available_size)
            if quantity <= 0:
                continue
            locked_cost = opp.buy_price + opp.hedge_no_price + opp.estimated_cost
            pnl = opp.net_edge * quantity
            trades.append(PaperTrade(
                timestamp=opp.timestamp,
                canonical_event_id=opp.canonical_event_id,
                canonical_outcome=opp.canonical_outcome,
                quantity=quantity,
                gross_edge=opp.gross_edge,
                net_edge=opp.net_edge,
                locked_cost_per_contract=locked_cost,
                pnl=pnl,
                buy_venue=opp.buy_venue,
                hedge_venue=opp.hedge_venue,
            ))
            last_trade[key] = opp.timestamp
        return trades
