from pme.research.odds import american_to_implied, devig_probabilities, implied_to_american


def test_american_odds_conversion():
    assert abs(american_to_implied(-150) - 0.6) < 1e-12
    assert abs(american_to_implied(200) - 1 / 3) < 1e-12
    assert abs(implied_to_american(0.6) + 150) < 1e-9


def test_devig():
    fair = devig_probabilities([0.55, 0.50])
    assert abs(sum(fair) - 1) < 1e-12
    assert fair[0] > fair[1]
