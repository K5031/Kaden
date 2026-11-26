# Agent Note: Standing watches

Status: implemented — a watch the runtime keeps, apart from operations: run again after each firing and after a restart, each firing a notice that wakes the conversation

## Problem

Continuous needs input from the world ([Following things](../../todo.md),
[a Bilibili account of its own](../proposed/2026-11-25-a-bilibili-account-of-its-own.md)): new videos
from the channels it follows, and the user's messages to its Bilibili account. Today the
only way something outside wakes it is a tool call that waits (`BiliFeed` or
`BiliMessages` with `wait`), runs in the background, and wakes it when it finishes.

That makes watching the life's chore, and it loses things:

- A wait ends when it fires: until the life starts another, nothing is watched.
- A wait ends when Kaden quits (the runtime marks it interrupted on resume), and Kaden
  restarts about daily: every watch dies within a day unless the life notices and starts
  it again.
- The user writing to it on Bilibili wakes it only if it happens to be waiting. In the
  app, the user writing always wakes it ("the user writing wakes you" is one of the facts
  of its life); the same message by another door should too.

## Decision

### Watches are the runtime's, not background operations

A background operation is work that ends: the runtime will not switch conversations while
one runs (`start` refuses), sends back or closes a plan only once none does, counts them
in its state, and bills their time against a duration limit. A watch never ends, so it
is kept apart from operations (`_ops`) altogether: a **watch** is a task the runtime runs
for a conversation, with a stable id (`w1`, `w2`, …).

- **A watch tool** (`ToolSpec.watch`) is a call that waits for something outside and
  finishes with what happened (as `BiliFeed` and `BiliMessages` with `wait` do now). It is
  read-only. Calling one goes through the usual path (validation, hooks, the permission
  gate, the tool-call budget), but the runtime does not run it as an operation: it answers
  at once, `Watching (w3): BiliFeed`, and from then on runs the call in the watch's own
  task, again each time it finishes. A second call with the same tool and arguments answers
  the watch that is already there.
- **A firing** goes into the conversation as a notice of Kaden's (`notify`'s path):
  `[Watch w3, BiliFeed] New videos: …`. It wakes the conversation:
  - a watch the life started, as its own time (a new duty cause, `duties.WATCH`): within
    its daily amount, held to the next day once the day's share is spent;
  - a watch a module keeps for the user (below), as the user's own message does: at once,
    and not its own time.
- **Delivered only when delivered:** the tool's output carries a confirmation
  (`ToolOutput.confirm`), which the runtime calls once the notice is in the conversation;
  only then does the tool mark what it gave as delivered (its mark in the data folder). A
  firing lost to a crash is given again. The watch calls its tool again only after that:
  looking before the mark moved would see the same thing again, and fire it again.
- **Stopping:** `TaskStop` with the watch id ends it for good. The user's Stop ends the
  turn under way, not watches. Shutdown ends their tasks silently: nothing is written.
- **Failures:** a call that fails is tried again after a wait that doubles from a minute
  to an hour, and the conversation is told once. One that says it cannot pass
  (`ToolOutput(retry=False)`, as when the Bilibili login is gone) ends the watch, and the
  conversation is told why.
- **After a restart:** each conversation's watches are kept in `<data dir>/watches.json`
  (versioned, written whole through a temporary file; another version is refused): id,
  tool, arguments, who keeps it. When the conversation is opened again they start again,
  after the permission check, silently: nothing in the conversation says they stopped.
  The conversation store does not change.
- **What it knows:** what it started (`Watching (w3)`), what it stopped, and each firing
  are in its conversation. Nothing is added to the system prompt, which stays the same, so
  the provider's cache does too.

### Watches a module keeps

- A Tools module may name watches it keeps on a conversation with a duty (a life
  included), whatever the conversation does (`Tools.standing_watches()`), read when a
  conversation is opened and after each cycle, as `list_tools` is. The runtime starts each
  named one that is not running and stops one the module no longer names.
- `StandingWatch.as_user` says how one wakes it: as the user's own message (at once, not
  the duty's own time), for the user's messages by another way in; otherwise as the duty's
  own time, within its day.
- The built-in module keeps one: `BiliWatchMessages`, the account's inbox, while the
  instance is logged in to Bilibili, as the life's own time: anyone can write to an
  account.
- Follows stay the life's to choose: it starts `BiliWatchFeed` when it wants it. The
  plain `BiliFeed` and `BiliMessages` look once.

### Unchanged

- Waiting costs nothing; a firing is one wake. Bursts are batched by the tool (the feed
  waits 5 minutes after the first new video).
- The runtime knows nothing of Bilibili: a module says which tools are watches and which
  watches it keeps for the user; the runtime runs them and wakes the conversation.
- A conversation's watches run while it is open in the process; the life always is.

### The contract

`ToolSpec.watch`, `ToolOutput.confirm` and `retry`, `StandingWatch` and
`Tools.standing_watches()` are in `api.py`; the watch id answered for a watch call and
taken by `TaskStop`, and `duties.WATCH`, are the runtime's; Spec section 16.4 states the
behaviour.

## Alternatives considered

- **Watches as standing background operations** (re-armed operations in `_ops`). Written
  first: the runtime would never switch conversations while one ran, never close a plan,
  and would bill its time against a duration limit; each re-arm would get a new operation
  id the life was never told, and a restart would leave "cancelled" results.
- **Every firing as the life's own time.** Simple, but a module could not say that a
  watch carries the user's own messages, which should reach the life as their message in
  the app does (`as_user`).
- **The life starts its waits again** (as now). Nothing to build, but every firing and
  every restart costs it a turn spent re-arming, and the user's messages miss it whenever
  it forgot.
- **Watches in the conversation store** (with the plan). One place for a conversation's
  state, but a new field is a new schema version, and Kaden refuses a store of another
  version: the user's chats and the life's archive would no longer open.
- **The runtime polls on the module's behalf** (a `poll()` the module answers, the
  runtime's timer). The runtime would own the timing of every kind of watch; a call that
  waits keeps each kind's own timing (a minute for messages, ten for the feed, a burst's
  wait) in the module that knows it.
- **A separate watcher process** (outside Kaden, waking it over its headless channel).
  Survives Kaden's restarts by itself, but a second program to run, and it would hold the
  Bilibili login too.

## Consequences

- A watch fires, wakes the conversation with what happened and watches again without the
  life doing anything; it never holds up switching conversations, a plan or Stop, since it
  is no operation (`tests/test_watches.py`).
- After a restart a conversation's watches run again and its conversation shows nothing
  of it; a firing lost to a crash before it reached the conversation is given again
  (`ToolOutput.confirm`).
- A Bilibili message wakes the life within 5 seconds while a conversation is going, else
  within 30 (`MESSAGE_ACTIVE_S`, `MESSAGE_CHECK_S`); like a channel it
  follows, on a day its own time is spent it waits for the next.
- More runtime: a task per watch, `watches.json`, back-off, and five additions to the
  contract. A conversation's watches run only while it is open; the life always is.
- Checked with stand-ins only: the real Bilibili feed and messages are tried once the life
  is logged in.
