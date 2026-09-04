from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from rapidfuzz import process
from rapidfuzz.fuzz import token_set_ratio

from pme.matching.normalize import normalize_text
from pme.models import Market, Venue


@dataclass(frozen=True)
class MatchSuggestion:
    kalshi_market_id: str
    polymarket_market_id: str
    polymarket_token_id: str | None
    kalshi_question: str
    polymarket_question: str
    score: float
    text_score: float
    date_score: float
    category_score: float


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "no",
    "of",
    "on",
    "or",
    "the",
    "to",
    "will",
    "win",
    "wins",
    "yes",
}


def _tokens(text: str) -> set[str]:
    normalized = normalize_text(text)
    words = re.findall(r"[a-z0-9]+", normalized)

    return {word for word in words if word not in STOPWORDS and len(word) >= 3}


def _event_title(market: Market) -> str:
    events = market.metadata.get("events") or []

    if events and isinstance(events, list):
        first = events[0]

        if isinstance(first, dict):
            return str(first.get("title") or "")

    return ""


def _description(market: Market) -> str:
    return str(market.metadata.get("description") or "")


def _rules_primary(market: Market) -> str:
    return str(market.metadata.get("rules_primary") or "")


def _contract_scope(market: Market) -> str:
    question = market.question.lower()
    ticker = market.market_id.upper()

    event_title = _event_title(market).lower()
    description = _description(market).lower()
    rules = _rules_primary(market).lower()

    if market.venue == Venue.KALSHI:
        if ticker.startswith("KXATPSETWINNER"):
            return "tennis_set_winner"

        if ticker.startswith("KXWTASETWINNER"):
            return "tennis_set_winner"

        if ticker.startswith("KXATPEXACTMATCH"):
            return "tennis_exact_match"

        if ticker.startswith("KXWTAEXACTMATCH"):
            return "tennis_exact_match"

        if ticker.startswith("KXATPMATCH"):
            return "tennis_match_winner"

        if ticker.startswith("KXWTAMATCH"):
            return "tennis_match_winner"

        if "professional tennis match" in rules and "win set" not in question:
            return "tennis_match_winner"

        if "tournament" in rules and "wins" in rules:
            return "tennis_tournament_winner"

    if market.venue == Venue.POLYMARKET:
        if "winner (tennis)" in event_title or (
            "tournament" in description and "wins" in description
        ):
            return "tennis_tournament_winner"

        sports_market_type = str(market.metadata.get("sportsMarketType") or "").lower()

        if sports_market_type in {
            "moneyline",
            "match_winner",
            "h2h",
        }:
            return "tennis_match_winner"

        if " vs " in event_title and "set" not in question:
            return "tennis_match_winner"

        if "set " in question:
            return "tennis_set_winner"

    return "unknown"


def _is_compound_kalshi_market(market: Market) -> bool:
    ticker = market.market_id.upper()

    if ticker.startswith("KXMVE"):
        return True

    question = market.question.lower()

    if question.count(",yes ") >= 1:
        return True

    if question.count(",no ") >= 1:
        return True

    return False


def _passes_anchor_filter(a: Market, b: Market) -> bool:
    a_tokens = _tokens(a.question)
    b_tokens = _tokens(b.question)

    shared = a_tokens & b_tokens

    if len(shared) >= 2:
        return True

    if len(shared) == 1:
        word = next(iter(shared))

        if len(word) >= 7:
            return True

    return False


def _date_score(a: Market, b: Market) -> float:
    if not a.start_time or not b.start_time:
        return 0.5

    delta = abs(a.start_time - b.start_time)

    if delta <= timedelta(hours=6):
        return 1.0

    if delta <= timedelta(days=1):
        return 0.8

    if delta <= timedelta(days=3):
        return 0.4

    return 0.0


def _category_score(a: Market, b: Market) -> float:
    if not a.category or not b.category:
        return 0.5

    return 1.0 if normalize_text(a.category) == normalize_text(b.category) else 0.0


def score_pair(
    a: Market,
    b: Market,
) -> tuple[float, float, float, float]:
    text = (
        token_set_ratio(
            normalize_text(a.question),
            normalize_text(b.question),
        )
        / 100.0
    )

    date = _date_score(a, b)
    category = _category_score(a, b)

    score = 100.0 * (0.90 * text + 0.07 * date + 0.03 * category)

    return (
        score,
        100.0 * text,
        100.0 * date,
        100.0 * category,
    )


def suggest_matches(
    markets: list[Market],
    threshold: float = 70.0,
    limit: int = 100,
) -> list[MatchSuggestion]:
    kalshi = [
        market
        for market in markets
        if market.venue == Venue.KALSHI and not _is_compound_kalshi_market(market)
    ]

    polymarket = [market for market in markets if market.venue == Venue.POLYMARKET]

    poly_normalized = [normalize_text(market.question) for market in polymarket]

    suggestions: list[MatchSuggestion] = []

    print(f"Matching {len(kalshi)} Kalshi markets against {len(polymarket)} Polymarket markets...")

    for index, kalshi_market in enumerate(kalshi, start=1):
        if index % 100 == 0:
            print(f"Matching Kalshi market {index}/{len(kalshi)}...")

        kalshi_scope = _contract_scope(kalshi_market)

        if kalshi_scope == "unknown":
            continue

        text_matches = process.extract(
            normalize_text(kalshi_market.question),
            poly_normalized,
            scorer=token_set_ratio,
            limit=30,
            score_cutoff=40,
        )

        candidates: list[MatchSuggestion] = []

        for _, _, poly_index in text_matches:
            poly_market = polymarket[poly_index]

            poly_scope = _contract_scope(poly_market)

            # Critical rule:
            # match winner != tournament winner != set winner
            if kalshi_scope != poly_scope:
                continue

            if not _passes_anchor_filter(
                kalshi_market,
                poly_market,
            ):
                continue

            score, text, date, category = score_pair(
                kalshi_market,
                poly_market,
            )

            if score < threshold:
                continue

            candidates.append(
                MatchSuggestion(
                    kalshi_market_id=kalshi_market.market_id,
                    polymarket_market_id=poly_market.market_id,
                    polymarket_token_id=poly_market.token_id,
                    kalshi_question=kalshi_market.question,
                    polymarket_question=poly_market.question,
                    score=score,
                    text_score=text,
                    date_score=date,
                    category_score=category,
                )
            )

        candidates.sort(
            key=lambda item: item.score,
            reverse=True,
        )

        suggestions.extend(candidates[:3])

    suggestions.sort(
        key=lambda item: item.score,
        reverse=True,
    )

    return suggestions[:limit]
