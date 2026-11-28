import unittest

from report import busiest_month


class BusiestMonthTest(unittest.TestCase):
    def test_december_wins(self):
        self.assertEqual(busiest_month(["01/12/2024", "15/12/2024", "03/11/2024"]), 12)


if __name__ == "__main__":
    unittest.main()
