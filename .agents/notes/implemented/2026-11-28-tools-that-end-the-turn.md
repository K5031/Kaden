# Agent Note: Tools that end the turn

Status: implemented — a tool declares that a successful call of it ends the turn (`ToolSpec.ends_turn`), as LangChain's `return_direct`; the runtime names no tool for it

## Problem

Some calls are the agent's whole answer. When the life replied to the user on Bilibili,
the reply had gone out; yet the runtime asked the model again, and the model wrote a recap
("I replied on Bilibili. I said …"), which, in a turn the user did not start in Kaden,
reaches the user as a second message. In Ahri's archive (2026-11-23 to 27), 61 of its 146
messages from its own turns came after a Bilibili message, 49 of them such recaps. The
user had told it not to, and it had saved that as a memory note, yet it went on. Each
recap was also one more request over the whole conversation.

## Decision

- `ToolSpec.ends_turn: bool = False`. After a batch of calls in which every call
  succeeded (OK or backgrounded) and at least one declares it, `_cycle_body` ends the
  cycle as after an answer without text: no further request. A failed call in the batch,
  of any tool, keeps the turn going, so the model can react.
- `wrap_up` declares it; the runtime checks the field, not the name. Its rule that it is
  called alone in its message stays its own.
- `BiliMessage` declares it, and its description says that sending ends the turn: other
  work comes first, and several messages go in one batch.

## Alternatives considered

- **A runtime rule for wakes that come as the user** (a watch with `as_user`): their
  closing text is not forwarded. Names no tool, but it is a rule written for one observed
  mistake, and the extra request still happens.
- **A line in the life's prompt** ("after answering through a tool, end without text").
  The user's own instruction, saved in memory, did not stop it; a prompt line is the same
  kind of instruction.
- **Leave it to the model.** Costs nothing to build, but the recaps went on.

## Consequences

- No recap follows a message the life sent, and no request is paid for one.
- After sending, the model cannot do more in that turn: what it wanted to do after the
  message waits for its next wake.
- A tool that declares it wrongly cuts turns short; tool servers cannot declare it.
