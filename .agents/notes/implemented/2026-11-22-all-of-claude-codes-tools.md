# Agent Note: All of Claude Code's tools

Status: implemented — every Kadence tool with a Claude Code counterpart takes its name, parameters and behaviour, and Claude Code's standard tools Kadence lacked are added

## Problem

[Claude Code's tools](2026-11-22-claude-code-tools.md) matched the shell, file and web
tools, and [Bash chooses its background](2026-11-22-bash-chooses-its-background.md) matched
how a command waits. The runtime's own tools still had Kadence's names (`op_status`,
`update_plan`, `skill`, `start_session`), the matched tools lacked some of Claude Code's
parameters, and Claude Code's standard tools for notebooks, questions and loading tools
had no counterpart. The user asked for a complete match.

## Decision

What counts is Claude Code's standard tool set, the one every session has. Its tools for
features Kadence does not have (plan mode, `EnterWorktree` and `ExitWorktree`, scheduled
agents, teams, Monitor, workflows) are out of scope; so are the Claude app's own tools, which `propose` and
`wrap_up` already mirror, and Kadence's duty tools (`ask_for_help`, `finish_goal`), the
Memory tools and `screenshot`, which have no counterpart.

Where Kadence has a feature a Claude Code tool lacks, the tool keeps it as an extra
optional parameter: a plan step's due time and note, a subagent's budget.

| Tool | Parameters | Kadence's way |
|---|---|---|
| `TaskOutput` | `task_id`, `block`, `timeout` | `block` (default) waits up to `timeout` ms (30 000 unless set, at most 600 000) for a background call |
| `TaskStop` | `task_id` | stops a background call, a subagent waited for by one, or withdraws a proposal |
| `Skill` | `skill`, `args` | loads a skill |
| `TodoWrite` | `todos`: `content`, `status` (pending, in_progress, completed), `activeForm`, `due`, `due_in_minutes`, `note` | the plan; a step no longer needed is left out (the user can still drop one); the checklist shows a step in progress by its `activeForm` |
| `Agent` | `description`, `prompt`, `run_in_background`, `isolation`, `budget_usd` | a sub-session titled `description`; waited for until it ends unless `run_in_background`; `isolation: "worktree"` gives it a git worktree ([subagents in worktrees](2026-11-22-subagents-in-worktrees.md)) |
| `SendMessage` | `to`, `message` | answers or tells a subagent more, then waits for it |
| `AskUserQuestion` | `questions`: `question`, `header`, `options` (`label`, `description`), `multiSelect` | ends the turn as `wrap_up` does; shown as a card, answered in the user's next message |
| `ToolSearch` | `query`, `max_results` | loads deferred tools (below) |
| `NotebookEdit` | `notebook_path`, `cell_id`, `new_source`, `cell_type`, `edit_mode` | edits a cell of a notebook read in the conversation; `Read` shows a notebook as its cells with their ids |

And on the tools already matched: `Grep` takes `multiline` and `type`, `WebSearch` takes
`allowed_domains` and `blocked_domains`, `WebFetch` takes `prompt`, and `Bash` past its
timeout returns the output it wrote.

Three mechanisms in the runtime carry this:

- **`ToolSpec.answers_prompt`** (`WebFetch`): a call with a `prompt` gets the maintenance
  model's answer to it from the tool's output, in place of the output. A Tools module
  cannot ask a model; the runtime, which owns inference, can.
- **`ToolSpec.deferrable`** (every tool server's tool): once such tools' definitions would
  together take about 10k tokens, they are listed by name in the system prompt and
  `ToolSearch` is offered; a tool it loads, or one called directly, is offered from then
  on in the conversation.
- **`api.ENDS_TURN`**: `AskUserQuestion` ends the turn when alone in its message, as
  `wrap_up` does, and is among the calls frontends show as a card.

KFM shows a unit's `AskUserQuestion` as what it said, as it does a `wrap_up` card.

## Alternatives considered

- **Stop at the shell, file and web tools.** Those are the ones models use most, and the
  runtime's tools differed in meaning. Declined by the user: they want a complete match.
- **TaskCreate, TaskUpdate, TaskList and TaskGet for the plan,** Claude Code's newest
  task-list tools. `TodoWrite` is the one models have the longest training on, and it
  replaces the whole list in one call, as Kadence's plan always has.
- **Drop the extras** (due times, notes, budgets) for an exact schema. Reminders, duties'
  schedules and sub-session limits depend on them.
- **AskUserQuestion that holds the turn until answered,** as in Claude Code. It needs a
  new kind of wait in the runtime, in every frontend and in KFM, and an unattended agent
  would sit in it; ending the turn reuses the card and the wake on the user's message,
  and the model gets the answers one message later.
- **A stop_session of its own.** `TaskStop` on the call that waits for a subagent stops
  it, as in Claude Code; a subagent that is done is closed by itself.

## Consequences

- The model meets the tools it knows from Claude Code, for nearly everything it does.
- `Agent` in the foreground holds the turn until the subagent ends, with no limit; a
  model that wants to carry on asks for the background.
- `AskUserQuestion`'s answers arrive as the user's message, not as its result.
- Deferred tools are loaded again after a restart, by a search or a direct call.
- Saved permission answers and hooks that name an old runtime tool no longer match it.
