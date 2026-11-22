# Agent Note: One way to answer the agent

Status: implemented — whatever waits on the person (a permission, a question, a plan, a proposal) is one kind of prompt in the contract, settled by one command

## Problem

Each thing that waits on the person grew the contract on its own: permission prompts,
proposals, [questions](2026-11-22-asking-the-user-mid-turn.md) and
[plans](2026-11-22-plan-mode.md) each had their own event pair, snapshot field and
command, and their own case in the remote client, in the desktop app and in KFM's
harness. Every new waiting tool added another set. All four are the same thing: the
agent's work waits until the person replies.

## Decision

- **`Prompt(id, kind, text, call, details, words)`** (`api.py`): something that waits on
  the person. `kind` is a `PromptKind`: `permission`, `question`, `plan` or `proposal`;
  `text` says in a line what it asks, for a frontend with no card for the kind; `call` is
  the tool call it is about; `details` holds the kind's own data; `words` says whether a
  message in the person's own words answers it. `PromptKind` documents each kind's
  details and reply in one place.
- **`PromptOpened(prompt)`** and **`PromptClosed(prompt_id, reply)`** (reply None: it ended
  unanswered: Stop, a withdrawn proposal), **`RuntimeSnapshot.prompts`**, and
  **`RuntimeAPI.respond(prompt_id, reply)`**: a `Decision` for a permission, the answers by
  question for a question, `{approve, feedback, permissions}` for a plan, true or false
  for a proposal; a reply the kind does not take is a `ValueError`.
- The runtime keeps one table of open prompts (`Runtime._open`). The state is
  `AWAITING_PERMISSION` while any prompt but a proposal is open; a proposal waits beside
  the agent's work. A message sent while a prompt that takes words is open answers it,
  and is kept as the person's message after its result.
- Decisions nobody was asked for (policy, saved rules, the automatic review, grants) are
  no longer events; the event log records each as before.
- The desktop app has a card per kind and `AnyPromptCard` for any other: the prompt's
  text and a reply in words. The remote client, the headless channel and KFM's harness
  handle every kind through the same event and command.

## Alternatives considered

- **Keep one set per kind.** Each is typed exactly, but every new waiting tool grows the
  contract, every frontend and KFM.
- **One command, typed events per kind.** Halves the growth; a new kind still needs a new
  event that every client must learn.

## Consequences

- A new kind of thing to ask the person needs no change to the contract, the wire, the
  remote client or KFM; the desktop app shows it with the generic card until it is
  given its own.
- `details` and `reply` are loosely typed: a frontend that misreads a kind's shape fails
  at run time, not at a type check. The shapes live on `PromptKind`.
- Frontends no longer see automatic permission decisions as events.
