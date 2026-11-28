# Agent Note: Compaction window

Status: implemented — what is sent can be capped below the model's window (`compact_window`, as Claude Code's `autoCompactWindow`); Continuous caps it at 150K

## Problem

A conversation is compacted when it fills 90% of the model's window. On a 1M window that
is about 900K tokens, so a conversation that never ends, as Continuous's, only grows. Each
request that finds the prompt cache expired sends all of it again at the cache-write
price. In Ahri's event log (4.3 days, Opus 5.5): 283 wakes; 52 came more than five minutes
after the one before, and so missed the cache; the conversation grew about 37K tokens a
day and stood at about 175K, so each such wake cost about $0.88 and would cost about $4.50
near 900K.

## Decision

- `compact_window`, a runtime setting in tokens (`[runtime]` in config.toml or the saved
  settings), at least 100000 as Claude Code's floor. What is sent, system prompt and tools
  included, is kept to at most that many tokens: `Runtime._context_request` caps the room
  left after the output reserve, and Memory, seeing the smaller `ContextRequest.max_tokens`,
  compacts within it as it does within the window. No new contract field (the `ContextRequest.max_tokens` comment names the cap): every share in
  `CompactionConfig` (trigger, summary size, long-term budget) follows the smaller budget,
  so the summary's cap shrinks with it.
- The output reserve stays that of the real window: capping the window instead would cut
  the reply limit with it.
- Continuous writes 150000 into its settings at each start (`life.COMPACT_WINDOW`). About
  two days of Ahri's life stay word for word; the rest is in the rolling summary, and the
  archive and `recall` keep every message. The user's chats keep the model's window.

## Alternatives considered

- **Idle compaction as Claude Code has it**: compact about an hour after the last request,
  just before its one-hour cache expires, so the summary request reads the warm cache.
  Claude Code's summary is written on the main model, for which the warm cache matters;
  Kaden's is written on the light model, so by estimate compacting just before expiry
  and compacting at the next cold wake cost the same within a cent or two. It needs a
  timer that runs a model request nobody asked for, and it fires in
  Claude Code only from 200K tokens. The saving comes from the conversation being smaller
  afterwards, which the window gives without the timer. Proposed again, with the summary
  request reusing the cache: [Claude Code-style idle
  compaction](../proposed/2026-11-28-claude-code-style-idle-compaction.md).
- **Compact at the first request after the cache expired.** Costs nothing extra in cache
  writes, since that request writes all of it anyway, but saves a few cents per compaction
  over compacting whenever the limit is reached; the size limit is what saves.
- **The one-hour cache for Continuous.** Replayed on Ahri's log it costs about $2 more over
  the 4.3 days: 40 wakes came 5 to 60 minutes apart and would have stayed cached, but every
  request writes its new part at 2× the input price instead of 1.25×, and 230 wakes came
  within five minutes anyway.
- **A `CompactionConfig` option in tokens.** Memory's own option, but a life copies its
  parent's config.toml, and a third-party Memory refuses options it does not know, so
  Continuous could not set it for whichever Memory runs.
- **100K for Continuous.** Saves an estimated $5 a day rather than $3, but keeps about a day of
  the life word for word, and the summary's share is then a larger part of a small context.

## Consequences

- Ahri's wakes after an expired cache cost less, and stop growing with the life's age.
- Ahri remembers the days before the last two only through the summary, its memory notes
  and `recall`; summaries are rewritten more often, so their details fade sooner.
- The life's value is fixed in code: a user who wants another one for Continuous cannot
  set it there.
