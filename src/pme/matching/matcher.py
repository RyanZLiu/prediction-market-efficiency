from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

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


def _date_score(a: Market, b: Market) -> float:
    if not a.close_time or not b.close_time:
        return 0.5
    delta = abs(a.close_time - b.close_time)
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


def score_pair(a: Market, b: Market) -> tuple[float, float, float, float]:
    text = token_set_ratio(normalize_text(a.question), normalize_text(b.question)) / 100.0
    date = _date_score(a, b)
    category = _category_score(a, b)
    score = 100.0 * (0.75 * text + 0.20 * date + 0.05 * category)
    return score, 100.0 * text, 100.0 * date, 100.0 * category


def suggest_matches(
    markets: list[Market], threshold: float = 70.0, limit: int = 100
) -> list[MatchSuggestion]:
    kalshi = [m for m in markets if m.venue == Venue.KALSHI]
    poly = [m for m in markets if m.venue == Venue.POLYMARKET]
    suggestions: list[MatchSuggestion] = []
    for k in kalshi:
        candidates: list[MatchSuggestion] = []
        for p in poly:
            score, text, date, category = score_pair(k, p)
            if score < threshold:
                continue
            candidates.append(
                MatchSuggestion(
                    kalshi_market_id=k.market_id,
                    polymarket_market_id=p.market_id,
                    polymarket_token_id=p.token_id,
                    kalshi_question=k.question,
                    polymarket_question=p.question,
                    score=score,
                    text_score=text,
                    date_score=date,
                    category_score=category,
                )
            )
        candidates.sort(key=lambda x: x.score, reverse=True)
        suggestions.extend(candidates[:3])
    suggestions.sort(key=lambda x: x.score, reverse=True)
    return suggestions[:limit]
