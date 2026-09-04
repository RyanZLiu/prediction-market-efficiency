from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Venue(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"
    SPORTSBOOK = "sportsbook"


class MarketType(StrEnum):
    BINARY = "binary"
    MULTI = "multi"
    UNKNOWN = "unknown"


class Market(BaseModel):
    venue: Venue
    market_id: str
    event_id: str | None = None
    question: str
    outcome: str = "YES"
    token_id: str | None = None
    category: str | None = None
    market_type: MarketType = MarketType.BINARY
    start_time: datetime | None = None
    close_time: datetime | None = None
    active: bool = True
    volume: float | None = None
    liquidity: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utcnow)


class OrderBookLevel(BaseModel):
    price: float
    size: float


class OrderBook(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    venue: Venue
    market_id: str
    token_id: str | None = None
    outcome: str = "YES"
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    last: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def best_bid(self) -> OrderBookLevel | None:
        return max(self.bids, key=lambda x: x.price) if self.bids else None

    @property
    def best_ask(self) -> OrderBookLevel | None:
        return min(self.asks, key=lambda x: x.price) if self.asks else None


class Quote(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    venue: Venue
    market_id: str
    token_id: str | None = None
    outcome: str = "YES"
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    last: float | None = None
    volume: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def midpoint(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


class MarketMapping(BaseModel):
    canonical_event_id: str
    canonical_outcome: str
    venue: Venue
    market_id: str
    token_id: str | None = None
    question: str | None = None
    confidence: float = 1.0
    verified: bool = False


class Opportunity(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    canonical_event_id: str
    canonical_outcome: str
    buy_venue: Venue
    buy_market_id: str
    buy_price: float
    buy_size: float | None = None
    hedge_venue: Venue
    hedge_market_id: str
    hedge_yes_bid: float
    hedge_no_price: float
    hedge_size: float | None = None
    gross_edge: float
    estimated_cost: float
    net_edge: float
    available_size: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConstraintKind(StrEnum):
    SUBSET = "subset"
    COMPLEMENT = "complement"
    MUTUALLY_EXCLUSIVE = "mutually_exclusive"
    EXHAUSTIVE = "exhaustive"
    MONOTONIC_DESC = "monotonic_desc"


class ProbabilityConstraint(BaseModel):
    kind: ConstraintKind
    variables: list[str]
    name: str | None = None
    tolerance: float = 1e-9


class ConstraintViolation(BaseModel):
    constraint: ProbabilityConstraint
    magnitude: float
    message: str


class PaperTrade(BaseModel):
    timestamp: datetime
    canonical_event_id: str
    canonical_outcome: str
    quantity: float
    gross_edge: float
    net_edge: float
    locked_cost_per_contract: float
    pnl: float
    buy_venue: Venue
    hedge_venue: Venue


class SportsbookQuote(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    sport_key: str
    event_id: str
    commence_time: datetime | None = None
    home_team: str | None = None
    away_team: str | None = None
    bookmaker: str
    market_key: str
    outcome: str
    american_odds: float
    point: float | None = None
    implied_probability: float
    fair_probability: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
