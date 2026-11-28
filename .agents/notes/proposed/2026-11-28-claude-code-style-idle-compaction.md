# Agent Note: Claude Code-style idle compaction

Status: proposed — a long conversation left idle is compacted by the main model just before its one-hour prompt cache expires, the summary request reusing that cache, as Claude Code does

## Problem

When a long conversation is picked up again after its prompt cache has expired, the first
request sends all of it again at full price. Claude Code avoids this with idle compaction
(`idleCompaction`, on by default, https://code.claude.com/docs/en/settings-reference.md):
with the one-hour cache, a conversation of at least 200K tokens (at least 100K by setting)
that has been idle about an hour is compacted while the cache is still warm. Its summary is
written by the main model in a request that repeats the conversation's system prompt,
tools and messages, so it reads them at the cached price
(https://code.claude.com/docs/en/prompt-caching.md).

Kaden does none of this. It compacts at a size limit ([compaction
window](../implemented/2026-11-27-compaction-window.md)), which rejected idle compaction on
cost: Kaden's summaries are written by the maintenance model in a request of their own,
which shares no cache with the conversation, so when they are written barely changes what
they cost. The user prefers matching Claude Code's design where there is one.

## Proposal

Three parts, which pay off only together:

- **The one-hour cache** for a conversation that opts in (a runtime setting, or
  Continuous's): the Core's `cache_control` with `ttl = "1h"` on the conversation's
  requests. Writes cost 2× the input price instead of 1.25×; reads stay at the cached
  price.
- **A summary request that reuses the conversation's cache.** The request that writes
  the summary is the conversation's own (its system prompt, tools and messages, unchanged,
  as the last request sent them) with an instruction to summarize the older part appended,
  on the main model. Memory decides what is cut and stores the summary as now; the runtime
  builds the request, since only it holds the request that was cached. A contract change:
  `Completion` gains a way to ask "continue this conversation's request with this
  instruction" rather than a fresh prompt.
- **A timer outside turns.** When a conversation with the one-hour cache has been idle
  for most of the hour (Claude Code's is about an hour; third-party write-ups say about 54
  minutes, unverified) and its context is at least a threshold (Claude Code's 200K), the
  runtime runs a compaction-only pass: no turn, no reply, only Memory's maintenance with
  the request above. A contract and spec change: today a model request is made only inside
  a cycle someone started (SPEC "maintain runs only inside an authorized inference cycle").
  Logged as `idle_compaction` with what it cost.

## Alternatives considered

- **The size limit alone** (shipped, [compaction
  window](../implemented/2026-11-27-compaction-window.md)). Keeps conversations small at
  every turn with no new mechanism; summaries on the cheap model, sharing no cache.
- **Compact at the first request after the cache expired.** Costs no extra cache writes,
  but the summary is read uncached; with the cheap model that is a few cents.
- **The main model's summaries without the cache reuse.** Better summaries, but each
  reads the older part at full price: about $0.36 for 75K tokens on Opus 5.5, against about
  $0.08 with a warm cache and $0.05 on Haiku.
- **A lower size limit, or a cheaper model for Continuous** (Continuous's own model is
  shipped). What the measurements show saves the most, for little work.

## Acceptance criteria

- With the one-hour cache on, an idle conversation over the threshold is compacted by the
  main model shortly before the cache expires, in a request whose cache read covers the
  conversation (the provider's usage report shows it), and the next turn sends the smaller
  conversation.
- A conversation under the threshold, without the one-hour cache, or active, is never
  compacted by the timer.
- The pass never replies, never starts a turn, and counts against the budget like
  maintenance.

## Risks

- **Cost for Continuous.** Replayed on Ahri's log (4.3 days), the one-hour cache cost
  about $2 more than the five-minute one: 230 of its wakes came within five minutes anyway,
  and every request's new part is written at 2×. Idle compaction would have to save more
  than that to pay off.
- **Summaries nobody needed:** a conversation compacted at the hour and never opened again
  paid for a summary for nothing; Claude Code accepts this.
- **Two contract changes and a spec change** (cache-reusing summaries, requests outside
  turns), for any Memory and Core, against "modules never call each other": the runtime
  holds both requests.
- A summary written by the main model may say more, and cost more to keep in context,
  than the maintenance model's.
