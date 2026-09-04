from __future__ import annotations

from collections.abc import Mapping

from pme.models import ConstraintKind, ConstraintViolation, ProbabilityConstraint


def evaluate_constraints(
    prices: Mapping[str, float], constraints: list[ProbabilityConstraint]
) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    for c in constraints:
        vals = [float(prices[v]) for v in c.variables]
        magnitude = 0.0
        message = ""
        if c.kind == ConstraintKind.SUBSET:
            if len(vals) != 2:
                raise ValueError("subset constraints require [child, parent]")
            magnitude = max(0.0, vals[0] - vals[1])
            message = f"P({c.variables[0]}) must be <= P({c.variables[1]})"
        elif c.kind == ConstraintKind.COMPLEMENT:
            if len(vals) != 2:
                raise ValueError("complement constraints require two variables")
            magnitude = abs(sum(vals) - 1.0)
            message = f"P({c.variables[0]}) + P({c.variables[1]}) must equal 1"
        elif c.kind == ConstraintKind.MUTUALLY_EXCLUSIVE:
            magnitude = max(0.0, sum(vals) - 1.0)
            message = "Mutually exclusive probabilities must sum to <= 1"
        elif c.kind == ConstraintKind.EXHAUSTIVE:
            magnitude = abs(sum(vals) - 1.0)
            message = "Exhaustive outcome probabilities must sum to 1"
        elif c.kind == ConstraintKind.MONOTONIC_DESC:
            magnitude = max([0.0, *[vals[i + 1] - vals[i] for i in range(len(vals) - 1)]])
            message = "Sequence must be non-increasing"
        if magnitude > c.tolerance:
            violations.append(
                ConstraintViolation(constraint=c, magnitude=magnitude, message=message)
            )
    return violations


def repair_prices(
    prices: Mapping[str, float],
    constraints: list[ProbabilityConstraint],
    weights: Mapping[str, float] | None = None,
) -> dict[str, float]:
    try:
        import cvxpy as cp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "CVXPY is required for price repair. Install project dependencies."
        ) from exc

    names = list(prices)
    index = {name: i for i, name in enumerate(names)}
    x = cp.Variable(len(names))
    w = [float((weights or {}).get(name, 1.0)) for name in names]
    target = [float(prices[name]) for name in names]
    objective = cp.Minimize(cp.sum(cp.multiply(w, cp.square(x - target))))
    rules = [x >= 0, x <= 1]

    for c in constraints:
        ids = [index[v] for v in c.variables]
        if c.kind == ConstraintKind.SUBSET:
            rules.append(x[ids[0]] <= x[ids[1]])
        elif c.kind == ConstraintKind.COMPLEMENT:
            rules.append(cp.sum(x[ids]) == 1)
        elif c.kind == ConstraintKind.MUTUALLY_EXCLUSIVE:
            rules.append(cp.sum(x[ids]) <= 1)
        elif c.kind == ConstraintKind.EXHAUSTIVE:
            rules.append(cp.sum(x[ids]) == 1)
        elif c.kind == ConstraintKind.MONOTONIC_DESC:
            for left, right in zip(ids, ids[1:], strict=False):
                rules.append(x[left] >= x[right])

    problem = cp.Problem(objective, rules)
    problem.solve()
    if x.value is None:
        raise RuntimeError(f"Constraint-repair optimization failed with status {problem.status}")
    return {name: float(x.value[index[name]]) for name in names}
