from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from pme.config import settings
from pme.models import Quote, Venue

QuoteHandler = Callable[[Quote], Awaitable[None]]


def _ms_to_dt(value: str | int | float | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    number = float(value)
    if number > 10_000_000_000:
        number /= 1000.0
    return datetime.fromtimestamp(number, tz=timezone.utc)


async def stream_polymarket(
    token_to_market: dict[str, str],
    handler: QuoteHandler,
    seconds: int | None = None,
    url: str | None = None,
) -> None:
    """Stream public Polymarket BBO/book updates for YES token IDs."""
    endpoint = url or settings.polymarket_ws_url
    token_ids = list(token_to_market)
    if not token_ids:
        raise ValueError("No Polymarket token IDs supplied.")

    deadline = time.monotonic() + seconds if seconds else None
    latest: dict[str, dict[str, float | None]] = {}

    async with websockets.connect(endpoint, ping_interval=None) as ws:
        await ws.send(json.dumps({"assets_ids": token_ids, "type": "market", "custom_feature_enabled": True}))

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(10)
                await ws.send("PING")

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            while deadline is None or time.monotonic() < deadline:
                timeout = None if deadline is None else max(0.1, deadline - time.monotonic())
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                if raw == "PONG":
                    continue
                data = json.loads(raw)
                messages = data if isinstance(data, list) else [data]
                for msg in messages:
                    event_type = msg.get("event_type")
                    if event_type == "book":
                        token = str(msg.get("asset_id"))
                        bids = msg.get("bids") or []
                        asks = msg.get("asks") or []
                        best_bid = max((float(x["price"]) for x in bids), default=None)
                        best_ask = min((float(x["price"]) for x in asks), default=None)
                        bid_size = next((float(x["size"]) for x in bids if float(x["price"]) == best_bid), None)
                        ask_size = next((float(x["size"]) for x in asks if float(x["price"]) == best_ask), None)
                        latest[token] = {"bid": best_bid, "ask": best_ask, "bid_size": bid_size, "ask_size": ask_size}
                        await handler(Quote(
                            timestamp=_ms_to_dt(msg.get("timestamp")), venue=Venue.POLYMARKET,
                            market_id=token_to_market[token], token_id=token, bid=best_bid, ask=best_ask,
                            bid_size=bid_size, ask_size=ask_size, raw=msg,
                        ))
                    elif event_type == "best_bid_ask":
                        token = str(msg.get("asset_id"))
                        if token not in token_to_market:
                            continue
                        await handler(Quote(
                            timestamp=_ms_to_dt(msg.get("timestamp")), venue=Venue.POLYMARKET,
                            market_id=token_to_market[token], token_id=token,
                            bid=float(msg["best_bid"]) if msg.get("best_bid") is not None else None,
                            ask=float(msg["best_ask"]) if msg.get("best_ask") is not None else None,
                            raw=msg,
                        ))
                    elif event_type == "price_change":
                        for change in msg.get("price_changes", []):
                            token = str(change.get("asset_id"))
                            if token not in token_to_market:
                                continue
                            await handler(Quote(
                                timestamp=_ms_to_dt(msg.get("timestamp")), venue=Venue.POLYMARKET,
                                market_id=token_to_market[token], token_id=token,
                                bid=float(change["best_bid"]) if change.get("best_bid") is not None else None,
                                ask=float(change["best_ask"]) if change.get("best_ask") is not None else None,
                                raw=msg,
                            ))
        except TimeoutError:
            return
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)


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
    """Stream Kalshi ticker BBO updates. Connection authentication is required by Kalshi."""
    key_id = api_key_id or settings.kalshi_api_key_id
    key_path = private_key_path or settings.kalshi_private_key_path
    if not key_id or not key_path:
        raise ValueError("Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH for Kalshi WebSockets.")
    headers = _kalshi_headers(key_id, key_path)
    endpoint = url or settings.kalshi_ws_url
    deadline = time.monotonic() + seconds if seconds else None
    async with websockets.connect(endpoint, additional_headers=headers) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["ticker"], "market_tickers": market_tickers},
        }))
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
            await handler(Quote(
                timestamp=_ms_to_dt(data.get("ts_ms") or data.get("ts")),
                venue=Venue.KALSHI,
                market_id=ticker,
                bid=float(data["yes_bid_dollars"]) if data.get("yes_bid_dollars") not in (None, "") else None,
                ask=float(data["yes_ask_dollars"]) if data.get("yes_ask_dollars") not in (None, "") else None,
                bid_size=float(data["yes_bid_size_fp"]) if data.get("yes_bid_size_fp") not in (None, "") else None,
                ask_size=float(data["yes_ask_size_fp"]) if data.get("yes_ask_size_fp") not in (None, "") else None,
                last=float(data["price_dollars"]) if data.get("price_dollars") not in (None, "") else None,
                volume=float(data["volume_fp"]) if data.get("volume_fp") not in (None, "") else None,
                raw=msg,
            ))
