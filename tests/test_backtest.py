from datetime import UTC, datetime

from pme.backtest import BacktestConfig, PaperBacktester, summarize_trades
from pme.models import Opportunity, Venue


def test_backtest_and_metrics():
    opp = Opportunity(
        timestamp=datetime.now(UTC),
        canonical_event_id="e",
        canonical_outcome="YES",
        buy_venue=Venue.KALSHI,
        buy_market_id="k",
        buy_price=0.45,
        buy_size=10,
        hedge_venue=Venue.POLYMARKET,
        hedge_market_id="p",
        hedge_yes_bid=0.50,
        hedge_no_price=0.50,
        hedge_size=8,
        gross_edge=0.05,
        estimated_cost=0.01,
        net_edge=0.04,
        available_size=8,
    )
    trades = PaperBacktester(BacktestConfig(max_contracts=5, min_net_edge=0.01)).run([opp])
    assert len(trades) == 1
    assert trades[0].quantity == 5
    assert abs(trades[0].pnl - 0.20) < 1e-9
    stats = summarize_trades(trades)
    assert abs(stats["total_pnl"] - 0.20) < 1e-9
