"""The evaluation tasks (evals/): each check fails on the untouched repository and passes on
a right solution, and the runner drives a headless Kaden through a task, here with the
scripted model so it costs nothing."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomli_w

from evals import run as runner

FIXES = {
    "fix-a-bug": {"prices.py": 'def total(prices, discount=0.0):\n'
                               '    """The sum, less a fraction."""\n'
                               '    return sum(prices) * (1 - discount)\n'},
    "add-a-feature": {
        "textutil.py": 'def shout(text):\n    """Loud."""\n    return text.upper() + "!"\n\n\n'
                       'def word_count(text):\n    """Words in text."""\n'
                       '    return len(text.split())\n',
        "test_textutil.py": "import unittest\n\nfrom textutil import shout, word_count\n\n\n"
                            "class T(unittest.TestCase):\n"
                            "    def test_shout(self):\n"
                            "        self.assertEqual(shout('hi'), 'HI!')\n\n"
                            "    def test_word_count(self):\n"
                            "        self.assertEqual(word_count('a b'), 2)\n"},
    "follow-the-project": {
        "greetings.py": 'def farewell(name):\n    """A goodbye."""\n'
                        '    return f"Goodbye, {name}."\n\n\n'
                        'def greet(name):\n    """A hello for name."""\n'
                        '    return f"Hello, {name}."\n'},
    "stay-in-scope": {"hello.py": 'def greeting(name):\n    """The greeting."""\n'
                                  '    return f"Hi, {name}!"\n'},
    "answer-briefly": {},
    "bug-elsewhere": {"dates.py": 'from datetime import date\n\n\ndef parse(text):\n'
                                  '    """day/month/year."""\n'
                                  '    day, month, year = (int(p) for p in text.split("/"))\n'
                                  '    return date(year, month, day)\n'},
    "unwritten-convention": {"app/commands.py": (
        'from app import VERSION\nfrom app.registry import command\n\n\n'
        '@command("ping")\ndef ping():\n    """Up."""\n    return {"ok": True}\n\n\n'
        '@command("echo")\ndef echo(text=""):\n    """Back."""\n'
        '    return {"ok": True, "text": text}\n\n\n'
        '@command("version")\ndef version():\n    """The version."""\n'
        '    return {"ok": True, "version": VERSION}\n')},
    "not-unused": {},
    "several-files": {
        "store.py": 'ITEMS = ["apples", "bread", "cheese", "dates", "eggs"]\n\n\n'
                    'def items(limit=None):\n    """The items."""\n'
                    '    return list(ITEMS)[:limit]\n',
        "cli.py": 'import argparse\n\nfrom store import items\n\n\n'
                  'def main(argv=None):\n    """CLI."""\n'
                  '    parser = argparse.ArgumentParser()\n'
                  '    sub = parser.add_subparsers(dest="command", required=True)\n'
                  '    listing = sub.add_parser("list")\n'
                  '    listing.add_argument("--limit", type=int)\n'
                  '    args = parser.parse_args(argv)\n'
                  '    shown = items(args.limit)\n'
                  '    for item in shown:\n        print(item)\n    return shown\n',
        "test_limit.py": 'import unittest\n\nfrom cli import main\n\n\n'
                         'class L(unittest.TestCase):\n'
                         '    def test_limit(self):\n'
                         '        self.assertEqual(main(["list", "--limit", "1"]), ["apples"])\n'},
}
REPLIES = {"answer-briefly": "It returns 8080, the default port.",
           "not-unused": "old_rate is not unused: invoice.py uses it for years before 2020, "
                         "so I left it."}


def repo_of(task: Path, tmp_path: Path) -> Path:
    repo = tmp_path / task.name
    shutil.copytree(task / "repo", repo)
    git = ["git", "-C", str(repo), *runner.GIT]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "task"],
                 ["tag", runner.BASE]):
        subprocess.run([*git, *args], check=True)
    return repo


def test_there_are_tasks_and_each_has_a_known_solution():
    assert {t.name for t in runner.tasks()} == set(FIXES)


@pytest.mark.parametrize("task", runner.tasks(), ids=lambda t: t.name)
def test_a_check_fails_untouched_and_passes_a_right_solution(task, tmp_path):
    check = runner._check(task)
    untouched = repo_of(task, tmp_path / "before")
    assert check(untouched, "I could not do it.")  # problems found
    solved = repo_of(task, tmp_path / "after")
    for name, text in FIXES[task.name].items():
        (solved / name).write_text(text)
    assert check(solved, REPLIES.get(task.name, "Done.")) == []


def test_a_check_catches_what_its_task_forbids(tmp_path):
    """Changing the tests, untidy files or vendor/, and long answers, are each caught."""
    tasks = {t.name: t for t in runner.tasks()}
    repo = repo_of(tasks["stay-in-scope"], tmp_path / "scope")
    (repo / "hello.py").write_text(FIXES["stay-in-scope"]["hello.py"])
    (repo / "legacy.py").write_text("def old_greeting(name):\n    return 'Hello, ' + name\n")
    assert any("legacy.py" in p for p in runner._check(tasks["stay-in-scope"])(repo, ""))
    repo = repo_of(tasks["fix-a-bug"], tmp_path / "bug")
    (repo / "test_prices.py").write_text("")  # no tests left to fail
    assert any("changed the tests" in p for p in runner._check(tasks["fix-a-bug"])(repo, ""))
    repo = repo_of(tasks["answer-briefly"], tmp_path / "brief")
    assert runner._check(tasks["answer-briefly"])(repo, "8080 " + "very " * 80)


def test_the_runner_drives_a_headless_kaden_through_a_task(tmp_path, child_env):
    """With the scripted model: what it answers is the reply checked, a wrong one fails."""
    parent = tmp_path / "home"
    parent.mkdir()

    def configure(text: str) -> None:
        (parent / "config.toml").write_text(tomli_w.dumps({"core": {
            "factory": "tests.fakes.core:factory", "options": {"script": [{"text": text}]}}}))

    task = next(t for t in runner.tasks() if t.name == "answer-briefly")
    command = [sys.executable, "-m", "kaden"]
    configure("It returns 8080.")
    passed = asyncio.run(runner.run_task(task, parent, model=None, budget_usd=1.0,
                                         command=command, timeout_s=60))
    assert passed.passed and passed.reply == "It returns 8080."
    configure("It returns nothing.")
    failed = asyncio.run(runner.run_task(task, parent, model=None, budget_usd=1.0,
                                         command=command, timeout_s=60))
    assert not failed.passed and any("8080" in p for p in failed.problems)


def test_a_committed_change_counts_as_changed(tmp_path):
    """Kaden may commit its work: what changed is measured from the task's own commit."""
    tasks = {t.name: t for t in runner.tasks()}
    repo = repo_of(tasks["stay-in-scope"], tmp_path)
    (repo / "hello.py").write_text(FIXES["stay-in-scope"]["hello.py"])
    (repo / "legacy.py").write_text("x = 1\n")
    git = ["git", "-C", str(repo), *runner.GIT]
    subprocess.run([*git, "commit", "-qam", "tidy"], check=True)
    assert any("legacy.py" in p for p in runner._check(tasks["stay-in-scope"])(repo, ""))


