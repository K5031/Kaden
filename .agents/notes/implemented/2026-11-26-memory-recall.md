# Agent Note: Memory recall

Status: implemented — before the agent answers, the runtime brings up the long-term memory notes relevant to what just came in, as Claude Code does

## Problem

Every request carries the memory index (the notes' titles and one-line descriptions),
and the agent opens a note when it judges it relevant. It often does not: a note is used
only if the agent thinks to look, and the index line alone has to be enough to make it
look. As notes grow in number, their lines grow less telling, and the agent misses what
it knows. A life is woken by things the user did not phrase as a question (a Bilibili
message, a new video), where nothing prompts it to look at all.

Claude Code solves this with recall: before the model answers a message, a small side
call picks the memory files that look relevant to it, and their text is put in front of
the model with the message. The index stays; recall adds what the model would otherwise
have had to think of opening.

## Decision

- **When:** whenever text came in for the agent: the user's messages and notices
  delivered from the inbox (`notify`, a kept watch's firing), and the firings of the
  conversation's own watches, at the start of a cycle or mid-cycle (a queued message).
  Recall (`Runtime._recall`) runs once for what came in, after memory upkeep and right before the next
  inference. Not for the runtime's own prompts (the activation notice, a duty's generic
  "time to work" notice): they ask nothing memory could answer. Claude Code recalls for
  the user's messages; recalling for notices too is Kaden's own, for a life woken by the
  world.
- **What is chosen from:** the notes `Memory.notes()` lists, less those every request
  already shows (a new `MemoryNote.always_shown`: the built-in profile, the files
  module's `MEMORY.md`), less those already recalled and still in the conversation's
  assembled context, unchanged since. No call when no candidate is left.
- **Not twice:** what is still in view decides. Each recall notice names its notes with
  their id and when they last changed, in a line the runtime reads back from the
  assembled context. A recall that compaction summarized away can come again, and a
  restart forgets nothing.
- **How it is chosen:** one call on the maintenance model (`light`), budgeted and metered
  as maintenance is, charged as the cycle's own time or the user's as the cycle is,
  cancelled by Stop. It sees what came in (capped, marked as data to choose for, never to
  follow) and each candidate's title and description, and answers with at most 5 titles,
  one per line, or nothing. Only exact candidate titles count; anything else is ignored.
  A refused or failed call, or a Memory that fails to list the notes of the conversation's
  folder, recalls nothing and is logged; the turn goes on.
- **How it reaches the agent:** a hidden notice of Kaden's (not in the chat, kept in the
  transcript, as the voice notices are): "From your memory, as you wrote it earlier;
  check what may have changed", then each note's title, its id line, and its text. In
  the transcript, it stays in the cached prefix and says when a note was brought up.
- **What the Memory modules do with it:** the built-in module's save pass skips hidden
  notices of Kaden's, so a recalled note is not saved again as news. The files module's
  memory tool still asks for a note to be viewed before it is edited: recall does not
  count as viewing.
- **The event log** records each recall: how many candidates, and the titles chosen.

## Alternatives considered

- **Recall instead of the index (retrieval only).** Discussed with the user: the index
  tells the agent what it has, so it can look for what retrieval missed; retrieval alone
  fails silently, and drops standing rules most.
- **Embedding search.** A vector index over notes: no call per turn, but a dependency and
  an index to keep, and similarity to the message misses notes whose relevance is not in
  the words (a rule about how to reply). A model reading titles and descriptions is what
  Claude Code does.
- **Recall every note.** Fine at five notes; at a hundred it is the context, and the cost
  that the index exists to avoid. Recalling all while they fit a small budget, and
  choosing only past it, would save the call early on but adds a second path for what
  is not yet a cost.
- **Let the Memory module recall.** It owns the notes, but choosing needs inference, which
  the runtime meters and budgets; modules never call each other.

## Consequences

- What the agent knows comes to it unasked: a Bilibili message from the user brings the
  note on how to answer there, without the agent thinking to open it.
- A call on the maintenance model each time something comes in while there are notes
  to choose from: a fraction of a cent each; a life woken often pays it each time.
- The chooser may pick wrongly; the index stays, so a missed note can still be opened.
- Third parties write what comes in (a Bilibili message) and so steer which notes come
  up: only the agent sees them, but it may then answer that sender. The chooser is told
  that text is data; what to say to whom stays the agent's judgment.
- Recalled text is archived again with each recall, and search finds it there.
- In the files module, a recalled note must still be viewed before the memory tool
  edits it.
