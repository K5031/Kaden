# Agent Note: No duty mode

Status: implemented — duties and goals are gone; Continuous's life is the one way a conversation goes on by itself, and sub-sessions work as Claude Code's subagents do

## Problem

A conversation could be given a duty ("keep doing it until I turn it off") or a goal
("until it is done", with `finish_goal` and `ask_for_help`), and would then check in by
itself on a schedule ([duties](../rejected/2026-11-11-duties.md)). Continuous covers
standing work of the agent's own, and sub-sessions were the one thing that used goals.
The user found duty mode redundant and wanted it gone from Kaden, sub-sessions and the
headless `--duty`/`--goal` flags included.

## Decision

- `DutyKind` has only `LIFE`, its default. `Duty` (instruction, kind, intervals, model) is
  a life, which only a person sets (`RuntimeAPI.set_duty`); Continuous does, for its own
  conversation ([Continuous](2026-11-23-continuous.md)). The stored form
  (`"kind": "life"`) and the wire form between Continuous and its process are those the
  life always had, so no Memory store is refused. Sub-session records (`session.json`)
  with the fields of a goal (`question`, `has_goal`) are not read: they are skipped as
  unreadable. A stored duty of another kind does not read,
  like any unknown enum value; over the channel, `set_duty` with another kind is an
  invalid-params error.
- There is no `finish_goal`, `ask_for_help`, `HelpNeeded` or `RuntimeSnapshot.help_needed`,
  no `## Your duty` prompt, no check-in at a maximum interval, no **Set a duty…** in the
  desktop app and no headless `--duty` or `--goal` (argparse refuses them).
- **Sub-sessions are plain tasks**, as Claude Code's subagents
  ([sub-sessions](2026-11-11-sub-sessions.md)): the child starts with the task as its
  first message and works until its turn ends. It is done when it is idle with no
  background work and no queued message, and its last reply, whole, is the result the
  parent gets. A child that needs something says so in that reply, and the parent answers
  with SendMessage; mid-turn questions and permission prompts still reach the user.
  Stopped or failed children can be resumed.
- A headless client gives a unit its task with `start` and `send`
  ([headless units](2026-11-11-headless-units.md)). KFM, which started Kaden with
  `--duty`/`--goal`, changes in its own repository.

## The plan and reminders

What duty mode built for every conversation stays:

- **The plan.** `api.PlanItem`: text, status (todo, doing, done, dropped), optional due
  time and note. The agent replaces the whole list with the runtime built-in `TodoWrite`,
  offered in every open conversation; the user edits it with `RuntimeAPI.set_plan`. It
  is in view through the agent's own `TodoWrite` call while that call is among the
  request's messages; otherwise (the user edited it, the conversation was opened again,
  the call was summarized away) it is in the system prompt. Compaction never loses it,
  and an update does not change the prompt, which keeps the provider's cache valid.
- **Stored as metadata** with the life (`ConversationInfo.duty`, `plan`) by the optional
  `Memory.save_duty`. Memory knows nothing of their meaning; the runtime reads them to
  schedule. "Continue from" copies neither.
- **Reminders.** Without a life, an open step that falls due wakes the agent, once
  (`duties.next_reminder`, `duties.reminder`), not sooner than
  `RuntimeConfig.reminder_min_s` after a turn ends. Opening a conversation counts every
  step already due as reminded: it waits for the user, so a crash never repeats an
  action. Stop and a failure put reminders off until the next message. Frontends show the
  next one from `DutyScheduled` ("Reminder at …").
- **Notices.** A cycle the runtime starts by itself begins with a notice of the cause, and
  the event log records it. The agent knows the time because every user message, notices
  included, reaches the model with the time it was sent.

## Alternatives considered

- **Remove only the desktop's Set a duty…** Smallest change, but duty mode would stay in
  the contract and the runtime for sub-sessions and KFM: the user asked for the mode itself
  to go.
- **Keep goals for sub-sessions.** Children would still check in until `finish_goal`;
  rejected by the user with the rest of duty mode.
- **Drop `DutyKind` and make `Duty` the life.** Cleaner names, but the stored and wire
  forms change: the stored conversations need a schema version, which refuses the whole
  store, or code that reads both forms, which the rules forbid.

For the plan and reminders that stay:

- **Due steps that wake only with a duty.** The first design: the agent set due times
  anyway and waited for a wake-up that never came, and "remind me" is a natural thing to
  ask in any chat.
- **A "wake me in…" tool without a plan.** Simpler, but what the agent decided would live
  only in the transcript, nobody could see where the work stands, and the agent forgets to
  call it.
- **The plan as a memory note.** Notes are long-term memory shared by every conversation,
  and the runtime could not read due times from them without knowing how a Memory module
  stores notes.

## Consequences

- One way for a conversation to go on by itself (a life), one for "later" in any chat (a
  reminder); fewer concepts in the contract, the desktop app and the docs.
- A sub-session with long-lived background work (a dev server, a watcher left running)
  never counts as done, so the parent's `Agent` call waits until it or the user stops it
  (`TaskStop`); the tool's description tells the agent not to leave such work running in
  one.
- A sub-session does not go on by itself after its turn ends: long work it would have
  spread over check-ins has to fit in its turns, or the parent sends it on.
- The names stay a duty's (`Duty`, `set_duty`, `save_duty`, `DutyScheduled`,
  `runtime/duty.py`) for a life, the price of keeping the stored and wire forms.
- KFM does not work until it is changed.
