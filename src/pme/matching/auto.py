from __future__ import annotations

from collections import defaultdict

from pme.matching.matcher import MatchSuggestion, score_pair
from pme.models import Market, MarketType, Venue


def _looks_completed(market: Market) -> bool:
    text = market.question.lower()

    bad_phrases = (
        "completed match",
        "completed game",
        "completed event",
    )

    return any(
        phrase in text
        for phrase in bad_phrases
    )

def auto_match_markets(
    markets: list[Market],
    min_score: float = 84.0,
    min_text_score: float = 80.0,
    min_date_score: float = 80.0,
    min_margin: float = 4.0,
) -> list[MatchSuggestion]:
    

    kalshi = [
        market
        for market in markets
        if (
            market.venue == Venue.KALSHI
            and market.active
            and market.market_type == MarketType.BINARY
        )
    ]

    polymarket = [
        market
        for market in markets
        if (
            market.venue == Venue.POLYMARKET
            and market.active
            and market.market_type == MarketType.BINARY
            and market.token_id
            and not _looks_completed(market)
        )
    ]

    candidates_by_kalshi: dict[
        str, list[MatchSuggestion]
    ] = defaultdict(list)

    candidates_by_poly: dict[
        str, list[tuple[float, str]]
    ] = defaultdict(list)

    for k_market in kalshi:
        for p_market in polymarket:
            score, text, date, category = score_pair(
                k_market,
                p_market,
            )

            suggestion = MatchSuggestion(
                kalshi_market_id=k_market.market_id,
                polymarket_market_id=p_market.market_id,
                polymarket_token_id=p_market.token_id,
                kalshi_question=k_market.question,
                polymarket_question=p_market.question,
                score=score,
                text_score=text,
                date_score=date,
                category_score=category,
            )

            candidates_by_kalshi[
                k_market.market_id
            ].append(suggestion)

            candidates_by_poly[
                p_market.market_id
            ].append(
                (
                    score,
                    k_market.market_id,
                )
            )

    accepted: list[MatchSuggestion] = []

    for kalshi_id, candidates in candidates_by_kalshi.items():
        candidates.sort(
            key=lambda item: item.score,
            reverse=True,
        )

        if not candidates:
            continue

        best = candidates[0]

        if best.score < min_score:
            continue

        if best.text_score < min_text_score:
            continue

        if best.date_score < min_date_score:
            continue

        second_score = (
            candidates[1].score
            if len(candidates) > 1
            else 0.0
        )

        if best.score - second_score < min_margin:
            continue

        reverse = sorted(
            candidates_by_poly[
                best.polymarket_market_id
            ],
            key=lambda item: item[0],
            reverse=True,
        )

        if not reverse:
            continue

        reverse_best_score, reverse_best_kalshi = reverse[0]

        if reverse_best_kalshi != kalshi_id:
            continue

        reverse_second_score = (
            reverse[1][0]
            if len(reverse) > 1
            else 0.0
        )

        if (
            reverse_best_score - reverse_second_score
            < min_margin
        ):
            continue

        accepted.append(best)

    accepted.sort(
        key=lambda item: item.score,
        reverse=True,
    )

    return accepted