# Driving Kaden from a program

`kaden --headless` runs Kaden without a screen, for another program to drive: a fleet
manager, a script, a test. The program starts Kaden as a child process and talks to it
over Kaden's stdin and stdout.

```bash
kaden --headless --data-dir ~/units/writer
```

| Option | Does |
|---|---|
| `--headless` | Use stdin and stdout for the program, as below |
| `--data-dir`, `--config`, `--conversation`, `--model`, `--maintenance-model`, `--permissions` | See [Starting](using-kaden.md#starting) |

To give it work, `start` a conversation and `send` the task, as the desktop app does for
its sub-sessions. Give each unit its own data folder. Its `config.toml` sets the model, permissions and
spending limit, if it should have one; see [config.toml](settings.md#configtoml).

## The channel

[JSON-RPC 2.0](https://www.jsonrpc.org/specification), one message per line, UTF-8.
Kaden writes nothing else to stdout: anything else the process prints goes to stderr, and
so do Kaden's own errors, such as a conversation that cannot open. Closing Kaden's stdin quits it
the way closing the window does.

**Requests** have the names and parameters of the runtime's commands, every one but
`restart`. Objects may be sent with their `"type"`, as Kaden sends them; the desktop app
drives its sub-sessions this way:

| Method | Params |
|---|---|
| `start` | `conversation_id`, `model`, `seed_from`, `folder` (all optional) |
| `send` | `text`, optional `files`: paths of files to attach (an image or a PDF the model sees, a text file with its content), optional `spoken`: `true` when the user said it aloud and hears the reply, so the agent is told to answer in a way that can be listened to |
| `activate`, `stop` | none |
| `cancel_operation` | `call_id` |
| `select_model` | `model_id` |
| `set_duty` | `duty`: `{instruction, kind ("life"), min_interval_s, max_interval_s}` or `null` to end it: the life Continuous gives its conversation ([Continuous](concepts.md#continuous)) |
| `set_plan` | `plan`: a list of `{text, status, due, note}` |
| `respond` | `prompt_id`, `reply`: shaped by the prompt's `kind` (below) |
| `set_plan_mode` | `on` (true or false) |
| `snapshot` | none |
| `history` | `conversation_id`, optional `before`, `limit` |
| `list_conversations` | optional `include_archived` |
| `list_models`, `list_tools`, `settings`, `notes`, `skills` | none |
| `update_settings` | `update`: a `SettingsUpdate` object |
| `rename_conversation`, `archive_conversation`, `delete_conversation` | `conversation_id`, and `title` or `archived` |
| `search_archive` | `query`, optional `conversation_id`, `limit` |
| `inspect_memory` | `conversation_id` |
| `save_note`, `delete_note` | `title`, `description`, `text`, optional `note_id`; or `note_id` |

A refusal (asking before `start`, a life without an instruction, an unknown
conversation) is error `-32000` with the runtime's message and `data.type`, such as
`RuntimeStateError`. A `RuntimeStateError` also has `data.reason`, a word to act on:
`not_started`, `busy`, `budget`, `not_pending`, `conversation_open` or `unavailable`. Missing or unreadable parameters (a `kind` that is not `life`) are `-32602`, unknown methods `-32601`.
A message longer than 16 MiB is `-32600` (with no `id`) and is dropped; the channel
goes on. Any other failure inside a command is `-32000`, `data.type` naming the
exception.

**Events** come as notifications: `{"method": "event", "params": {"seq": 12, "event":
{...}}}`, every event the runtime emits, in order. Values are JSON objects with their kind
under `"type"`, such as `MessageCommitted`, `StateChanged`, `PromptOpened`,
and `DutyScheduled`. A jump in `seq` means events were dropped: take a
`snapshot`, ignore events up to its `seq`, and read `history`.

**Naming a step.** A tool call with a `step_summary` argument names itself (Bash and the
Bilibili tools ask for one). For another, the tool's name for people is in the snapshot's
`tool_titles` (by tool name), and `ToolTitles` carries all of them whenever they change,
such as when a tool server starts; most tools have none.

**States.** `StateChanged` carries `not_started`, `idle`, `thinking`, `running_tools`,
`awaiting_permission`, `finishing`, `stopping`, `stopped` or `failed`. The agent is at
work from `thinking` until it is `idle` again; in `finishing` its answer is complete and
Kaden saves what it learned.

**A message says who wrote it.** A `Message` with role `user` has an `author`: `user`,
or `kaden` for what Kaden itself wrote for the agent (a reminder, a life's wake-up,
background results). Its `text` is exactly what was written. To tell the agent something
that is not the user's words, use `notify` with a `text` instead of `send`.

**Prompts reach the program.** Whatever waits on you comes as `PromptOpened` with a
`prompt`: its `id`, `kind`, a one-line `text`, the tool `call` it is about and the kind's
`details`. Answer it with `respond(prompt_id, reply)`; `PromptClosed` follows (also,
with `reply` null, when it ended unanswered). The snapshot lists the open ones as
`prompts`.

| `kind` | `details` | `reply` |
|---|---|---|
| `permission` | `read_only`, `confined`, `reason`, `title` (the tool's name for people, or null) | `allow_once`, `allow_conversation`, `allow_always`, `deny` or `deny_always` |
| `question` | `questions` (AskUserQuestion's) | `{question: answer}`, the labels chosen (several joined with ", ") or your own words |
| `plan` | `plan` (markdown) | `{"approve": true}`, optionally with `"permissions"` (a level to set first), or `{"approve": false, "feedback": "…"}` |
| `proposal` | `reason`, `card`, `title` (or null) | `true` runs the call, `false` dismisses it |

A prompt with `words: true` (a question, a plan) is also answered by `send`: your
message answers it in your own words and follows it in the conversation. A kind you do not
know is shown by its `text`.

**The wrap-up card is a tool call.** A turn that leaves a decision may end with a `wrap_up` call:
`question`, up to three `options` with the agent's pick first, and an optional `recap`. Answer with `send`, using an option's `label` or anything
else. An option with a `call` is also a pending proposal with `card: true` and the
`wrap_up` call's id: `respond(id, true)` runs it. Sending a message withdraws it.

## Example

```
→ {"jsonrpc": "2.0", "id": 1, "method": "start", "params": {}}
← {"jsonrpc": "2.0", "id": 1, "result": {"type": "ConversationInfo", "id": "c41…", …}}
→ {"jsonrpc": "2.0", "id": 2, "method": "send", "params": {"text": "Summarize notes.md"}}
← {"jsonrpc": "2.0", "id": 2, "result": null}
← {"jsonrpc": "2.0", "method": "event", "params": {"seq": 3, "event": {"type": "StateChanged", "state": "thinking", "pending_ops": 0}}}
← … TextDelta, MessageCommitted, StateChanged idle …
```
