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
    if isinstance(value, dict):
        value = value.get("value")
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


def _fee_coefficient(value: Any) -> float:
    parsed = _f(value)
    if parsed is None:
        return settings.polymarket_us_taker_fee_coefficient
    # Some APIs expose percentages as whole numbers; normalize defensively.
    if parsed > 1.0:
        parsed /= 100.0
    return parsed


class PolymarketClient(MarketDataClient):
    """Polymarket US public market-data client.

    The public gateway does not require credentials. `Market.token_id` stores the
    Polymarket US market slug for backward compatibility with the existing
    cross-venue mapping model, which historically used that field for CLOB token IDs.
    """

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or settings.polymarket_us_gateway_url).rstrip("/")
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
        page_size = min(500, max(1, limit))

        while len(results) < limit:
            size = min(page_size, limit - len(results))
            params: dict[str, Any] = {
                "limit": size,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "archived": "false",
                "includeHidden": "false",
                "orderDirection": "desc",
            }
            response = await self.client.get(f"{self.base_url}/v1/markets", params=params)
            if response.status_code in {400, 422} and page_size > 100:
                page_size = 100
                continue
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("markets", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list) or not rows:
                break

            for row in rows:
                if not isinstance(row, dict):
                    continue
                market = self._normalize_market(row)
                if market is not None:
                    results.append(market)
                if len(results) >= limit:
                    break

            offset += len(rows)
            if len(rows) < size:
                break

        return results[:limit]

    async def list_sports_markets(self, limit: int = 1000) -> list[Market]:
        # Polymarket US exposes sports fields directly on market objects. Pull a
        # broader active universe and retain markets with sports metadata.
        universe = await self.list_markets(max(limit, min(5000, limit * 3)))
        sports = [
            market
            for market in universe
            if any(
                market.metadata.get(key)
                for key in (
                    "sportsMarketType",
                    "sportsMarketTypeV2",
                    "gameId",
                    "gameStartTime",
                )
            )
        ]
        return sports[:limit]

    def _normalize_market(self, row: dict[str, Any]) -> Market | None:
        market_id = str(row.get("id") or "")
        slug = str(row.get("slug") or "")
        if not market_id or not slug:
            return None

        question = row.get("question") or row.get("title") or slug
        metadata = dict(row)
        metadata["polymarket_us"] = True

        return Market(
            venue=Venue.POLYMARKET,
            market_id=market_id,
            event_id=None,
            question=str(question),
            outcome="YES",
            token_id=slug,
            category=row.get("category"),
            market_type=MarketType.BINARY,
            start_time=_dt(row.get("startDate") or row.get("gameStartTime")),
            close_time=_dt(row.get("endDate")),
            active=bool(row.get("active", True))
            and not bool(row.get("closed", False))
            and not bool(row.get("archived", False)),
            volume=_f(row.get("volume") or row.get("volumeNum")),
            liquidity=_f(row.get("liquidity") or row.get("liquidityNum")),
            metadata=metadata,
        )

    async def get_market(self, market_id: str) -> dict[str, Any]:
        response = await self.client.get(f"{self.base_url}/v1/market/id/{market_id}")
        response.raise_for_status()
        payload = response.json()
        market = payload.get("market") if isinstance(payload, dict) else None
        if not isinstance(market, dict):
            raise ValueError("Unexpected Polymarket US market response")
        return market

    async def get_bbo(self, slug: str) -> dict[str, Any]:
        response = await self.client.get(f"{self.base_url}/v1/markets/{slug}/bbo")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("marketData") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("Unexpected Polymarket US BBO response")
        return data

    async def get_orderbook(self, market_id: str, token_id: str | None = None) -> OrderBook:
        slug = token_id
        if not slug:
            market = await self.get_market(market_id)
            slug = str(market.get("slug") or "")
        if not slug:
            raise ValueError("Polymarket US order books require a market slug")

        response = await self.client.get(f"{self.base_url}/v1/markets/{slug}/book")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("marketData") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("Unexpected Polymarket US book response")

        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        for level in data.get("bids") or []:
            if not isinstance(level, dict):
                continue
            price = _f(level.get("px"))
            size = _f(level.get("qty"))
            if price is not None and size is not None:
                bids.append(OrderBookLevel(price=price, size=size))
        for level in data.get("offers") or []:
            if not isinstance(level, dict):
                continue
            price = _f(level.get("px"))
            size = _f(level.get("qty"))
            if price is not None and size is not None:
                asks.append(OrderBookLevel(price=price, size=size))

        stats = data.get("stats") or {}
        timestamp = _dt(data.get("transactTime")) or utcnow()
        return OrderBook(
            timestamp=timestamp,
            venue=Venue.POLYMARKET,
            market_id=market_id,
            token_id=slug,
            bids=bids,
            asks=asks,
            last=_f(stats.get("lastTradePx")),
            raw=payload,
        )

    async def get_quote(self, market_id: str, token_id: str | None = None) -> Quote:
        slug = token_id
        if not slug:
            market = await self.get_market(market_id)
            slug = str(market.get("slug") or "")
        if not slug:
            raise ValueError("Polymarket US BBO requires a market slug")

        data = await self.get_bbo(slug)
        # The public BBO is a fresh snapshot. Use receipt time for freshness;
        # lastPriceSample.ts is a trade/sample time and can be stale even when BBO changes.
        timestamp = utcnow()
        return Quote(
            timestamp=timestamp,
            venue=Venue.POLYMARKET,
            market_id=market_id,
            token_id=slug,
            bid=_f(data.get("bestBid")),
            ask=_f(data.get("bestAsk")),
            last=_f(data.get("lastTradePx")),
            raw=data,
        )

    async def get_binary_executable_book(self, market: Market) -> dict[str, Any]:
        """Return executable YES and synthetic-NO prices from the actual US book.

        Polymarket US has one instrument per outcome. Buying the instrument is
        YES; shorting that same instrument is NO. Therefore the executable YES
        ask is the best offer, while the effective cost of a NO/short position
        is 1 - the executable YES bid. Sizes come from those exact book levels.
        """
        slug = market.token_id or str(market.metadata.get("slug") or "")
        if not slug:
            raise ValueError("Polymarket US market slug is missing")

        book = await self.get_orderbook(market.market_id, slug)
        yes_bid = book.best_bid
        yes_ask = book.best_ask
        coefficient = _fee_coefficient(market.metadata.get("feeCoefficient"))

        return {
            "timestamp": book.timestamp,
            "market_id": market.market_id,
            "market_slug": slug,
            "yes_bid": None if yes_bid is None else yes_bid.price,
            "yes_bid_size": None if yes_bid is None else yes_bid.size,
            "yes_ask": None if yes_ask is None else yes_ask.price,
            "yes_ask_size": None if yes_ask is None else yes_ask.size,
            "no_bid": None if yes_ask is None else 1.0 - yes_ask.price,
            "no_bid_size": None if yes_ask is None else yes_ask.size,
            "no_ask": None if yes_bid is None else 1.0 - yes_bid.price,
            "no_ask_size": None if yes_bid is None else yes_bid.size,
            "fee_rate": coefficient,
            "fee_exponent": 2.0,
            "taker_only": True,
            "minimum_order_size": _f(market.metadata.get("minimumTradeQty")),
            "raw": book.raw,
        }

    async def price_history(
        self,
        token_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        interval: str = "all",
        fidelity: int = 1,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": token_id}
        if start_ts is not None and end_ts is not None:
            params["timestamp.startTimestamp"] = start_ts
            params["timestamp.endTimestamp"] = end_ts
            params["fidelity"] = max(1, fidelity)
        else:
            profiles = {
                "all": ("INTERVAL_ALL", 180),
                "1m": ("INTERVAL_1M", 180),
                "1w": ("INTERVAL_1W", 180),
                "1d": ("INTERVAL_1D", 5),
                "6h": ("INTERVAL_6H", 1),
                "1h": ("INTERVAL_1H", 1),
                "live": ("INTERVAL_LIVE", 1),
            }
            fixed, required_fidelity = profiles.get(interval.lower(), profiles["all"])
            params["fixedInterval"] = fixed
            params["fidelity"] = required_fidelity
        response = await self.client.get(f"{self.base_url}/v1/price-history", params=params)
        response.raise_for_status()
        payload = response.json()
        return payload.get("history", []) if isinstance(payload, dict) else []
