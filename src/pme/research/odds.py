from __future__ import annotations


def american_to_implied(odds: float) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    a = abs(odds)
    return a / (a + 100.0)


def implied_to_american(probability: float) -> float:
    if not 0 < probability < 1:
        raise ValueError("probability must be strictly between 0 and 1")
    if probability >= 0.5:
        return -100.0 * probability / (1.0 - probability)
    return 100.0 * (1.0 - probability) / probability


def devig_probabilities(implied: list[float]) -> list[float]:
    total = sum(implied)
    if total <= 0:
        raise ValueError("sum of implied probabilities must be positive")
    return [p / total for p in implied]
