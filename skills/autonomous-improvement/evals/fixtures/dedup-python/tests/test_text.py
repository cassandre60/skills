from src.text import normalize


def test_normalize_trims_and_lowercases():
    assert normalize("  Mixed Case  ") == "mixed case"
