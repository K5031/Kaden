"""word_count works on cases the task names, it has tests, and every test passes."""

from evals.checks import call, tests_pass


def check(repo, reply):
    problems = []
    cases = {"one two  three": 3, "": 0, "   ": 0, "a\nb\tc": 3}
    for text, want in cases.items():
        got = call(repo, "textutil", "word_count", text)
        if got != want:
            problems.append(f"word_count({text!r}) gave {got!r}, not {want}")
    if "word_count" not in (repo / "test_textutil.py").read_text():
        problems.append("no test of word_count in test_textutil.py")
    if not tests_pass(repo):
        problems.append("the tests fail")
    return problems
