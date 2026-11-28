"""`list --limit N` shows the first N, without it everything, and a test covers the limit
(wherever the project's tests are)."""

from evals.checks import evaluate, tests_pass


def check(repo, reply):
    problems = []
    shown = evaluate(repo, "from cli import main", "main(['list', '--limit', '2'])")
    if shown != ["apples", "bread"]:
        problems.append(f"`list --limit 2` gives {shown!r}")
    everything = evaluate(repo, "from cli import main", "main(['list'])")
    if everything is None or len(everything) != 5:
        problems.append(f"`list` without a limit gives {everything!r}")
    tests = "".join(p.read_text() for p in repo.rglob("test_*.py"))
    if "limit" not in tests:
        problems.append("no test of the limit")
    if not tests_pass(repo):
        problems.append("the tests fail")
    return problems
