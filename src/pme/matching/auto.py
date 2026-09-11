from __future__ import annotations

from collections import defaultdict

from rapidfuzz.fuzz import token_set_ratio

from pme.matching.matcher import (
    MatchSuggestion,
    _contract_scope,
    _contracts_compatible,
    _is_compound_kalshi_market,
    _passes_anchor_filter,
    _tokens,
    score_pair,
)
from pme.matching.normalize import normalize_text
from pme.models import Market, MarketType, Venue


def _semantic_contract_tags(market: Market) -> set[str]:
    metadata = market.metadata

    parts = [
        market.question,
        market.market_id,
        str(market.token_id or ""),
        str(metadata.get("slug") or ""),
        str(metadata.get("description") or ""),
        str(metadata.get("rules_primary") or ""),
        str(metadata.get("rules_secondary") or ""),
        str(metadata.get("sportsMarketType") or ""),
        str(metadata.get("sportsMarketTypeV2") or ""),
    ]

    combined = " ".join(parts).lower()
    tags: set[str] = set()

    # Award contracts
    if "award" in combined:
        tags.add("award")

    if "finalist" in combined:
        tags.add("award_finalist")

    if (
        "award winner" in combined
        or ("wins the " in combined and " award" in combined)
        or ("win the " in combined and " award" in combined)
    ):
        tags.add("award_winner")

    # Season/stat-leader contracts
    stat_patterns = {
        "most_receiving_yards": (
            "most receiving yards",
            "mostrecyds",
        ),
        "most_rushing_yards": (
            "most rushing yards",
            "mostrushyds",
        ),
        "most_passing_yards": (
            "most passing yards",
            "mostpassyds",
        ),
        "most_receptions": (
            "most receptions",
            "mostreceptions",
        ),
        "most_passing_touchdowns": (
            "most passing touchdowns",
            "mostpassingtd",
        ),
    }

    for tag, patterns in stat_patterns.items():
        if any(pattern in combined for pattern in patterns):
            tags.add("stat_leader")
            tags.add(tag)

    # Player stat props
    if (
        "passing touchdown" in combined
        or "football_player_passing_touchdowns" in combined
    ):
        tags.add("passing_touchdowns")

    if (
        "passing yards" in combined
        or "football_player_passing_yards" in combined
    ):
        tags.add("passing_yards")

    if (
        "receiving yards" in combined
        or "football_player_receiving_yards" in combined
    ):
        tags.add("receiving_yards")

    if (
        "rushing yards" in combined
        or "football_player_rushing_yards" in combined
    ):
        tags.add("rushing_yards")

    if (
        "receptions" in combined
        or "football_player_receptions" in combined
    ):
        tags.add("receptions")

    # Kalshi KXNFLTD refers to the player personally scoring a touchdown,
    # which is different from a QB recording a passing touchdown.
    if market.market_id.upper().startswith("KXNFLTD"):
        tags.add("touchdowns_scored")

    return tags


def _semantic_contracts_compatible(a: Market, b: Market) -> bool:
    a_tags = _semantic_contract_tags(a)
    b_tags = _semantic_contract_tags(b)

    # Award proposition vs statistical-leader proposition
    if (
        "award" in a_tags
        and "stat_leader" in b_tags
    ) or (
        "award" in b_tags
        and "stat_leader" in a_tags
    ):
        return False

    # Finalist and winner are not equivalent.
    if (
        "award_finalist" in a_tags
        and "award_winner" in b_tags
    ) or (
        "award_finalist" in b_tags
        and "award_winner" in a_tags
    ):
        return False

    specific_stats = {
        "most_receiving_yards",
        "most_rushing_yards",
        "most_passing_yards",
        "most_receptions",
        "most_passing_touchdowns",
        "passing_touchdowns",
        "passing_yards",
        "receiving_yards",
        "rushing_yards",
        "receptions",
        "touchdowns_scored",
    }

    a_specific = a_tags & specific_stats
    b_specific = b_tags & specific_stats

    if (
        a_specific
        and b_specific
        and a_specific.isdisjoint(b_specific)
    ):
        return False

    return True


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

            if not _contracts_compatible(k_market, p_market):
                continue

            if not _semantic_contracts_compatible(k_market, p_market):
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
