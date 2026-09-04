from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding

from pme.config import settings
from pme.models import Quote, Venue

QuoteHandler = Callable[[Quote], Awaitable[None]]


def _ms_to_dt(value: str | int | float | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    number = float(value)
    if number > 10_000_000_000:
        number /= 1000.0
    return datetime.fromtimestamp(number, tz=UTC)


def _iso_to_dt(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def _amount(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _level(level: Any) -> tuple[float, float] | None:
    if not isinstance(level, dict):
        return None
    price = _amount(level.get("px"))
    size = _amount(level.get("qty"))
    if price is None or size is None:
        return None
    return price, size


def _polymarket_us_headers(key_id: str, secret_key: str) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    path = "/v1/ws/markets"
    message = f"{timestamp}GET{path}".encode()
    raw_secret = base64.b64decode(secret_key)
    if len(raw_secret) < 32:
        raise ValueError("POLYMARKET_SECRET_KEY did not decode to an Ed25519 private key")
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(raw_secret[:32])
    signature = base64.b64encode(private_key.sign(message)).decode()
    return {
        "X-PM-Access-Key": key_id,
        "X-PM-Timestamp": timestamp,
        "X-PM-Signature": signature,
    }


async def stream_polymarket(
    token_to_market: dict[str, str],
    handler: QuoteHandler,
    seconds: int | None = None,
    url: str | None = None,
    key_id: str | None = None,
    secret_key: str | None = None,
) -> None:
    """Stream Polymarket US full order-book updates.

    `token_to_market` is retained as the parameter name for compatibility; its
    keys are Polymarket US market slugs, not international CLOB token IDs.
    """
    slugs = list(token_to_market)
    if not slugs:
        raise ValueError("No Polymarket US market slugs supplied")
    pm_key = key_id or settings.polymarket_us_key_id
    pm_secret = secret_key or settings.polymarket_us_secret_key
    if not pm_key or not pm_secret:
        raise ValueError(
            "Set POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY for Polymarket US WebSockets"
        )

    endpoint = url or settings.polymarket_us_ws_url
    headers = _polymarket_us_headers(pm_key, pm_secret)
    deadline = time.monotonic() + seconds if seconds else None

    async with websockets.connect(
        endpoint,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        for index in range(0, len(slugs), 100):
            batch = slugs[index : index + 100]
            request_id = f"pme-book-{index // 100 + 1}"
            await ws.send(
                json.dumps(
                    {
                        "subscribe": {
                            "requestId": request_id,
                            "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA",
                            "marketSlugs": batch,
                        }
                    }
                )
            )

        while deadline is None or time.monotonic() < deadline:
            timeout = None if deadline is None else max(0.1, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except TimeoutError:
                return
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                continue
            if msg.get("error"):
                raise RuntimeError(f"Polymarket US WebSocket subscription failed: {msg['error']}")
            if "heartbeat" in msg:
                continue

            data = msg.get("marketData") or msg.get("market_data")
            if isinstance(data, dict):
                slug = str(data.get("marketSlug") or data.get("market_slug") or "")
                market_id = token_to_market.get(slug)
                if not market_id:
                    continue
                bid_levels = [parsed for row in data.get("bids") or [] if (parsed := _level(row))]
                ask_levels = [parsed for row in data.get("offers") or [] if (parsed := _level(row))]
                best_bid = max(bid_levels, default=None, key=lambda x: x[0])
                best_ask = min(ask_levels, default=None, key=lambda x: x[0])
                stats = data.get("stats") or {}
                await handler(
                    Quote(
                        timestamp=_iso_to_dt(data.get("transactTime") or data.get("transact_time")),
                        venue=Venue.POLYMARKET,
                        market_id=market_id,
                        token_id=slug,
                        bid=None if best_bid is None else best_bid[0],
                        ask=None if best_ask is None else best_ask[0],
                        bid_size=None if best_bid is None else best_bid[1],
                        ask_size=None if best_ask is None else best_ask[1],
                        last=_amount(stats.get("lastTradePx") or stats.get("last_trade_px")),
                        raw=msg,
                    )
                )
                continue

            lite = msg.get("marketDataLite") or msg.get("market_data_lite")
            if isinstance(lite, dict):
                slug = str(lite.get("marketSlug") or lite.get("market_slug") or "")
                market_id = token_to_market.get(slug)
                if not market_id:
                    continue
                await handler(
                    Quote(
                        timestamp=datetime.now(UTC),
                        venue=Venue.POLYMARKET,
                        market_id=market_id,
                        token_id=slug,
                        bid=_amount(lite.get("bestBid") or lite.get("best_bid")),
                        ask=_amount(lite.get("bestAsk") or lite.get("best_ask")),
                        last=_amount(lite.get("lastTradePx") or lite.get("last_trade_px")),
                        raw=msg,
                    )
                )


def _kalshi_headers(api_key_id: str, private_key_path: Path) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    path = "/trade-api/ws/v2"
    message = f"{timestamp}GET{path}".encode()
    key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    signature = key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }


async def stream_kalshi(
    market_tickers: list[str],
    handler: QuoteHandler,
    seconds: int | None = None,
    url: str | None = None,
    api_key_id: str | None = None,
    private_key_path: Path | None = None,
) -> None:
    """Stream Kalshi ticker BBO updates. Connection authentication is required."""
    key_id = api_key_id or settings.kalshi_api_key_id
    key_path = private_key_path or settings.kalshi_private_key_path
    if not key_id or not key_path:
        raise ValueError("Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH for Kalshi WebSockets.")
    headers = _kalshi_headers(key_id, key_path)
    endpoint = url or settings.kalshi_ws_url
    deadline = time.monotonic() + seconds if seconds else None
    async with websockets.connect(endpoint, additional_headers=headers) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {"channels": ["ticker"], "market_tickers": market_tickers},
                }
            )
        )
        while deadline is None or time.monotonic() < deadline:
            timeout = None if deadline is None else max(0.1, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except TimeoutError:
                return
            msg = json.loads(raw)
            if msg.get("type") != "ticker":
                continue
            data = msg.get("msg", {})
            ticker = str(data.get("market_ticker"))
            await handler(
                Quote(
                    timestamp=_ms_to_dt(data.get("ts_ms") or data.get("ts")),
                    venue=Venue.KALSHI,
                    market_id=ticker,
                    bid=float(data["yes_bid_dollars"])
                    if data.get("yes_bid_dollars") not in (None, "")
                    else None,
                    ask=float(data["yes_ask_dollars"])
                    if data.get("yes_ask_dollars") not in (None, "")
                    else None,
                    bid_size=float(data["yes_bid_size_fp"])
                    if data.get("yes_bid_size_fp") not in (None, "")
                    else None,
                    ask_size=float(data["yes_ask_size_fp"])
                    if data.get("yes_ask_size_fp") not in (None, "")
                    else None,
                    last=float(data["price_dollars"])
                    if data.get("price_dollars") not in (None, "")
                    else None,
                    volume=float(data["volume_fp"])
                    if data.get("volume_fp") not in (None, "")
                    else None,
                    raw=msg,
                )
            )
