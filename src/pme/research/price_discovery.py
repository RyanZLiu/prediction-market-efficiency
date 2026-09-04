from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass(frozen=True)
class LagCorrelation:
    lag: int
    correlation: float


@dataclass(frozen=True)
class ConvergenceEpisode:
    start: datetime
    end: datetime
    duration_seconds: float
    max_gap: float


def lead_lag_correlation(a: list[float], b: list[float], max_lag: int = 10) -> list[LagCorrelation]:
    if len(a) != len(b):
        raise ValueError("Series must have the same length after alignment.")
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    results: list[LagCorrelation] = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            left, right = x[-lag:], y[:lag]
        elif lag > 0:
            left, right = x[:-lag], y[lag:]
        else:
            left, right = x, y
        if len(left) < 3 or np.std(left) == 0 or np.std(right) == 0:
            corr = float("nan")
        else:
            corr = float(np.corrcoef(left, right)[0, 1])
        results.append(LagCorrelation(lag=lag, correlation=corr))
    return results


def convergence_episodes(
    timestamps: list[datetime],
    gaps: list[float],
    entry_threshold: float = 0.03,
    exit_threshold: float = 0.01,
) -> list[ConvergenceEpisode]:
    if len(timestamps) != len(gaps):
        raise ValueError("timestamps and gaps must have equal length")
    episodes: list[ConvergenceEpisode] = []
    start: datetime | None = None
    max_gap = 0.0
    for ts, gap in zip(timestamps, gaps, strict=True):
        absolute = abs(gap)
        if start is None and absolute >= entry_threshold:
            start = ts
            max_gap = absolute
        elif start is not None:
            max_gap = max(max_gap, absolute)
            if absolute <= exit_threshold:
                episodes.append(
                    ConvergenceEpisode(
                        start=start,
                        end=ts,
                        duration_seconds=(ts - start).total_seconds(),
                        max_gap=max_gap,
                    )
                )
                start = None
                max_gap = 0.0
    return episodes
