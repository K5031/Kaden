import unittest

from prices import total


class TotalTest(unittest.TestCase):
    def test_no_discount(self):
        self.assertEqual(total([10, 20]), 30)

    def test_ten_percent_off(self):
        self.assertAlmostEqual(total([50, 50], discount=0.1), 90)


if __name__ == "__main__":
    unittest.main()
