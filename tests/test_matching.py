from datetime import datetime, timezone

from pme.matching.matcher import score_pair
from pme.models import Market, Venue


def test_similar_markets_score_high():
    dt = datetime(2026, 10, 1, tzinfo=timezone.utc)
    a = Market(venue=Venue.KALSHI, market_id="k", question="Will Boston Celtics beat Los Angeles Lakers?", close_time=dt)
    b = Market(venue=Venue.POLYMARKET, market_id="p", question="Boston Celtics vs Los Angeles Lakers: Celtics to win", close_time=dt)
    score, *_ = score_pair(a, b)
    assert score > 65
