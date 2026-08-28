from pme.matching.normalize import canonical_slug, normalize_text


def test_normalize_text():
    assert normalize_text("  Celtics & Lakers! ") == "celtics and lakers"


def test_slug():
    assert canonical_slug("Will Boston win?") == "will_boston_win"
