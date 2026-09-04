from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from typing import Any

from pme.models import Market, MarketMapping, Quote, Venue


@dataclass(frozen=True)
class ExecutableLeg:
    venue: Venue
    market_id: str
    side: str
    price: float
    available_size: float
    fee: float


@dataclass(frozen=True)
class ExecutableArbitrage:
    timestamp: datetime
    canonical_event_id: str
    canonical_outcome: str
    question: str | None
    direction: str
    leg1: ExecutableLeg
    leg2: ExecutableLeg
    quantity: float
    gross_profit: float
    total_fee: float
    net_profit: float
    net_edge: float
    total_cost: float
    payout: float
    net_roi: float
    kalshi_quote_age_seconds: float
    polymarket_quote_age_seconds: float
    quote_skew_seconds: float
    verification_latency_seconds: float
    match_confidence: float
    rules_verified: bool
    books_verified: bool
    fees_verified: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None
    kalshi_age_seconds: float
    polymarket_age_seconds: float
    skew_seconds: float


def quote_gate(
    kalshi_quote: Quote,
    polymarket_quote: Quote,
    *,
    max_age_seconds: float,
    max_skew_seconds: float,
    now: datetime | None = None,
) -> GateResult:
    now = now or datetime.now(UTC)
    k_ts = _aware(kalshi_quote.timestamp)
    p_ts = _aware(polymarket_quote.timestamp)
    k_age = max(0.0, (now - k_ts).total_seconds())
    p_age = max(0.0, (now - p_ts).total_seconds())
    skew = abs((k_ts - p_ts).total_seconds())

    if k_age > max_age_seconds:
        return GateResult(False, f"Kalshi quote is {k_age:.2f}s old", k_age, p_age, skew)
    if p_age > max_age_seconds:
        return GateResult(False, f"Polymarket quote is {p_age:.2f}s old", k_age, p_age, skew)
    if skew > max_skew_seconds:
        return GateResult(False, f"quote skew is {skew:.2f}s", k_age, p_age, skew)
    return GateResult(True, None, k_age, p_age, skew)


def kalshi_taker_fee(
    *,
    price: float,
    quantity: float,
    fee_type: str,
    fee_multiplier: float,
) -> float | None:
    """Estimate the official Kalshi taker trade fee before fill-rounding effects.

    Current quadratic schedules use 0.07 * C * P * (1-P), multiplied by the
    series fee multiplier. Kalshi's current fixed-point fee docs round the trade
    fee up to the nearest $0.0001. Flat-fee series are intentionally rejected
    because their fee depends on the specific fee table rather than the
    quadratic formula.
    """

    if quantity <= 0 or not 0.0 <= price <= 1.0:
        return None
    if fee_type not in {"quadratic", "quadratic_with_maker_fees"}:
        return None
    p = Decimal(str(price))
    q = Decimal(str(quantity))
    multiplier = Decimal(str(fee_multiplier))
    raw = Decimal("0.07") * multiplier * q * p * (Decimal("1") - p)
    return float(raw.quantize(Decimal("0.0001"), rounding=ROUND_CEILING))


def polymarket_taker_fee(
    *,
    price: float,
    quantity: float,
    fee_rate: float,
    fee_exponent: float | None,
) -> float | None:
    """Estimate the current Polymarket US taker fee.

    Polymarket US uses Fee = theta * C * p * (1-p). The exchange-wide
    taker theta is currently 0.06 unless a market-specific coefficient is
    supplied. Fees are rounded to the nearest cent using banker rounding.
    """
    if quantity <= 0 or not 0.0 <= price <= 1.0 or fee_rate < 0:
        return None
    if fee_exponent is not None and abs(float(fee_exponent) - 2.0) > 1e-9:
        return None
    p = Decimal(str(price))
    q = Decimal(str(quantity))
    theta = Decimal(str(fee_rate))
    raw = theta * q * p * (Decimal("1") - p)
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def market_pair(
    mappings: list[MarketMapping],
    markets: dict[tuple[Venue, str], Market],
) -> tuple[MarketMapping, MarketMapping, Market, Market] | None:
    k_mapping = next((m for m in mappings if m.venue == Venue.KALSHI), None)
    p_mapping = next((m for m in mappings if m.venue == Venue.POLYMARKET), None)
    if k_mapping is None or p_mapping is None:
        return None
    k_market = markets.get((Venue.KALSHI, k_mapping.market_id))
    p_market = markets.get((Venue.POLYMARKET, p_mapping.market_id))
    if k_market is None or p_market is None:
        return None
    return k_mapping, p_mapping, k_market, p_market


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
