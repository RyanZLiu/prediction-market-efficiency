import pytest

from pme.models import OrderBook, OrderBookLevel, Quote, Venue


def test_quote_midpoint_and_spread():
    quote = Quote(
        venue=Venue.KALSHI,
        market_id="TEST",
        bid=0.40,
        ask=0.44,
    )

    assert quote.midpoint == pytest.approx(0.42)
    assert quote.spread == pytest.approx(0.04)


def test_orderbook_best_prices():
    book = OrderBook(
        venue=Venue.POLYMARKET,
        market_id="TEST",
        bids=[
            OrderBookLevel(price=0.40, size=10),
            OrderBookLevel(price=0.42, size=5),
        ],
        asks=[
            OrderBookLevel(price=0.47, size=3),
            OrderBookLevel(price=0.45, size=7),
        ],
    )

    assert book.best_bid.price == pytest.approx(0.42)
    assert book.best_ask.price == pytest.approx(0.45)
