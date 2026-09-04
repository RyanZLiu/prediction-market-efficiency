from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from pme.collectors.base import MarketDataClient
from pme.config import settings
from pme.models import Market, MarketType, OrderBook, OrderBookLevel, Quote, Venue, utcnow


def _parse_jsonish(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


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


class PolymarketClient(MarketDataClient):
    def __init__(
        self,
        gamma_url: str | None = None,
        clob_url: str | None = None,
        timeout: int | None = None,
    ):
        self.gamma_url = (gamma_url or settings.polymarket_gamma_url).rstrip("/")
        self.clob_url = (clob_url or settings.polymarket_clob_url).rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=timeout or settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
        )

    async def __aenter__(self) -> PolymarketClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    async def list_markets(self, limit: int = 100) -> list[Market]:
        results: list[Market] = []
        offset = 0
        page_size = min(100, max(1, limit))
        while len(results) < limit:
            response = await self.client.get(
                f"{self.gamma_url}/markets",
                params={
                    "limit": min(page_size, limit - len(results)),
                    "offset": offset,
                    "closed": "false",
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                market = self._normalize_market(row)
                if market is not None:
                    results.append(market)
            offset += len(rows)
            if len(rows) < page_size:
                break
        return results[:limit]

    def _normalize_market(self, row: dict[str, Any]) -> Market | None:
        outcomes = [str(x) for x in _parse_jsonish(row.get("outcomes"))]
        token_ids = [str(x) for x in _parse_jsonish(row.get("clobTokenIds"))]
        yes_token: str | None = None
        for index, outcome in enumerate(outcomes):
            if outcome.strip().lower() == "yes" and index < len(token_ids):
                yes_token = token_ids[index]
                break
        if yes_token is None and token_ids:
            yes_token = token_ids[0]

        question = row.get("question") or row.get("slug") or str(row.get("id", ""))
        return Market(
            venue=Venue.POLYMARKET,
            market_id=str(row.get("id")),
            event_id=row.get("conditionId") or row.get("condition_id"),
            question=question,
            outcome="YES",
            token_id=yes_token,
            category=row.get("category"),
            market_type=MarketType.BINARY,
            start_time=_dt(row.get("startDate") or row.get("startDateIso")),
            close_time=_dt(row.get("endDate") or row.get("endDateIso")),
            active=bool(row.get("active", not row.get("closed", False))),
            volume=_f(row.get("volumeNum") or row.get("volume")),
            liquidity=_f(row.get("liquidityNum") or row.get("liquidity")),
            metadata=row,
        )

    async def get_orderbook(self, market_id: str, token_id: str | None = None) -> OrderBook:
        if not token_id:
            raise ValueError(
                "Polymarket order books require the YES token_id from market discovery/mapping."
            )
        response = await self.client.get(f"{self.clob_url}/book", params={"token_id": token_id})
        response.raise_for_status()
        payload = response.json()
        bids = [
            OrderBookLevel(price=float(level["price"]), size=float(level["size"]))
            for level in payload.get("bids", [])
        ]
        asks = [
            OrderBookLevel(price=float(level["price"]), size=float(level["size"]))
            for level in payload.get("asks", [])
        ]
        return OrderBook(
            timestamp=utcnow(),
            venue=Venue.POLYMARKET,
            market_id=market_id,
            token_id=token_id,
            bids=bids,
            asks=asks,
            last=_f(payload.get("last_trade_price")),
            raw=payload,
        )

    async def get_quote(self, market_id: str, token_id: str | None = None) -> Quote:
        book = await self.get_orderbook(market_id, token_id)
        bid = book.best_bid
        ask = book.best_ask
        return Quote(
            timestamp=book.timestamp,
            venue=book.venue,
            market_id=market_id,
            token_id=token_id,
            bid=bid.price if bid else None,
            ask=ask.price if ask else None,
            bid_size=bid.size if bid else None,
            ask_size=ask.size if ask else None,
            last=book.last,
            raw=book.raw,
        )

    async def price_history(
        self,
        token_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        interval: str = "all",
        fidelity: int = 1,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"market": token_id, "interval": interval, "fidelity": fidelity}
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts
        response = await self.client.get(f"{self.clob_url}/prices-history", params=params)
        response.raise_for_status()
        return response.json().get("history", [])
