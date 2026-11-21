# Agent Note: Leave a working conversation

Status: proposed — not built; it changes SPEC 4.3 and 10.4, so it waits for the user's word

## Problem

While a conversation works (a turn runs, or background operations are outstanding), the
desktop app refuses to open or start another: "Finish or stop the current task first."
The user either waits or stops the work. In the app the user compares Kadence with (the
Claude desktop app) every conversation works on its own, the sidebar shows which are
busy, and the user moves between them freely.

The refusal is not a window rule. A runtime holds one conversation's state
(`Runtime.start` replaces it and is refused as BUSY), a process holds one runtime (SPEC
4.3), and a data folder holds one process (`instance.lock`, SPEC 10.4). SPEC 4.3 says how
to work in two conversations at once: a second instance with its own data folder, which
shares no memory and no conversation list.

## Proposal

The window's own runtime stays on the conversation that works. A conversation opened
meanwhile runs in a **guest**: a headless Kadence child on the same data folder, shown in
the conversation area as a sub-session is ([sub-sessions](../implemented/2026-11-11-sub-sessions.md)).

### Guests

- `kadence --headless --guest --data-dir <the same folder> --conversation <id>`. A guest
  does not take `instance.lock`; it starts only when the lock is held by its parent
  process (the pid in the lock file is `os.getppid()`), and is refused otherwise. One
  process still runs one agent with one open conversation.
- It reads the same `config.toml` and `settings.toml`, and opens the same `memory.db`
  (WAL: a second process reads and writes alongside, as the decisions already record).
  Conversations, long-term memory and skills are therefore shared.
- A conversation is open in one process at a time: the window knows which guest holds
  which and never starts a second runtime on it.

### Spending is counted across processes

`usage.json` is written by each process from its own totals, so two would overwrite each
other. `BudgetTracker` changes for every instance: what was spent since the last write is
added to the file's totals under a lock on the file (`fcntl.flock`), and the totals read
back are what limits are tested against. A limit then holds for the window and its guests
together.

### Event log

A guest appends to the same `events.jsonl`, each record naming its conversation, with
`O_APPEND` writes of one line.

### The window

- `frontends/guests.py` (no UI code) starts, lists, opens and closes guests, as
  `SubSessions` does children; `MainWindow` shows one through the `Viewing` it already
  has for a sub-session (its own `Controller` and model).
- Opening or starting a conversation while the window's runtime is busy opens it in a
  guest instead of refusing. While it is free, it opens there as today.
- A click on a conversation that a guest holds shows that guest; one the window's runtime
  holds shows that.
- The sidebar marks every conversation that works, as it marks sub-sessions.
- A guest that is idle with nothing outstanding is closed once it is not shown. Guests
  stop with the window; nothing resumes by itself (SPEC 11).
- A permission prompt in a conversation that is not shown waits; the sidebar says so.

### Out of scope

- The terminal app keeps its refusal.
- A guest cannot start sub-sessions or use voice: both are the window's own, offered to
  its runtime only. A conversation that needs them is opened when the window's runtime is
  free.

## Alternatives considered

- **A second runtime in the same process.** No lock, one budget object, one Memory. But
  SPEC 4.3 keeps one agent per process, sub-sessions rejected the same for the reason
  that a crash or a runaway loop takes the others with it, and `Runtime` owns its lock,
  its modules and its log.
- **A guest with its own data folder**, as a sub-session has. No shared files, but the
  conversation would not be in the list or the store the user opened it from.
- **The window holds no runtime; every conversation is a child.** Symmetric, and what the
  Claude app does. It moves memory screens, settings, sub-sessions and voice off the
  in-process runtime: far more change for the same result on screen.
- **Move the working conversation to a child and keep the new one in the window.** A turn
  in flight cannot be handed to another process.
- **Guests report what they spend to the window**, which alone writes `usage.json`. Keeps
  the file single-writer, but a guest's limit check would lag by a round trip and the
  window becomes part of every guest's accounting.

## Acceptance criteria

- With a turn running in one conversation, opening another and starting a new one both
  work; the first keeps working and its messages are there on return.
- Both conversations' spending is in `usage.json`, and a cost limit stops whichever
  reaches it.
- The sidebar marks working conversations; a click shows the right one.
- A second `kadence` on the folder that is not a guest of the lock's holder is refused as
  today.
- Closing the window stops every guest; reopening shows every conversation idle.

## Risks

- **Two writers on one store.** SQLite serialises them, but Memory's maintenance (the
  save pass, compaction) was written for one process; two saving long-term notes at once
  could each overwrite the other's edit of the same note.
- **Cost of a guest.** A full process with its own modules and tool servers for each
  conversation that works beside another.
- **Two kinds of open conversation.** One in the window's runtime with everything, one in
  a guest without sub-sessions or voice; the user cannot tell which they got.
- **SPEC 4.3 and 10.4 change**: the lock admits guests, and "a second instance with its
  own data directory" stops being the answer.
