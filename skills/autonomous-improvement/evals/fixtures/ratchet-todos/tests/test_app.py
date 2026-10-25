import unittest

import src.app


class AppTests(unittest.TestCase):
    def test_main(self):
        self.assertEqual(src.app.main(), 42)
