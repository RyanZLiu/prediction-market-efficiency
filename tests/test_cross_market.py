from datetime import datetime, timezone

from pme.models import MarketMapping, Quote, Venue
from pme.signals.cross_market import CostModel, detect_cross_venue


def test_cross_market_edge_uses_ask_vs_bid():
    ts = datetime.now(timezone.utc)
    quotes = [
        Quote(timestamp=ts, venue=Venue.KALSHI, market_id="k", bid=.52, ask=.54, ask_size=100),
        Quote(timestamp=ts, venue=Venue.POLYMARKET, market_id="p", bid=.58, ask=.60, bid_size=80),
    ]
    mappings = [
        MarketMapping(canonical_event_id="e", canonical_outcome="YES", venue=Venue.KALSHI, market_id="k", verified=True),
        MarketMapping(canonical_event_id="e", canonical_outcome="YES", venue=Venue.POLYMARKET, market_id="p", verified=True),
    ]
    opps = detect_cross_venue(quotes, mappings, CostModel(slippage_bps=0), min_net_edge=0)
    assert len(opps) == 1
    assert abs(opps[0].gross_edge - .04) < 1e-9
    assert opps[0].available_size == 80
