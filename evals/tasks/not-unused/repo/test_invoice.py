import unittest

from invoice import tax


class TaxTest(unittest.TestCase):
    def test_now(self):
        self.assertAlmostEqual(tax(100, "NL", 2024), 21)

    def test_before_2020(self):
        self.assertAlmostEqual(tax(100, "NL", 2019), 19)


if __name__ == "__main__":
    unittest.main()
