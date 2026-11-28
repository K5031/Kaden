import unittest

from cli import main
from store import items


class StoreTest(unittest.TestCase):
    def test_items(self):
        self.assertEqual(items()[:2], ["apples", "bread"])

    def test_list(self):
        self.assertEqual(len(main(["list"])), 5)


if __name__ == "__main__":
    unittest.main()
