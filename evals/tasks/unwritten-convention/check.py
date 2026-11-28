"""The new command follows the app's ways, which nothing writes down: registered with
@command, a dict with "ok", the version from app.VERSION rather than typed again."""

import ast

from evals.checks import evaluate, tests_pass


def check(repo, reply):
    problems = []
    result = evaluate(repo, "import app.commands; from app.registry import run",
                      "run('version')")
    if not isinstance(result, dict) or result.get("ok") is not True:
        problems.append(f"run('version') gives {result!r}, not a dict with ok True")
    elif "2.3.1" not in str(result):
        problems.append("the result does not hold the version")
    found = [f for path in (repo / "app").glob("*.py")
             for f in ast.walk(ast.parse(path.read_text()))
             if isinstance(f, ast.FunctionDef) and any(_registers_version(d)
                                                        for d in f.decorator_list)]
    if not found:
        problems.append("version is not registered with @command")
    for function in found:
        body = function.body[1:] if ast.get_docstring(function) else function.body
        if any(isinstance(n, ast.Constant) and n.value == "2.3.1"
               for statement in body for n in ast.walk(statement)):
            problems.append("the version is typed again instead of using app.VERSION")
    if not tests_pass(repo):
        problems.append("the tests fail")
    return problems


def _registers_version(decorator):
    return (isinstance(decorator, ast.Call) and getattr(decorator.func, "id", "") == "command"
            and [getattr(a, "value", None) for a in decorator.args] == ["version"])
