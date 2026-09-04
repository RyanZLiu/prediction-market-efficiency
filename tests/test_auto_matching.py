from pme.matching.auto import auto_match_markets
from pme.models import Market, Venue


def _kalshi(market_id: str, question: str) -> Market:
    return Market(
        venue=Venue.KALSHI,
        market_id=market_id,
        question=question,
        category="politics",
    )


def _poly(market_id: str, question: str) -> Market:
    return Market(
        venue=Venue.POLYMARKET,
        market_id=market_id,
        token_id=f"token-{market_id}",
        question=question,
        category="politics",
    )


def test_auto_match_accepts_high_confidence_pair_without_dates() -> None:
    markets = [
        _kalshi("KX-GOV-CA", "Will Alex Smith win the California governor election?"),
        _poly("101", "Will Alex Smith win the California governor election?"),
    ]

    matches = auto_match_markets(markets, min_score=90.0, min_text_score=90.0)

    assert len(matches) == 1
    assert matches[0].kalshi_market_id == "KX-GOV-CA"
    assert matches[0].polymarket_market_id == "101"


def test_auto_match_rejects_ambiguous_pair_without_margin() -> None:
    markets = [
        _kalshi("KX-A", "Will Alex Smith win the California governor election?"),
        _poly("101", "Will Alex Smith win the California governor election?"),
        _poly("102", "Will Alex Smith win California governor election?"),
    ]

    matches = auto_match_markets(markets, min_margin=5.0)

    assert matches == []


def test_auto_match_ignores_completed_polymarket_market() -> None:
    markets = [
        _kalshi("KX-TENNIS", "Julia Camargo wins tennis match"),
        _poly("201", "Completed Match: Julia Camargo wins tennis match"),
    ]

    matches = auto_match_markets(markets, min_score=70.0, min_text_score=70.0)

    assert matches == []
