from datetime import UTC, datetime, timedelta

from pme.executable import kalshi_taker_fee, polymarket_taker_fee, quote_gate
from pme.models import Quote, Venue


def test_kalshi_quadratic_fee() -> None:
    assert kalshi_taker_fee(
        price=0.50,
        quantity=100,
        fee_type="quadratic",
        fee_multiplier=1.0,
    ) == 1.75


def test_kalshi_reduced_multiplier_fee() -> None:
    assert kalshi_taker_fee(
        price=0.50,
        quantity=100,
        fee_type="quadratic",
        fee_multiplier=0.5,
    ) == 0.875


def test_kalshi_flat_fee_is_not_guessed() -> None:
    assert (
        kalshi_taker_fee(
            price=0.50,
            quantity=100,
            fee_type="flat",
            fee_multiplier=1.0,
        )
        is None
    )


def test_polymarket_us_taker_fee() -> None:
    assert polymarket_taker_fee(
        price=0.50,
        quantity=100,
        fee_rate=0.06,
        fee_exponent=2,
    ) == 1.50


def test_polymarket_us_bankers_rounding() -> None:
    assert polymarket_taker_fee(
        price=0.10,
        quantity=1,
        fee_rate=0.06,
        fee_exponent=2,
    ) == 0.01


def test_quote_gate_rejects_stale_or_skewed_quotes() -> None:
    now = datetime.now(UTC)
    k = Quote(
        timestamp=now - timedelta(seconds=0.5),
        venue=Venue.KALSHI,
        market_id="K",
        bid=0.4,
        ask=0.41,
    )
    p = Quote(
        timestamp=now - timedelta(seconds=0.8),
        venue=Venue.POLYMARKET,
        market_id="P",
        bid=0.45,
        ask=0.46,
    )
    assert quote_gate(k, p, max_age_seconds=2, max_skew_seconds=1, now=now).passed
    stale = p.model_copy(update={"timestamp": now - timedelta(seconds=3)})
    assert not quote_gate(k, stale, max_age_seconds=2, max_skew_seconds=1, now=now).passed
    skewed = p.model_copy(update={"timestamp": now - timedelta(seconds=1.8)})
    assert not quote_gate(k, skewed, max_age_seconds=2, max_skew_seconds=1, now=now).passed
