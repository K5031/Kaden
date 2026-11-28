import unittest

import app.commands  # noqa: F401 - registers the commands
from app.registry import run


class CommandsTest(unittest.TestCase):
    def test_ping(self):
        self.assertEqual(run("ping"), {"ok": True})

    def test_echo(self):
        self.assertEqual(run("echo", text="hi"), {"ok": True, "text": "hi"})


if __name__ == "__main__":
    unittest.main()
