"""Run Kaden on a few small coding tasks with the real model, and check what it did.

Each task in evals/tasks/<name>/ is a repository (repo/), what to ask (task.md) and a check
(check.py: check(repo, reply) -> the problems found, [] when it passed). Kaden runs
headless in a throwaway data folder with the user's configuration and keys (as a
sub-session does), the user's own instructions left out, allowed to change the workspace
without asking, and a spending limit per task. It costs real money: run it before and after
a change to Kaden's prompts or tools, not on every commit.

    uv run python -m evals.run [--model ID] [--tasks a,b] [--budget USD] [--runs N]
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from kaden.api import (
    PERMISSION_CEILING_ENV,
    AgentState,
    EventEnvelope,
    PromptKind,
    Role,
    StartOptions,
    StateChanged,
    Text,
    ToolCall,
)
from kaden.frontends import child
from kaden.frontends.common import prompt_summary
from kaden.frontends.remote import RemoteRuntime
from kaden.tools.shell_files import sandbox

TASKS = Path(__file__).parent / "tasks"
TIMEOUT_S = 900.0  # a task that takes longer has failed
BASE = "eval-base"  # the tag of the task's commit: what is changed is measured from it
GIT = ["-c", "user.name=eval", "-c", "user.email=eval@localhost", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null"]
# The user's settings that would make one person's runs unlike another's: their tool
# servers, saved answers, own base prompt and tools' settings. Their model and keys stay.
DROPPED = ("mcp_servers", "always_allow", "never_allow", "base_prompt", "tool_settings")


class Asked(Exception):
    """Kaden asked something: nobody answers in an evaluation, so the task failed."""
ENDED = (AgentState.IDLE, AgentState.STOPPED, AgentState.FAILED)


@dataclass
class Result:
    task: str
    problems: list[str]
    reply: str
    cost_usd: float
    seconds: float

    @property
    def passed(self) -> bool:
        return not self.problems


def tasks(names: list[str] | None = None) -> list[Path]:
    found = sorted(p for p in TASKS.iterdir() if (p / "task.md").is_file())
    return [p for p in found if names is None or p.name in names]


def prepare(task: Path, root: Path, parent: Path, model: str | None,
            budget_usd: float) -> tuple[Path, Path]:
    """Blocking: the task's repository as a git repository, and Kaden's data folder."""
    repo = root / "repo"
    shutil.copytree(task / "repo", repo)
    git = ["git", "-C", str(repo), *GIT]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "task"], ["tag", BASE]):
        subprocess.run([*git, *args], check=True)
    data = root / "data"
    settings = child.settings_of(parent, data)
    for key in DROPPED:
        settings.pop(key, None)
    config = data / "config.toml"  # its modules, without hooks or the user's prompt
    if config.exists():
        content = tomllib.loads(config.read_text())
        content.pop("hooks", None)
        for key in ("system_prompt", "system_prompt_file", "base_prompt"):
            content.get("runtime", {}).pop(key, None)
        config.write_text(tomli_w.dumps(content))
    settings.update(budget={"max_cost_usd": budget_usd}, work_folder=str(repo),
                    permissions="allow_workspace", system_prompt="")
    if model is not None:
        settings["model"] = model
    child.write_settings(data / "settings.toml", settings)
    return repo, data


class Watch:
    """Whether the child has worked since the task was sent: its events say so, however
    briefly (a polled snapshot can miss a turn that is over in a moment)."""

    def __init__(self) -> None:
        self.worked = False

    def heard(self, envelope: EventEnvelope | None) -> None:
        if envelope is not None and isinstance(envelope.event, StateChanged) and (
                envelope.event.state not in ENDED):
            self.worked = True


async def finished(live: RemoteRuntime, watch: Watch, timeout_s: float) -> None:
    """Until it has worked and stopped: idle with nothing running or queued, or failed."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if live.channel.ended.is_set():
            raise RuntimeError("Kaden's process ended: " + " | ".join(
                list(live.channel.stderr)[-3:]))
        snap = live.snapshot()
        if asked := [p for p in snap.prompts if p.kind is not PromptKind.PROPOSAL]:
            raise Asked(f"it asked: {prompt_summary(asked[0])}")
        if (watch.worked and snap.state in ENDED and not snap.operations
                and not snap.queued_messages):
            return
        await asyncio.sleep(0.2)
    raise TimeoutError(f"not done after {timeout_s:.0f} s")


