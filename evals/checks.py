"""What a task's check may ask of the repository Kaden worked in."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

TIMEOUT_S = 120


def changed(repo: Path, base: str = "eval-base") -> list[str]:
    """The files changed, added or removed since the task began (its commit, tagged
    `base`), committed or not, relative to `repo`."""
    git = ["git", "-C", str(repo)]
    # A rename is its old name and its new one, so a moved file is never out of sight.
    diff = subprocess.run([*git, "diff", "--name-only", "--no-renames", base],
                          capture_output=True, text=True, check=True).stdout.splitlines()
    new = subprocess.run([*git, "ls-files", "--others"], capture_output=True, text=True,
                         check=True).stdout.splitlines()
    return sorted(p for p in {*diff, *new} if p and "__pycache__" not in p)


def tests_pass(repo: Path) -> bool:
    """Whether the repository's unittest tests all pass."""
    try:
        run = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(repo), "-t", str(repo)],
            cwd=repo, capture_output=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return False
    return run.returncode == 0


def call(repo: Path, module: str, function: str, *args: Any) -> Any:
    """`module.function(*args)` from `repo`, in a process of its own; None when it fails."""
    return evaluate(repo, f"from {module} import {function} as f",
                    f"f(*json.loads({json.dumps(json.dumps(args))}))")


def evaluate(repo: Path, imports: str, expression: str) -> Any:
    """The value of `expression` after `imports`, run in `repo` in a process of its own, as
    JSON (its output's last line: what the code prints comes before); None when it fails."""
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); " + imports + "; "
            f"print(json.dumps({expression}))")
    try:
        run = subprocess.run([sys.executable, "-c", code, str(repo)], cwd=repo,
                             capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return None
    lines = run.stdout.strip().splitlines()
    if run.returncode != 0 or not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None