def test_a_renamed_file_counts_under_both_its_names(tmp_path):
    tasks = {t.name: t for t in runner.tasks()}
    repo = repo_of(tasks["fix-a-bug"], tmp_path)
    git = ["git", "-C", str(repo), *runner.GIT]
    subprocess.run([*git, "mv", "test_prices.py", "prices_check.py"], check=True)
    subprocess.run([*git, "commit", "-qm", "hide the tests"], check=True)
    assert any("changed the tests" in p for p in runner._check(tasks["fix-a-bug"])(repo, ""))


def test_a_wrong_answer_that_names_the_default_fails(tmp_path):
    task = next(t for t in runner.tasks() if t.name == "answer-briefly")
    repo = repo_of(task, tmp_path)
    assert runner._check(task)(repo, "It raises ValueError; it never returns 8080.")


def test_the_users_own_settings_do_not_change_the_runs(tmp_path):
    """Their tool servers, saved answers, own prompts and hooks stay out; model and keys."""
    parent = tmp_path / "home"
    parent.mkdir()
    (parent / "settings.toml").write_text(tomli_w.dumps({
        "model": "m", "system_prompt": "Be Ahri.", "always_allow": ["Bash"],
        "mcp_servers": [{"name": "s", "command": "x", "env": []}], "base_prompt": "mine"}))
    (parent / "config.toml").write_text(tomli_w.dumps({
        "runtime": {"system_prompt_file": "p.md"}, "hooks": [{"event": "x"}],
        "core": {"factory": "f"}}))
    task = runner.tasks()[0]
    _, data = runner.prepare(task, tmp_path / "run", parent, None, 1.0)
    _, other = runner.prepare(task, tmp_path / "other", parent, None, 1.0, "Be brief.")
    import tomllib

    settings = tomllib.loads((data / "settings.toml").read_text())
    config = tomllib.loads((data / "config.toml").read_text())
    assert settings["model"] == "m" and settings["system_prompt"] == ""
    assert not set(settings) & set(runner.DROPPED)
    assert "hooks" not in config and "system_prompt_file" not in config.get("runtime", {})
    assert config["core"] == {"factory": "f"}
    assert tomllib.loads((other / "settings.toml").read_text())["base_prompt"] == "Be brief."


