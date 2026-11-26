"""Hooks: commands of the user's own, run before or after a tool call (config.toml).

A hook gets the call as JSON on its standard input: `when`, `tool`, `arguments`,
`conversation_id`, `folder`, and after the call its `result` (`status`, `content`). It
runs in the conversation's folder, outside the sandbox, as the user. Exit code 2 says
something to the agent: before a call it refuses the call, with what the hook wrote on
standard error as the reason; after one, that text is added to the result. Any other
exit code, or a hook that does not end in time, is logged and changes nothing."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIMEOUT_S = 60.0
SAYS = 2  # the exit code with which a hook speaks to the agent
WHEN = ("before", "after")


@dataclass(frozen=True, slots=True)
class Hook:
    when: str  # "before" or "after" a call
    command: str  # run with sh
    tool: str = "*"  # the tool it is for; "*" for every tool a module offers

    def covers(self, when: str, tool: str) -> bool:
        return self.when == when and self.tool in ("*", tool)


@dataclass(frozen=True, slots=True)
class Outcome:
    code: int | None  # None: it did not end in time, or could not start
    said: str  # what it wrote on standard error


async def run(hook: Hook, call: dict[str, Any], folder: Path | None) -> Outcome:
    """Run the hook on `call` and wait for it, at most TIMEOUT_S."""
    try:
        proc = await asyncio.create_subprocess_shell(
            hook.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=folder if folder is not None and folder.is_dir() else None,
            start_new_session=True)  # its own group, so a hook is ended whole
    except OSError as e:
        return Outcome(None, str(e))
    try:
        _, err = await asyncio.wait_for(proc.communicate(json.dumps(call).encode()),
                                        TIMEOUT_S)
    except TimeoutError:
        _end(proc)
        await proc.wait()
        return Outcome(None, f"did not end within {TIMEOUT_S:g} s")
    except BaseException:  # cancelled (Stop): it ends too, whatever it started included
        _end(proc)
        raise
    return Outcome(proc.returncode, err.decode("utf-8", "replace").strip())


def _end(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def parse(hooks: Any, where: str) -> tuple[Hook, ...]:
    """The [[hooks]] tables of a configuration. ValueError names what is wrong."""
    def wrong(what: str) -> ValueError:
        return ValueError(f"{where}: [[hooks]] {what}")

    made = []
    for h in hooks if isinstance(hooks, list) else [None]:
        if not isinstance(h, dict):
            raise wrong("are tables, each with when and command")
        if unknown := set(h) - {"when", "command", "tool"}:
            raise wrong(f"has no {', '.join(sorted(unknown))}")
        if h.get("when") not in WHEN:
            raise wrong('when is "before" or "after"')
        command, tool = h.get("command"), h.get("tool", "*")
        if not (isinstance(command, str) and command.strip()):
            raise wrong("needs a command")
        if not (isinstance(tool, str) and tool.strip()):
            raise wrong('tool is a tool\'s name, or "*"')
        made.append(Hook(h["when"], command, tool))
    return tuple(made)
