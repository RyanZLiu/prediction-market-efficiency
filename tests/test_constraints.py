from pme.models import ConstraintKind, ProbabilityConstraint
from pme.signals.constraints import evaluate_constraints


def test_subset_violation():
    prices = {"champ": 0.31, "conference": 0.29}
    constraints = [
        ProbabilityConstraint(kind=ConstraintKind.SUBSET, variables=["champ", "conference"])
    ]
    violations = evaluate_constraints(prices, constraints)
    assert len(violations) == 1
    assert abs(violations[0].magnitude - 0.02) < 1e-9


def test_monotonic_violation():
    prices = {"a": 0.7, "b": 0.6, "c": 0.62}
    constraints = [
        ProbabilityConstraint(kind=ConstraintKind.MONOTONIC_DESC, variables=["a", "b", "c"])
    ]
    violations = evaluate_constraints(prices, constraints)
    assert len(violations) == 1