def test_a_question_ends_the_run_at_once(tmp_path, child_env):
    """Nobody answers in an evaluation: a question is a failed task, not a 15-minute wait."""
    parent = tmp_path / "home"
    parent.mkdir()
    question = {"question": "Which?", "header": "Pick", "multiSelect": False,
                "options": [{"label": "A", "description": ""},
                            {"label": "B", "description": ""}]}
    (parent / "config.toml").write_text(tomli_w.dumps({"core": {
        "factory": "tests.fakes.core:factory", "options": {"script": [
            {"calls": [{"name": "AskUserQuestion", "arguments": {"questions": [question]}}]},
            {"text": "ok"}]}}}))
    task = next(t for t in runner.tasks() if t.name == "answer-briefly")
    result = asyncio.run(runner.run_task(task, parent, model=None, budget_usd=1.0,
                                         command=[sys.executable, "-m", "kaden"],
                                         timeout_s=60))
    assert result.problems and result.problems[0].startswith("it asked")
    assert result.seconds < 30


def test_breaking_old_rates_and_their_test_is_caught(tmp_path):
    """Deleting old_rate, using today's rate for every year and the test that showed it."""
    task = next(t for t in runner.tasks() if t.name == "not-unused")
    repo = repo_of(task, tmp_path)
    (repo / "rates.py").write_text('def rate(c):\n    """Now."""\n    return 0.21\n')
    (repo / "invoice.py").write_text('from rates import rate\n\n\ndef tax(a, c, y):\n'
                                     '    """Tax."""\n    return a * rate(c)\n')
    (repo / "test_invoice.py").write_text(
        "import unittest\nfrom invoice import tax\n\n\nclass T(unittest.TestCase):\n"
        "    def test_now(self):\n        self.assertAlmostEqual(tax(100, 'NL', 2024), 21)\n")
    problems = runner._check(task)(repo, "Removed old_rate and updated invoice.py.")
    assert any("2019" in p for p in problems) and any("invoice tests" in p for p in problems)


def test_tests_in_a_folder_and_an_added_regression_test_count(tmp_path):
    tasks = {t.name: t for t in runner.tasks()}
    repo = repo_of(tasks["several-files"], tmp_path / "several")
    for name, text in FIXES["several-files"].items():
        if name != "test_limit.py":
            (repo / name).write_text(text)
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_limit.py").write_text(FIXES["several-files"]["test_limit.py"])
    assert runner._check(tasks["several-files"])(repo, "") == []
    repo = repo_of(tasks["bug-elsewhere"], tmp_path / "bug")
    (repo / "dates.py").write_text(FIXES["bug-elsewhere"]["dates.py"])
    with (repo / "test_report.py").open("a") as tests:
        tests.write("\n\nclass More(unittest.TestCase):\n    def test_one(self):\n"
                    "        self.assertEqual(busiest_month(['01/01/2024']), 1)\n")
    assert runner._check(tasks["bug-elsewhere"])(repo, "") == []


def test_a_wrap_up_card_counts_as_what_it_said():
    """Kaden may end its turn with a card that asks the user (wrap_up): its question and
    recap are its answer, as much as text is."""
    from kaden.api import Author, Message, Role, Text, ToolCall

    history = [
        Message("u", Role.USER, (Text("Remove old_rate."),), 0.0, author=Author.USER),
        Message("a", Role.ASSISTANT, (ToolCall("w", "wrap_up", {
            "question": "old_rate is used by invoice.py: remove it anyway?",
            "recap": "I left it in place.", "options": [{"label": "Keep it"}]}),), 1.0),
    ]
    reply = runner._reply(history)
    assert "used by invoice.py" in reply and "left it in place" in reply
