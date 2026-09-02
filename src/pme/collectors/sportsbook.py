
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from pme.config import settings
from pme.models import SportsbookQuote, utcnow
from pme.research.odds import american_to_implied, devig_probabilities


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class OddsApiClient:
    """Optional sportsbook adapter for The Odds API v4."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.odds_api_key
        if not self.api_key:
            raise ValueError("Set ODDS_API_KEY to use sportsbook ingestion.")
        self.base_url = (base_url or settings.odds_api_base_url).rstrip("/")
        self.client = httpx.AsyncClient(timeout=settings.http_timeout, headers={"User-Agent": settings.user_agent})

    async def __aenter__(self) -> "OddsApiClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def sports(self) -> list[dict[str, Any]]:
        r = await self.client.get(f"{self.base_url}/sports", params={"apiKey": self.api_key})
        r.raise_for_status()
        return r.json()

    async def odds(
        self,
        sport_key: str,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
        bookmakers: str | None = None,
    ) -> list[SportsbookQuote]:
        params: dict[str, Any] = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        r = await self.client.get(f"{self.base_url}/sports/{sport_key}/odds", params=params)
        r.raise_for_status()
        now = utcnow()
        quotes: list[SportsbookQuote] = []
        for event in r.json():
            for book in event.get("bookmakers", []):
                for market in book.get("markets", []):
                    outcomes = market.get("outcomes", [])
                    raw_probs = [american_to_implied(float(o["price"])) for o in outcomes]
                    fair_probs = devig_probabilities(raw_probs) if raw_probs else []
                    for outcome, implied, fair in zip(outcomes, raw_probs, fair_probs):
                        quotes.append(SportsbookQuote(
                            timestamp=now,
                            sport_key=str(event.get("sport_key", sport_key)),
                            event_id=str(event["id"]),
                            commence_time=_dt(event.get("commence_time")),
                            home_team=event.get("home_team"),
                            away_team=event.get("away_team"),
                            bookmaker=str(book.get("key") or book.get("title")),
                            market_key=str(market.get("key")),
                            outcome=str(outcome.get("name")),
                            american_odds=float(outcome["price"]),
                            point=float(outcome["point"]) if outcome.get("point") is not None else None,
                            implied_probability=implied,
                            fair_probability=fair,
                            metadata={"event": event, "bookmaker": book.get("title")},
                        ))
        return quotes
