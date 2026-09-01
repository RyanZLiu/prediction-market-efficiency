from pme.research.calibration import brier_score, log_loss, reliability_bins


def test_calibration_metrics():
    p = [.8, .2]
    y = [1, 0]
    assert abs(brier_score(p, y) - .04) < 1e-9
    assert log_loss(p, y) > 0
    assert sum(int(x["count"]) for x in reliability_bins(p, y, bins=5)) == 2
