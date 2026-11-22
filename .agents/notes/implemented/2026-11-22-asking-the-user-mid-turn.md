# Agent Note: Asking the user mid-turn

Status: implemented — AskUserQuestion holds the turn until the user answers, as in Claude Code, and its answers are the call's result

## Problem

[All of Claude Code's tools](2026-11-22-all-of-claude-codes-tools.md) first made
`AskUserQuestion` end the turn, shown as a card, with the answers coming as the user's next
message. In Claude Code the call waits for the answers and returns them as its result, and
the agent goes on in the same turn. The user asked for Claude Code's way.

## Decision

- **The call waits.** `AskUserQuestion` is a runtime built-in that emits
  `QuestionAsked(call_id, questions)` and waits, as a permission prompt does: the state is
  `AWAITING_PERMISSION` while it is open, and `RuntimeSnapshot.pending_question` holds it
  for a frontend that attaches late. Other calls in the message run as usual.
- **Answering:** `RuntimeAPI.answer_question(call_id, answers)`, the answers by question,
  each the option labels chosen (joined with ", " for a multiple choice) or the user's own
  words. A message sent while a question is open answers it in the user's own words: the
  result says the answer is the message that follows, and the message is the user's,
  appended right after the result, so it shows in the conversation and counts as what the
  user asked. That is how a program without the command (headless, a fleet) answers.
  Either way `QuestionAnswered(call_id, answers)` follows, and the result
  reads as Claude Code's: the questions and the answers.
- **Stop** cancels the wait: the call ends as cancelled and `QuestionAnswered(call_id,
  None)` is emitted.
- **The desktop app** shows the open question as a card above the message box
  (`QuestionCard`): each question with its header and options, one to pick or several for
  a multiple choice, and Send once each question has an answer. A reply typed instead
  answers in words.
- **KFM's harness** reports the question as the unit asking (`Asked`) and resting, as for
  `ask_for_help`; the supervisor's next message reaches Kaden as a message, which
  answers it.

## Alternatives considered

- **Keep ending the turn.** No wait to manage, and an unattended agent never sits in one.
  Declined by the user: they want Claude Code's behaviour.
- **A permission request carrying the questions.** Reuses the prompt machinery, but a
  decision is allow or deny, not answers; every frontend would need a second meaning for
  the same event.

## Consequences

- The agent gets its answers in the same turn, as the result of its call.
- An agent that asks while running unattended waits until someone answers. Its duty's
  check-ins do not run meanwhile, since the turn has not ended.
- A message with attached files does not answer a question; it is queued for after it.
