from pme.collectors.polymarket import PolymarketClient
from pme.models import Venue


def test_polymarket_us_normalization_uses_slug_as_subscription_key() -> None:
    client = PolymarketClient()
    try:
        market = client._normalize_market(
            {
                "id": "123",
                "slug": "example-market",
                "question": "Will the example happen?",
                "active": True,
                "closed": False,
                "archived": False,
                "feeCoefficient": 0.06,
            }
        )
        assert market is not None
        assert market.venue == Venue.POLYMARKET
        assert market.market_id == "123"
        assert market.token_id == "example-market"
        assert market.active
        assert market.metadata["polymarket_us"] is True
    finally:
        import asyncio

        asyncio.run(client.close())
