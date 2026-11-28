# Agent Note: Sub-sessions

Status: implemented — the frontend hosts sub-sessions: it starts headless Kaden children for the agent, lists them under their conversation, and opens each like a conversation of its own

## Problem

The user uses an app (the Claude desktop app) where an agent hands self-contained tasks to
sub-sessions. They work on their own, report back, and the user can open any of them to see
what it is doing. Kaden could not do this. An agent can only work in its one conversation; a
long side task fills its context and blocks the conversation while it runs.

[Headless units](2026-11-11-headless-units.md) made the child easy: a
Kaden started with `--headless` takes a task as its first message and answers it. What is
missing is the host. In the Claude app the app itself starts and holds every session, so
listing and peeking are trivial. In Kaden the user-facing host is the frontend.

## Decision

### Frontends may offer tools

- `Frontend.tools()`, optional (default none): Tools modules the frontend runs, offered
  after every other module's. `run_instance` builds the frontend before the runtime, so it
  passes them into the runtime's Tools list. They are ordinary Tools modules: judged,
  gated, counted and closed like any other ([module-provided
  tools](2026-11-10-module-provided-tools.md)).

### The sub-session manager

`kaden.frontends.sessions.SubSessions`, a Tools module with no UI code, which the
desktop app creates and offers. It offers:

- `Agent(description, prompt, run_in_background, budget_usd, isolation)`, Claude Code's
  subagent tool ([all of Claude Code's tools](2026-11-22-all-of-claude-codes-tools.md)):
  starts a child, a separate Kaden agent with its own context, in the conversation's
  work folder, with `prompt` as its first message, titled `description`. The agent calls it when the
  user asked for the work; when it is its own idea it proposes the call
  ([proposals](2026-11-11-proposals.md)), as the Claude app asks before a session the agent
  proposes. The call waits for the child, or with `run_in_background` runs in the
  background, and returns the child's last reply, whole, once it is done: its turn has
  ended and no background work of its own still runs. A child that needs something says
  so in that reply ([no duty mode](2026-11-28-no-duty-mode.md)).
  With `isolation: "worktree"` it works in a git worktree of its own
  ([subagents in worktrees](2026-11-22-subagents-in-worktrees.md)).
- `SendMessage(to, message)`: answers the child or gives it more to do, then waits
  the same way.
- A child is stopped with `TaskStop` on the call that waits for it.

How a child runs:

- `<python> -m kaden --headless --data-dir <data>/sessions/<id>`: its own data folder,
  with the parent's `config.toml` and `settings.toml` copied in, its spending limit set to
  `budget_usd` when it has one (none by default, never the parent's) and its work folder
  set to the conversation's. Keys come from the same OS
  keychain. The permission ceiling passes down as for any child process.
- The manager starts its conversation and sends it the task. The child has no life
  ([duty](../rejected/2026-11-11-duties.md)): it does not go on by itself after its turn,
  as Claude Code's subagents do not.
- A permission prompt in the child waits for the user, as in the parent; the parent's call
  keeps waiting meanwhile.
- Cancelling the call (Stop, `TaskStop`) stops the child; closing the
  parent stops every child that is working. A child that is done or stopped is closed
  once no view has it open.
- Each child's record (`session.json` in its folder) keeps it listed after a restart.
  Opening one that is not running starts it again on its own conversation; **Resume** lets
  one that was stopped or failed carry on with its task. Nothing resumes by itself after a restart (SPEC 11).
- When a child stops working and no call of the parent's waits for it (the user talked to
  it), the parent gets a notice in its conversation, as the Claude app sends a note when a
  session ends a turn.
- While it runs the call's progress line is the child's current step, so the step card in
  the parent conversation shows it live.

### Opening a child

`SubSessions.open(child)` returns a `RemoteRuntime` (`kaden.frontends.remote`): a
RuntimeAPI over the child's channel. Every command but `restart` is a request;
`snapshot()`, which must answer at once, is read on opening and kept current from the
events. `kaden.frontends.wire` turns the channel's JSON back into API values. A frontend
therefore shows a child with the same code as its own conversation.

### What the user sees

- **Desktop app:** the sidebar is a tree, as in the Claude app. Conversations are grouped
  by project (their folder) under headings that fold, and each conversation's children
  are listed under it, with a dot while they work and their state as the second line.
  Clicking a child shows it in the conversation area: its messages and steps, its
  permission cards, Stop, and a message box that talks to it. Clicking a conversation goes
  back; the child keeps working. Memory, tool servers and settings stay the window's own.
- **Row actions:** open, resume, stop, rename, archive and delete.
- The parent conversation shows each child as a step, with its final reply as the result.

Choices are in `.agents/decisions.md` (Sub-sessions).

## Alternatives considered

- **A Tools module owns the children, outside the frontend.** No frontend change, but the
  frontend cannot see a module, so listing and peeking would need a new path from
  frontends through the runtime to that module.
- **The runtime owns them,** as it owns operations. Listing would be natural through
  RuntimeAPI, but the runtime would carry sub-sessions, which SPEC 1 keeps out of Kaden
  and every module and frontend would pay for.
- **Waiting for the KFM.** The right home for fleets, but not built, and a person using
  one window wants the Claude app's behaviour now.
- **Children in the same process.** Faster to start, but SPEC 4.3 has one agent per
  process, and a child's crash or runaway loop would take the parent with it.
- **Refusing a child's permission prompts,** since nobody watches it. Simpler, and shipped
  first, but a child could then do only what its policy allowed, and the user wanted the
  Claude app's behaviour, where a session asks the user.
- **A window per child,** or a plain text view of its work. Less code than switching the
  conversation area, but not what the Claude app does, and the plain view could not show
  steps, prompts or a message box.
- **Resuming children by themselves after a restart,** as long-running app sessions do.
  Convenient, but SPEC 11 keeps a crash from quietly repeating an action; one click is the
  price.
- **Children that go on by themselves,** on a goal with check-ins until they declared it
  reached. Built first; a child that ended its turn unfinished carried on, but goals went
  with duty mode ([no duty mode](2026-11-28-no-duty-mode.md)), and Claude Code's subagents
  work until their turn ends.
- **A real tree widget** for the sidebar. Native folding, but the rows' drawing, menus and
  shortcuts are built on a list; headings and indented rows in the same list keep them.

## Consequences

- The agent can run side tasks in parallel without filling its own context, and the user
  opens, talks to, approves and stops each as in the Claude app.
- **Cost.** Each child is a full agent with its own context. A spending limit caps it only
  when the user or the agent sets one, as with Claude's sub-agents; the parent's own limit
  does not include it.
- **A child's work fits in its turns.** One that ends its turn before the task is done
  stops there; the parent reads its reply and sends it on.
- **Children wait for approvals.** A child whose step needs the user's OK waits until the
  user opens it; the sidebar says so. Routing prompts to a supervisor agent is the KFM's
  escalation, not built.
- **Stopped on quit.** Working children stop with the window; they stay listed, and a click
  on Resume, not Kaden itself, lets them carry on.
- **Notices wake the parent.** Each notice is a request the parent pays for.
- **The channel protocol carries all of RuntimeAPI,** so it is a larger contract: a
  renamed API type or field breaks reopening a child run by an older Kaden.
- **Bundles.** A child is started as `python -m kaden` with the parent's interpreter; a
  packaged app whose interpreter cannot do that cannot start children.
