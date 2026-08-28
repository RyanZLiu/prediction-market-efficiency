from __future__ import annotations

from abc import ABC, abstractmethod

from pme.models import Market, OrderBook, Quote


class MarketDataClient(ABC):
    @abstractmethod
    async def list_markets(self, limit: int = 100) -> list[Market]: ...

    @abstractmethod
    async def get_orderbook(self, market_id: str, token_id: str | None = None) -> OrderBook: ...

    async def get_quote(self, market_id: str, token_id: str | None = None) -> Quote:
        book = await self.get_orderbook(market_id=market_id, token_id=token_id)
        bid = book.best_bid
        ask = book.best_ask
        return Quote(
            timestamp=book.timestamp,
            venue=book.venue,
            market_id=book.market_id,
            token_id=book.token_id,
            outcome=book.outcome,
            bid=bid.price if bid else None,
            ask=ask.price if ask else None,
            bid_size=bid.size if bid else None,
            ask_size=ask.size if ask else None,
            last=book.last,
            raw=book.raw,
        )
