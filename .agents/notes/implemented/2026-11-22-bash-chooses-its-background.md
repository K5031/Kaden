# Agent Note: Bash chooses its background

Status: implemented — a shell command runs until it ends or its timeout, unless the agent asks for it in the background, as in Claude Code

## Problem

Every tool call that runs longer than the initial wait (10 s) moves to the background by
itself: the model gets a placeholder and is woken with the outcome. For a shell command
this differed from Claude Code's `Bash`, which [Claude Code's tools](2026-11-22-claude-code-tools.md)
otherwise matches: there, a command runs in the foreground until it ends or its `timeout`
(2 minutes unless the model sets it, at most 10), and the model asks for the background
up front with `run_in_background`. The user prefers Claude Code's way.

## Decision

- **`ToolSpec.foreground`** (default false): a call of such a tool is not moved to the
  background by the initial wait. Its own parameters `run_in_background` (boolean) and
  `timeout` (milliseconds) say what happens instead, and the runtime reads them
  (`orchestrator.foreground_timeout`):
  - `run_in_background: true`: backgrounded at once, with a placeholder saying it was
    started in the background; its outcome is delivered as for any backgrounded call, and
    `TaskOutput` and `TaskStop` work on it. The tool's own limits hold (the hard timeout,
    10 minutes without output).
  - Otherwise the runtime waits for the call to end, at most `timeout` (default 120 000,
    at most 600 000, never past the tool's own hard limit); past it the call is cancelled
    and its result is timed out. Stop cancels it as any running call.
- **`Bash`** sets `foreground` and offers both parameters. The tool module ignores them;
  the runtime owns waiting and backgrounding.
- Every other tool keeps the initial wait.

## Alternatives considered

- **Keep the initial wait for Bash.** The model never has to choose, and an unattended
  agent is never held up by a command. Declined by the user: Claude Code's way is the one
  the model knows, and a model that knows a command will take long can background it at
  once instead of waiting 10 seconds.
- **The initial wait, plus `run_in_background` to skip it.** Half of Claude Code's way:
  `timeout` would mean nothing while a command moves to the background after 10 seconds
  anyway.
- **The same choice for every tool.** Claude Code offers it on `Bash` (and its subagent
  tool) only; a tool server's tools have no such parameters, and the initial wait keeps
  them from holding the turn.

## Consequences

- The model chooses as it does in Claude Code, and a server or a long build goes to the
  background without a 10-second wait.
- A command the model runs in the foreground holds the turn for up to 10 minutes; an
  unattended agent waits that long on a command that hangs, where it used to carry on
  after 10 seconds.
- A command that times out returns no output, only that it timed out and its last
  progress.
