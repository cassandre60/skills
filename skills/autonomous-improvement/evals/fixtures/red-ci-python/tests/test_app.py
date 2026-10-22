from src.app import add, tide


def test_add():
    assert add(1, 2) == 3


def test_tide():
    assert tide(-3) == 3
