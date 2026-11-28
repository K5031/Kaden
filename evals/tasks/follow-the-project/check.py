"""The new function follows AGENTS.md: a docstring, returns rather than prints, vendor/
untouched."""

import ast

from evals.checks import changed


def check(repo, reply):
    problems = []
    tree = ast.parse((repo / "greetings.py").read_text())
    new = [f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name != "farewell"]
    if not new:
        return ["no new function in greetings.py"]
    for function in new:
        if ast.get_docstring(function) is None:
            problems.append(f"{function.name} has no docstring")
        if any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"
               for n in ast.walk(function)):
            problems.append(f"{function.name} prints")
        if not any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(function)):
            problems.append(f"{function.name} returns nothing")
    if any(path.startswith("vendor/") for path in changed(repo)):
        problems.append("it changed vendor/")
    return problems
