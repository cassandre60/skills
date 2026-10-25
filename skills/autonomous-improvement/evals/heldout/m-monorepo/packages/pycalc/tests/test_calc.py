import unittest

from src.calc import add


class AddTests(unittest.TestCase):
    def test_adds_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_adds_negative(self):
        self.assertEqual(add(-1, 1), 0)

    def test_adds_zero(self):
        self.assertEqual(add(0, 0), 0)


if __name__ == "__main__":
    unittest.main()
