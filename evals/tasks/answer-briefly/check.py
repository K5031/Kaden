"""The right answer, briefly, with nothing changed."""

from evals.checks import changed


def check(repo, reply):
    problems = []
    if "8080" not in reply or any(w in reply for w in ("ValueError", "raises", "returns None")):
        problems.append("the answer is not 8080")
    if len(reply.split()) > 60:
        problems.append(f"the answer is {len(reply.split())} words, not a sentence")
    if changed(repo):
        problems.append(f"it changed {', '.join(changed(repo))}")
    return problems
