from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value in (None, "") else float(value)


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path(os.getenv("PME_DB_PATH", "data/pme.duckdb"))
    http_timeout: int = _int("PME_HTTP_TIMEOUT", 20)
    user_agent: str = os.getenv("PME_USER_AGENT", "prediction-market-efficiency/0.1")

    kalshi_base_url: str = "https://external-api.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = os.getenv(
        "KALSHI_WS_URL", "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    )
    kalshi_api_key_id: str | None = os.getenv("KALSHI_API_KEY_ID") or None
    kalshi_private_key_path: Path | None = (
        Path(os.environ["KALSHI_PRIVATE_KEY_PATH"])
        if os.getenv("KALSHI_PRIVATE_KEY_PATH")
        else None
    )

    # Polymarket US public market-data API. No key is required for REST reads.
    polymarket_us_gateway_url: str = os.getenv(
        "POLYMARKET_US_GATEWAY_URL", "https://gateway.polymarket.us"
    )
    # Authenticated API + market WebSocket. API credentials are required for WS.
    polymarket_us_api_url: str = os.getenv(
        "POLYMARKET_US_API_URL", "https://api.polymarket.us"
    )
    polymarket_us_ws_url: str = os.getenv(
        "POLYMARKET_US_WS_URL", "wss://api.polymarket.us/v1/ws/markets"
    )
    polymarket_us_key_id: str | None = os.getenv("POLYMARKET_KEY_ID") or None
    polymarket_us_secret_key: str | None = os.getenv("POLYMARKET_SECRET_KEY") or None
    polymarket_us_taker_fee_coefficient: float = _float(
        "POLYMARKET_US_TAKER_FEE_COEFFICIENT", 0.06
    )

    # Keep the international endpoints available for old notebooks/manual research,
    # but the active PolymarketClient now uses Polymarket US.
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    kalshi_fee_bps: float = _float("KALSHI_FEE_BPS", 0.0)
    polymarket_fee_bps: float = _float("POLYMARKET_FEE_BPS", 0.0)
    slippage_bps: float = _float("SLIPPAGE_BPS", 5.0)

    odds_api_key: str | None = os.getenv("ODDS_API_KEY") or None
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"


settings = Settings()