async def run_task(task: Path, parent: Path, *, model: str | None, budget_usd: float,
                   command: list[str], timeout_s: float = TIMEOUT_S) -> Result:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"kaden-eval-{task.name}-") as tmp:
        repo, data = await asyncio.to_thread(prepare, task, Path(tmp), parent, model,
                                             budget_usd)
        watch = Watch()
        live = await child.spawn(command, data, cwd=repo, on_event=watch.heard)
        reply, cost, problems = "", 0.0, []
        try:
            conversation = await live.start(StartOptions())
            await live.attach()
            await live.send((task / "task.md").read_text())
            await finished(live, watch, timeout_s)
            history = await live.history(conversation.id)
            reply = _reply(history)
            if live.snapshot().state is AgentState.FAILED:
                problems.append("Kaden failed (its event log in the data folder says why)")
        except Asked as e:
            problems.append(str(e))
        except Exception as e:  # noqa: BLE001 - a run that breaks is a failed task
            problems.append(f"the run broke: {e}")
        finally:
            if live.attached:  # what it spent, whatever became of the run
                cost = live.snapshot().usage.usage.cost_usd or 0.0
            await live.channel.close(child.STOP_GRACE_S)
        if not problems:
            try:
                problems = await asyncio.to_thread(_check(task), repo, reply)
            except Exception as e:  # noqa: BLE001 - e.g. Kaden removed the base tag
                problems = [f"the check broke: {e}"]
    return Result(task.name, problems, reply, cost, time.monotonic() - started)


def _reply(history: list) -> str:  # type: ignore[type-arg]
    """All it said after the task was sent: an answer may come before a closing "Done", and
    a turn may end with a card asking the user (wrap_up), whose recap and question count."""
    start = max((i for i, m in enumerate(history) if m.from_user), default=-1)
    said = []
    for message in history[start + 1:]:
        if message.role is not Role.ASSISTANT:
            continue
        for block in message.blocks:
            if isinstance(block, Text) and block.text.strip():
                said.append(block.text.strip())
            elif isinstance(block, ToolCall) and block.name == "wrap_up":
                said += [str(block.arguments.get(k, "")) for k in ("recap", "question")
                         if block.arguments.get(k)]
    return "\n\n".join(said)


def _check(task: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(f"eval_{task.name}", task / "check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", help="the model to use; the configured one by default")
    parser.add_argument("--tasks", help="comma-separated task names; all by default")
    parser.add_argument("--budget", type=float, default=1.0, help="US$ per task (1.0)")
    parser.add_argument("--runs", type=int, default=1, help="runs of each task (1)")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".kaden",
                        help="whose configuration and keys to use (~/.kaden)")
    args = parser.parse_args(argv)
    # Without the sandbox every command would ask, and nobody answers here.
    if sandbox.find() is None or os.environ.get(PERMISSION_CEILING_ENV):
        print("evals need the command sandbox (bubblewrap on Linux) and must not run inside "
              "a Kaden session's shell", file=sys.stderr)
        return 2
    names = args.tasks.split(",") if args.tasks else None
    command = [sys.executable, "-m", "kaden"]
    results: list[Result] = []
    for task in tasks(names):
        for _ in range(args.runs):
            result = await run_task(task, args.data_dir.expanduser(), model=args.model,
                                    budget_usd=args.budget, command=command)
            results.append(result)
            mark = "pass" if result.passed else "FAIL"
            print(f"{mark}  {result.task:20} ${result.cost_usd:.3f}  {result.seconds:5.0f} s")
            for problem in result.problems:
                print(f"      - {problem}")
            if result.problems and result.reply:  # to judge the check as well as the model
                print("      its reply: " + " ".join(result.reply.split())[:400])
    passed = sum(r.passed for r in results)
    cost = sum(r.cost_usd for r in results)
    print(f"\n{passed}/{len(results)} passed, ${cost:.2f}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
