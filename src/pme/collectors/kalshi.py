from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from pme.collectors.base import MarketDataClient
from pme.config import settings
from pme.models import Market, MarketType, OrderBook, OrderBookLevel, Quote, Venue, utcnow


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class KalshiClient(MarketDataClient):
    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or settings.kalshi_base_url).rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=timeout or settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
        )

    async def __aenter__(self) -> KalshiClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    async def list_markets(self, limit: int = 100) -> list[Market]:
        results: list[Market] = []
        cursor: str | None = None
        while len(results) < limit:
            page_size = min(1000, limit - len(results))
            params: dict[str, Any] = {
                "limit": page_size,
                "status": "open",
                "mve_filter": "exclude",
            }
            if cursor:
                params["cursor"] = cursor
            response = await self.client.get(f"{self.base_url}/markets", params=params)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("markets", [])
            for row in rows:
                results.append(self._normalize_market(row))
            cursor = payload.get("cursor") or None
            if not rows or not cursor:
                break
        return results[:limit]

    def _normalize_market(self, row: dict[str, Any]) -> Market:
        question = row.get("title") or row.get("yes_sub_title") or row.get("ticker", "")
        return Market(
            venue=Venue.KALSHI,
            market_id=str(row.get("ticker")),
            event_id=row.get("event_ticker"),
            question=question,
            outcome="YES",
            category=row.get("category"),
            market_type=MarketType.BINARY,
            start_time=_dt(row.get("open_time")),
            close_time=_dt(row.get("close_time")),
            active=row.get("status") == "open",
            volume=_f(row.get("volume_fp") or row.get("volume")),
            liquidity=None,
            metadata=row,
        )

    async def get_market(self, ticker: str) -> dict[str, Any]:
        response = await self.client.get(f"{self.base_url}/markets/{ticker}")
        response.raise_for_status()
        return response.json()["market"]

    async def get_orderbook(
        self, market_id: str, token_id: str | None = None, depth: int = 0
    ) -> OrderBook:
        del token_id
        response = await self.client.get(
            f"{self.base_url}/markets/{market_id}/orderbook", params={"depth": depth}
        )
        response.raise_for_status()
        payload = response.json()
        book = payload.get("orderbook_fp") or payload.get("orderbook") or {}

        yes_levels = [
            OrderBookLevel(price=float(price), size=float(size))
            for price, size, *_ in book.get("yes_dollars", [])
        ]
        no_bids = [
            OrderBookLevel(price=float(price), size=float(size))
            for price, size, *_ in book.get("no_dollars", [])
        ]
        # In a binary book, a NO bid at p is a YES ask at 1-p.
        yes_asks = [OrderBookLevel(price=1.0 - level.price, size=level.size) for level in no_bids]
        return OrderBook(
            timestamp=utcnow(),
            venue=Venue.KALSHI,
            market_id=market_id,
            bids=yes_levels,
            asks=yes_asks,
            raw=payload,
        )

    async def get_quote(self, market_id: str, token_id: str | None = None) -> Quote:
        del token_id
        # Market endpoint already exposes BBO fields. Fall back to the order book if needed.
        row = await self.get_market(market_id)
        bid = _f(row.get("yes_bid_dollars"))
        ask = _f(row.get("yes_ask_dollars"))
        if bid is None and ask is None:
            return await super().get_quote(market_id)
        return Quote(
            timestamp=utcnow(),
            venue=Venue.KALSHI,
            market_id=market_id,
            bid=bid,
            ask=ask,
            bid_size=_f(row.get("yes_bid_size_fp")),
            ask_size=_f(row.get("yes_ask_size_fp")),
            last=_f(row.get("last_price_dollars")),
            volume=_f(row.get("volume_fp")),
            raw=row,
        )
