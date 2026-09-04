from __future__ import annotations

from collections import defaultdict

from rapidfuzz.fuzz import token_set_ratio

from pme.matching.matcher import (
    MatchSuggestion,
    _contract_scope,
    _is_compound_kalshi_market,
    _passes_anchor_filter,
    _tokens,
    score_pair,
)
from pme.matching.normalize import normalize_text
from pme.models import Market, MarketType, Venue


def _looks_completed(market: Market) -> bool:
    text = market.question.lower()
    bad_phrases = (
        "completed match",
        "completed game",
        "completed event",
    )
    return any(phrase in text for phrase in bad_phrases)


def auto_match_markets(
    markets: list[Market],
    min_score: float = 84.0,
    min_text_score: float = 80.0,
    min_date_score: float = 80.0,
    min_margin: float = 4.0,
    candidate_limit: int = 30,
) -> list[MatchSuggestion]:
    """
    Find reciprocal high-confidence Kalshi/Polymarket matches.

    Uses a token index so each Kalshi market is compared only against
    Polymarket markets sharing meaningful words, rather than comparing
    every Kalshi market with every Polymarket market.
    """
    del min_date_score

    kalshi = [
        market
        for market in markets
        if (
            market.venue == Venue.KALSHI
            and market.active
            and market.market_type == MarketType.BINARY
            and not _is_compound_kalshi_market(market)
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

    if not kalshi or not polymarket:
        return []

    poly_normalized = [
        normalize_text(market.question)
        for market in polymarket
    ]

    poly_tokens = [
        _tokens(market.question)
        for market in polymarket
    ]

    # token -> Polymarket market indexes
    token_index: dict[str, list[int]] = defaultdict(list)

    for index, tokens in enumerate(poly_tokens):
        for token in tokens:
            token_index[token].append(index)

    candidate_limit = max(1, candidate_limit)
    fuzzy_cutoff = max(35.0, min_text_score - 20.0)

    candidates_by_kalshi: dict[
        str, list[MatchSuggestion]
    ] = defaultdict(list)

    candidates_by_poly: dict[
        str, list[tuple[float, str]]
    ] = defaultdict(list)

    for k_market in kalshi:
        k_tokens = _tokens(k_market.question)

        if not k_tokens:
            continue

        # Only examine Polymarket contracts sharing at least one
        # meaningful token with this Kalshi contract.
        candidate_indexes: set[int] = set()

        for token in k_tokens:
            candidate_indexes.update(
                token_index.get(token, ())
            )

        if not candidate_indexes:
            continue

        k_normalized = normalize_text(
            k_market.question
        )

        fuzzy_candidates: list[
            tuple[float, int]
        ] = []

        for poly_index in candidate_indexes:
            fuzzy_score = token_set_ratio(
                k_normalized,
                poly_normalized[poly_index],
            )

            if fuzzy_score >= fuzzy_cutoff:
                fuzzy_candidates.append(
                    (fuzzy_score, poly_index)
                )

        fuzzy_candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        k_scope = _contract_scope(k_market)

        for _, poly_index in fuzzy_candidates[
            :candidate_limit
        ]:
            p_market = polymarket[poly_index]
            p_scope = _contract_scope(p_market)

            if (
                k_scope != "unknown"
                and p_scope != "unknown"
                and k_scope != p_scope
            ):
                continue

            if not _passes_anchor_filter(
                k_market,
                p_market,
            ):
                continue

            _, text, date, category = score_pair(
                k_market,
                p_market,
            )

            # Text equivalence matters most.
            # Date/category metadata differ between venues,
            # so they only provide small bonuses.
            evidence_bonus = 0.0

            if category >= 100.0:
                evidence_bonus += 1.0

            if date >= 80.0:
                evidence_bonus += 1.0

            score = min(
                100.0,
                text + evidence_bonus,
            )

            suggestion = MatchSuggestion(
                kalshi_market_id=(
                    k_market.market_id
                ),
                polymarket_market_id=(
                    p_market.market_id
                ),
                polymarket_token_id=(
                    p_market.token_id
                ),
                kalshi_question=(
                    k_market.question
                ),
                polymarket_question=(
                    p_market.question
                ),
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

    for kalshi_id, candidates in (
        candidates_by_kalshi.items()
    ):
        candidates.sort(
            key=lambda item: item.score,
            reverse=True,
        )

        best = candidates[0]

        if (
            best.score < min_score
            or best.text_score < min_text_score
        ):
            continue

        second_score = (
            candidates[1].score
            if len(candidates) > 1
            else 0.0
        )

        if (
            best.score - second_score
            < min_margin
        ):
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

        (
            reverse_best_score,
            reverse_best_kalshi,
        ) = reverse[0]

        if reverse_best_kalshi != kalshi_id:
            continue

        reverse_second_score = (
            reverse[1][0]
            if len(reverse) > 1
            else 0.0
        )

        if (
            reverse_best_score
            - reverse_second_score
            < min_margin
        ):
            continue

        accepted.append(best)

    accepted.sort(
        key=lambda item: item.score,
        reverse=True,
    )

    return accepted
