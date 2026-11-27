# Agent Note: Step descriptions

Status: implemented — a tool call can carry a plain-language sentence the model writes, shown in place of the tool and its input

## Problem

Frontends showed a tool call as its name and raw arguments (`shell · curl -sL -m 20 …`,
`remember {"text": "…"}`). That means nothing to a non-technical user, and a permission
prompt they cannot read gets clicked away. Claude Code shows a short line per step instead.

## Decision

- A call carries its sentence in an optional argument, `api.DESCRIPTION_ARG`
  (`"step_summary"`), which the tools that want one declare in their own schema: see
  [tools declare their own step summary](2026-11-27-tools-declare-their-step-summary.md).
  Events, snapshots and the transcript keep it, so frontends show it live and in history.
- Frontends show the description as the step's title and a permission prompt's headline,
  with the exact input below it or in a tooltip (`frontends.common.call_parts`). Once a
  step succeeds its first word is put in the past tense ("Searched the web for …",
  `frontends.common.past_tense`) when it is a verb in Kaden's list; other sentences, and
  failed or refused steps, stay as the model wrote them. Without
  one they show the tool name and its input. No frontend knows any tool by name.
- Steps fold into groups. A run of steps, with the thinking before and between them, shows
  as one closed line until clicked: the latest unfinished step's description and state
  while any runs, then the one step's description, or "N steps" with what went wrong
  ("4 steps · 1 failed"), one rule in `frontends.common.group_title`. Reply text,
  notices and user messages end a group; thinking after a run's last step stays outside
  until a step follows it, so a reply's thinking is never folded away. The desktop app
  derives groups from its items (`viewmodel.step_segments`).
- The automatic permission review leaves the description out of what it judges: the model
  could describe `rm -rf ~/photos` as "tidy up a few files".

## Alternatives considered

- **Group lines by kind of tool** ("Ran 3 commands, read 2 files"). Says more than "5
  steps", but the frontends would have to know the tools again. The count, the failures and
  the live step's own sentence need no tool knowledge.
- **One card per step, always visible.** What shipped first: on a busy turn the cards
  outweighed the answer, and "done" on every card was noise.

- **A fixed sentence per tool in the frontends** ("Search the web for “{query}”"). Built
  first. The frontends had to know the built-in Tools module's tool names, tools from any
  other module got no sentence, and a fixed pattern says what a call touches, not why.
- **A sentence pattern declared in each ToolSpec.** Keeps frontends generic, but every
  module has to write patterns, and patterns still cannot describe complex calls; the
  shell needed a model-written description anyway.
- **A required description.** More consistent titles, but a call without one would fail
  validation and cost a retry; the fallback (name and input) is good enough.

## Consequences

- One way to show a step for every tool that declares a sentence, with a fixed phrase
  (`common.tool_sentence`) for the rest.
- About 10–20 more output tokens per call of a tool that declares it.
- Models sometimes leave it out, smaller ones more often; those steps show the tool and
  its input.
- Simple steps read wordier ("Read your notes" rather than "Read notes.md"); the exact
  input is one hover away.
- The sentence can mislead. Prompts always show the exact input under it, the docs say to
  trust the input, and the review ignores the sentence.
- The answer, not the work, fills the conversation; seeing every step takes one click, which
  matters when checking what the agent did.
