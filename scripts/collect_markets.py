"""Small direct-script example. Prefer the `pme discover` CLI for normal use."""
import asyncio

from pme.collectors import KalshiClient, PolymarketClient


async def main() -> None:
    async with KalshiClient() as k, PolymarketClient() as p:
        kalshi, poly = await asyncio.gather(k.list_markets(25), p.list_markets(25))
    print("Kalshi", len(kalshi))
    print("Polymarket", len(poly))


if __name__ == "__main__":
    asyncio.run(main())
