from __future__ import annotations

import math


def brier_score(probabilities: list[float], outcomes: list[int]) -> float:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("probabilities and outcomes must have equal non-zero length")
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(
        probabilities
    )


def log_loss(probabilities: list[float], outcomes: list[int], eps: float = 1e-12) -> float:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("probabilities and outcomes must have equal non-zero length")
    total = 0.0
    for p, y in zip(probabilities, outcomes, strict=True):
        p = min(1 - eps, max(eps, p))
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(probabilities)


def reliability_bins(
    probabilities: list[float], outcomes: list[int], bins: int = 10
) -> list[dict[str, float]]:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    result: list[dict[str, float]] = []
    for i in range(bins):
        low, high = i / bins, (i + 1) / bins
        rows = [
            (p, y)
            for p, y in zip(probabilities, outcomes, strict=True)
            if low <= p < high or (i == bins - 1 and p == 1)
        ]
        if not rows:
            continue
        result.append(
            {
                "bin_low": low,
                "bin_high": high,
                "count": float(len(rows)),
                "mean_probability": sum(p for p, _ in rows) / len(rows),
                "event_rate": sum(y for _, y in rows) / len(rows),
            }
        )
    return result
