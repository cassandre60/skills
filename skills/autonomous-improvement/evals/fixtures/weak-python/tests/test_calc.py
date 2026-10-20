from src.calc import discount


def test_non_member_pays_full_price():
    assert discount(50, None) == 50
