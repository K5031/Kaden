# Agent Note: Continuous

Status: implemented — beside the user's chats, one Kaden lives continuously: it does its own things and talks with the user when either wants to

## Problem

The user wants Kaden to feel alive: someone who lives their own life, does their own
things and is there between conversations, not a worker switched on by events. A chat
only exists when something happens: a turn runs when the user writes or a reminder falls due,
and everything is framed as work. Chats also need the opposite of a life: many fresh
conversations whose context the user controls.

The one-continuous-agent design was tried and retired in
[autonomy into sessions](2026-11-07-autonomy-into-sessions.md), because the user could not
curate what the agent sees and two modes doubled the concepts. A life needs exactly one
continuous conversation; the chats need exactly the curation that note protects. Both
have to hold.

## Decision

### One app, two views

The **Continuous** switch in the title bar, in the same place in both views, turns the
whole window between the user's chats and the life's view; it is the one way between
them. It changes what is shown, not what runs: the life goes on while the chats are up,
and what it says by itself shows there as a notice. Someone who wants an agent to talk
with wants both: the one who is always there, and fresh sessions for work.

### Its own instance

The life is a headless Kaden in `<data dir>/life/` that the app starts with itself and
drives over JSON-RPC (`frontends/life.py`), as it does sub-sessions: its memory, its one
conversation, usage and settings are its own, so it never sees the user's chats or their
memory, and they never see its life. Its view is the window's own, on that runtime,
without the chat list: its name, the step it is on as the status line (nothing while it
rests: when it wakes next is not shown, which would make it a timer),
Files, which opens its folder (`home/`) in the user's file manager; its name and who it is
are in its settings (the gear). While its process starts, the view says so.

### Its life

- **A life (`api.Duty`, of the one kind `DutyKind.LIFE`)** on its one conversation, set
  with `set_duty` on every start: who it is, its pace and its model as the app has them
  now. A life has `Rest`, and is woken again when it is resumed. It is the one way a
  conversation goes on by itself ([no duty mode](2026-11-28-no-duty-mode.md)).
- **Asleep until something wakes it.** After a turn it rests until the user writes,
  something it watches happens, its own background work finishes, or a time it chose
  comes: `Rest` until a time or for some minutes, or a step of its plan with a due time,
  at most a day ahead. Kaden does not wake it because time passed. Its prompt states what
  wakes it as a fact. Woken at a time it chose, it is told how long it rested and the time
  ("1 minute passed; it is 22:15.").
- **The first piece of its work to finish wakes it,** not the whole batch: a watcher is
  any tool that waits in the background, and one that finishes while other work runs
  still wakes it. Its plan stays open between turns: it is not sent back to close it.
- **A question it puts to the user does not hold it.** A permission, question or plan
  prompt left unanswered for 3 minutes closes by itself, refused or unanswered, with a
  note that the user may be away; the user may be talking to it on Bilibili, where no
  card can be answered.
- **Who it is** is the user's: a name and, if they like, who they are; left empty, it is
  theirs to find out. The user can change it at any time.
- **Its own prompt** (`life.PROMPT`): that it has a life of its own and tools to live it,
  that the user can see the files in its folder (no file is named: a named journal would
  suggest the habit of keeping one), then Kaden's facts about its tools. Not the chats'
  (an assistant's), nor the user's instructions for them.
- **Its own time costs at most a daily amount** (US$1 until the user sets another in its
  settings, or none), on the user's cheaper model;
  talking with the user does not count toward it and is answered with their chosen model.

Nothing else shapes what it does: abilities (its tools, its folder, the web, Bilibili)
and facts (the user can see its folder), never routines, quotas, schedules or moods.

## Alternatives considered

- **An app of its own.** Built and rolled back the same day: Kaden and the life's app
  were one engine with two frontends, the life's window borrowed the chat window with half
  of it hidden, and a user who wants an agent wants both in one place.
- **Its runtime in the app's process.** No wait while it starts, but two runtimes in one
  process share the exported permission ceiling and each other's failures.
- **Awake by default** (going on a minute after each turn unless it chose to rest). It
  was built after "asleep" first failed, but woken every minute it only said "(Still
  resting.)", a full turn each time: nudges with nothing to do bought nothing. The user
  had it removed.
- **Asleep, waking only at a time it sets itself, before it was woken by the world.**
  Tried with a day as the net: the model ended every turn without setting one, and
  slept a day. Since then the user's Bilibili messages, followed channels and finished
  work wake it, and `Rest` makes choosing a time plain.
- **Resting only through a plan step's due time.** It kept saying it rested and was then
  woken every minute to say so again, a full turn each time: a step with a due time is
  not how it thinks of resting.
- **A fixed beat** (woken every 15 minutes). A rhythm set for it, and the user does not
  want its behaviour decided in advance.
- **Routines, quotas, a share score and moods,** as Bilibili's "raising a fish" projects
  have. Declined by the user: the more constraints, the more it is faking a person.
- **Standing work with a persona** ("keep the user company", a
  [duty](../rejected/2026-11-11-duties.md)). Still a task on a schedule, with no memory of
  its own and no view.
- **A watcher flag on `ToolSpec`,** so only watchers wake it early and other background
  work keeps the batch rule. Fewer wake-ups with parallel jobs, but MCP servers cannot set
  the flag, and waking early only costs a turn that ends at once.
- **It sees the user's chats.** It could ask how the bug went without being told, but it
  would read everything the user does. Declined: it would feel watched.
- **Switching views pauses it.** Cheaper, but then it lives only while watched.

## Consequences

- One install and one window; the life runs in a second process, so its view waits for it
  to start the first time it is opened in a run.
- A life is paid for by the call: it spends only when something wakes it or it chose a
  time; between those it costs nothing, and does nothing.
- A long life outgrows any context: what it is depends on compaction and its memory.
