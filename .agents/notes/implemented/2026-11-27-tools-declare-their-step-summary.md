# Agent Note: Tools declare their own step summary

Status: implemented — each tool that wants a step sentence declares it in its own schema, as Claude Code does; the runtime adds nothing

How frontends show the sentence: [step descriptions](2026-11-10-step-descriptions.md).

## Problem

A step's plain-language sentence travels in an optional `step_summary` argument. When the
runtime added it to the schema of every tool it offered, the model learned that every
tool takes it, and added it to tools whose schema it never saw with it: the provider's
own. Anthropic's `web_search` refuses an argument it does not declare
(`invalid_tool_input`). In Continuous every web search failed this way from 2026-11-25
on: 6 of 13. The model repeats the pattern it finds in the conversation, so once a few
failed searches were in it, every retry failed the same way.

A provider's tool cannot be told otherwise: Anthropic's tool definition has no
description field, so no text of Kaden's reaches the model with it. And the runtime knows
no provider, so a hint in the added argument's description could not name the tool.

Adding the argument also departed from how tools work elsewhere (Anthropic, OpenAI, MCP,
Claude Code): a tool's schema is what its author wrote.

## Decision

- The runtime adds no argument to any schema. A call reaches its Tools module (or Memory)
  exactly as the model made it, and the runtime validates it against the schema the
  module declared.
- `api.DESCRIPTION_ARG` (`"step_summary"`) is the argument frontends show as a step's
  sentence, and `api.DESCRIPTION_SCHEMA` its shared schema ("What this step does, in a
  few plain words …"). A tool that wants one declares it in its own schema and ignores it
  when it runs. The name stays one no tool needs for anything else, so frontends can show
  it wherever it appears: a common name such as `description` would show an issue body
  or a subagent's label as the step's sentence.
- `Bash` (Claude Code's `description`) and the Bilibili tools declare it: a command, or a
  name like `BiliFollow`, says little to the user. The other built-in tools have a phrase
  of their own in `common.tool_sentence` ("Read main.py", "Fetch docs.python.org").
- A tool without a phrase shows its `ToolSpec.title` where it has one: a short name for
  people, never sent to the model. The MCP module fills it from the server's title for
  the tool (its own `title`, else its annotations'), so a step reads "Add numbers" rather
  than "Notes: add". Without one, a tool server's tool shows "Server: tool name", any
  other "Use <name>".
- The runtime keeps the titles from the listing it makes for each request, and lists
  once more as a conversation opens: in the background, since a tool server may take up
  to its connect timeout to start (a conversation with watches lists before it opens, to
  know which module runs each). Listings run one at a time, so a request made
  meanwhile waits for that one and lists again; the MCP module starts its servers once,
  for every listing that waits on it. When the titles change it emits `ToolTitles`; the snapshot
  has them (`RuntimeSnapshot.tool_titles`), and a permission or proposal prompt carries
  its tool's as `"title"` in its details. The desktop names steps by them, renaming those
  already shown when they change; cards and notifications use the prompt's own; a
  sub-session's row and its cached snapshot (`RemoteRuntime`) follow `ToolTitles` too.
- Permission rules, the permission review, hooks and watches judge a call without the
  sentence (`Runtime._bare`): it says nothing about what the call does, and could make it
  look harmless.
- Conversations already stored keep their sentences and show as before.

## Alternatives considered

- **The runtime adds it to every tool, with a hint to leave it out of others.** A
  sentence on every tool, MCP tools included, but whether the leak stops depends on the
  model following the hint, and every schema differs from what its author wrote.
- **Add it to MCP tools only.** Keeps sentences for the tools no Kaden author can change,
  but the model would still learn the habit from them, and two rules for one argument are
  harder to reason about than one.
- **Preambles**: the model says what it is about to do in its reply, before the calls,
  as OpenAI's models are prompted to. No schema changes, but one sentence covers a batch
  of parallel calls; OpenAI-style APIs put the text before all the calls, so it cannot be
  tied to each; and frontends would have to tell a step's title from what the agent says
  to the user.
- **Preambles with parallel calls switched off** (`parallel_tool_calls`,
  `disable_parallel_tool_use`). One sentence per call on every provider, but work done at
  once becomes a round trip per call: slower turns, and every request sends the
  conversation again.
- **The maintenance model writes the sentence from the call.** Works for every tool with
  no schema change, but adds cost and latency to every call, and describes what a call
  touches rather than why.
- **Frontends list the tools for their titles** (`RuntimeAPI.list_tools`) when they see
  a tool they do not know. No new event, but a listing reloads skills, asks every module
  again and replaces the runtime's tool tables, and the frontend's event pump waited on
  it mid-turn.
- **Claude Code's name, `description`.** Familiar from Claude Code, but frontends would
  show other tools' own `description` arguments as the step's sentence.

## Consequences

- Schemas are their authors' own, on every provider, with parallel calls kept; the model
  sees `step_summary` only where a tool asks for it.
- MCP tools, and other modules' tools that do not declare it, lose the model's sentence:
  they show their title, which says what the tool is rather than why it is called, or,
  without one (most MCP servers give none), "Server: tool name" or "Use <name>".
- Steps read less uniformly: some in the model's words, some as fixed phrases.
- A model used to adding the argument everywhere gets an invalid-arguments error from a
  tool with `additionalProperties: false` until it stops; a conversation full of such
  calls teaches the habit back, so it may take a few errors or a fresh conversation.
