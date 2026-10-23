from src.text import greet


def test_greet():
    assert greet("fleet") == "hello fleet"
