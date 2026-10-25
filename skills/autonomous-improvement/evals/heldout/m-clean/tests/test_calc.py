import unittest

from src.calc import add, clamp


class CalcTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_add_negative(self):
        self.assertEqual(add(-1, 1), 0)

    def test_clamp_below(self):
        self.assertEqual(clamp(-3, 0, 10), 0)

    def test_clamp_above(self):
        self.assertEqual(clamp(15, 0, 10), 10)

    def test_clamp_inside(self):
        self.assertEqual(clamp(5, 0, 10), 5)


if __name__ == "__main__":
    unittest.main()
