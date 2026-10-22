from src.dead import greeting
from src.live import shout


def test_greeting():
    assert greeting("Ada") == "hello Ada"
    assert greeting("") == "hello stranger"


def test_shout():
    assert shout("hi") == "HI"
