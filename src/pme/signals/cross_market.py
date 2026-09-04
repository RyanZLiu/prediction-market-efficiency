from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from pme.models import MarketMapping, Opportunity, Quote, Venue, utcnow


@dataclass(frozen=True)
class CostModel:
    fee_bps: dict[Venue, float] = field(default_factory=dict)
    slippage_bps: float = 0.0

    def cost_per_contract(
        self, buy_venue: Venue, hedge_venue: Venue, buy_price: float, hedge_no_price: float
    ) -> float:
        buy_fee = buy_price * self.fee_bps.get(buy_venue, 0.0) / 10_000.0
        hedge_fee = hedge_no_price * self.fee_bps.get(hedge_venue, 0.0) / 10_000.0
        slippage = 2.0 * self.slippage_bps / 10_000.0
        return buy_fee + hedge_fee + slippage


def _available_size(a: Quote, b: Quote) -> float | None:
    sizes = [x for x in [a.ask_size, b.bid_size] if x is not None]
    return min(sizes) if sizes else None


def detect_cross_venue(
    quotes: list[Quote],
    mappings: list[MarketMapping],
    cost_model: CostModel | None = None,
    min_net_edge: float = 0.0,
    max_pair_skew_seconds: float | None = 90.0,
    max_quote_age_seconds: float | None = 180.0,
) -> list[Opportunity]:
    cost_model = cost_model or CostModel()
    now = utcnow()
    filtered = [
        q
        for q in quotes
        if max_quote_age_seconds is None
        or (now - q.timestamp).total_seconds() <= max_quote_age_seconds
    ]
    quote_by_key = {(q.venue, q.market_id): q for q in filtered}
    grouped: dict[tuple[str, str], list[tuple[MarketMapping, Quote]]] = {}
    for mapping in mappings:
        q = quote_by_key.get((mapping.venue, mapping.market_id))
        if q is None or q.bid is None or q.ask is None:
            continue
        grouped.setdefault((mapping.canonical_event_id, mapping.canonical_outcome), []).append(
            (mapping, q)
        )

    opportunities: list[Opportunity] = []
    for (event_id, outcome), rows in grouped.items():
        for (_ma, qa), (_mb, qb) in combinations(rows, 2):
            if qa.venue == qb.venue:
                continue
            if (
                max_pair_skew_seconds is not None
                and abs((qa.timestamp - qb.timestamp).total_seconds()) > max_pair_skew_seconds
            ):
                continue
            directions = [(qa, qb), (qb, qa)]
            for buy, hedge in directions:
                if buy.ask is None or hedge.bid is None:
                    continue
                # Buy YES cheaply; hedge by buying NO on the venue with the expensive YES bid.
                hedge_no_price = 1.0 - hedge.bid
                gross_edge = 1.0 - (buy.ask + hedge_no_price)  # == hedge.bid - buy.ask
                cost = cost_model.cost_per_contract(buy.venue, hedge.venue, buy.ask, hedge_no_price)
                net = gross_edge - cost
                if net < min_net_edge:
                    continue
                opportunities.append(
                    Opportunity(
                        timestamp=max(buy.timestamp, hedge.timestamp),
                        canonical_event_id=event_id,
                        canonical_outcome=outcome,
                        buy_venue=buy.venue,
                        buy_market_id=buy.market_id,
                        buy_price=buy.ask,
                        buy_size=buy.ask_size,
                        hedge_venue=hedge.venue,
                        hedge_market_id=hedge.market_id,
                        hedge_yes_bid=hedge.bid,
                        hedge_no_price=hedge_no_price,
                        hedge_size=hedge.bid_size,
                        gross_edge=gross_edge,
                        estimated_cost=cost,
                        net_edge=net,
                        available_size=_available_size(buy, hedge),
                    )
                )
    opportunities.sort(key=lambda x: x.net_edge, reverse=True)
    return opportunities
