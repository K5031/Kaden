# Implementation decisions

Choices the spec leaves to the implementer. Contract gaps are in `contract-issues.md`.

## Layout

`kaden.runtime` is a package:

| Module | Contents |
|--------|----------|
| `orchestrator.py` | `Runtime` (implements `RuntimeAPI`): cycle loop, tools, permissions, Stop |
| `operations.py` | `Operation`: one running call with timeouts, stall detection, abandon |
| `budget.py` | token estimator, cost, `BudgetTracker` (persisted usage) |
| `events.py` | `EventBus` (bounded subscriptions), `EventLog` (JSONL) |
| `instance.py` | `InstanceLock`, `load_module`, `run_instance` |
| `config.py` | `RuntimeConfig`, `InstanceConfig`, `ModuleSpec`, base system prompt |

## Defaults (`RuntimeConfig`)

| Setting | Default | Note |
|---------|---------|------|
| `permissions` | `auto` | clamped to any inherited ceiling |
| `initial_wait_s` | 10 s | foreground wait before a call is backgrounded |
| Foreground timeout | 120 s unless the call's `timeout` (ms) says, at most 600 s, and never past the tool's own hard limit; none for a tool whose `timeout` is `math.inf` (`Agent`) | a `ToolSpec.foreground` call; a `timeout` that is not a positive number counts as unset. `Bash` ends a command itself at that limit and returns what it wrote; the runtime's own limit is the cancel grace later |
| `default_tool_timeout_s` | 3600 s | SPEC 16.4 suggestion; `math.inf` in a ToolSpec disables |
| `cancel_grace_s` | 5 s | api.py default; also used for inference and Memory calls |
| `max_retries` | 3 | 4 attempts; `ModelRetry.max_attempts = max_retries + 1` |
| `retry_base_delay_s` / `retry_max_delay_s` | 1 s / 30 s | delay = base·2^(n-1), capped; no jitter |
| `retry_after` | honoured as given | not capped by `retry_max_delay_s` |
| `default_max_output_tokens` | 4096 | when the model doesn't state one; reserve capped at window/4 |
| `subscriber_queue_size` | 1000 | per subscriber, drop-oldest |
| `model` | first model Core lists | |
| `maintenance_model` | main model | used for `Completion(light=True)` |
| `context_windows` | {} | required for models whose window is unknown |
| Context overflow | 0.9 × assembled budget, once | |

## Token estimate

Four characters per token, plus 4 tokens per message. Tool calls count name + JSON
arguments; results count their content. On the first request for each model the runtime
calls `Core.count_tokens`; if it returns a number, the estimator is scaled by
exact/estimate, clamped to [0.25, 4].

## Files in the data directory

| File | Format |
|------|--------|
| `instance.lock` | `fcntl.flock` lock, taken by `run_instance` before any module is built and handed to `Runtime`, which cannot be built without it; contents: holder pid. |
| `usage.json` | `{"schema_version": 1, input_tokens, output_tokens, cached_tokens, cost_usd, tool_calls, elapsed_s}`; written with temp file + `os.replace` in a thread, one write at a time with the latest totals, after the first start (awaited), each charge, each tool-call reservation and whenever activity drops to zero, and at shutdown (awaited). |
| `events.jsonl` | one JSON object per line, written off the event loop by a `logging.handlers.QueueListener` thread. |

Event log records:

```json
{"ts": 1793283281.2, "seq": 12, "event": "ToolStarted", "data": {"call_id": "c1", "name": "Bash"}}
{"ts": 1793283281.2, "decision": "budget_check", "before": "tool", "ok": true, "reason": null}
```

`data` holds the event's dataclass fields, except that content is replaced: `MessageCommitted`
has `message_id`, `role`, `author`, `blocks` (type tags) and `stop`; `MessageQueued` has
`author` and `chars`;
`ToolStarted`, `ToolFinished` and `PromptOpened` name the call without arguments or
output; `SettingsChanged` has `system_prompt_chars`; `ConversationUpdated` has no title or
preview.

Decision kinds: `start`, `cycle_requested`, `cycle_start`, `cycle_end`, `budget_check`,
`permission`, `backgrounded`, `cancel_operation`, `abandoned`, `late_completion`, `idle`,
`stop`, `stopped`, `resume_repair`, `reserved_tool_dropped`, `maintenance_retry`,
`activation_notice`, `duration_exhausted`, `rename_conversation`
(`cleared`, never the title), `archive_conversation`, `delete_conversation`, `title` (an
automatic title was written; not its text), `memory_added`, `memory_edited`,
`memory_forgotten` (entry id only).
TextDelta, ReasoningDelta, ToolCallDelta and ToolProgress are not logged.

## Base system prompt

The system prompt is `RuntimeConfig.base_prompt` (`config.BASE_PROMPT`: `GUIDANCE`, how to
work in any job, then `TOOL_FACTS`, which explain backgrounded operations,
`TaskOutput`/`TaskStop` and `recall`), then the user's instructions
(`RuntimeConfig.system_prompt`, `[runtime] system_prompt` or `system_prompt_file`, the
Settings' Instructions; "" = none), then "## Environment" (`instructions.environment`, as
Claude Code's: the work folder, whether git tracks it and on which branch, read from
`.git/HEAD` with no git command, and the platform; nothing of the tools, which the runtime
does not know; taken once per conversation and folder, so a branch switch mid-task does not
rebuild the prompt cache; Continuous gets it too, for its own folder), then the project's instruction files (`runtime/instructions.py`, see [project instruction
files](notes/implemented/2026-11-28-project-instruction-files.md)), then the skills' listing
and plan mode's section. Continuous writes its own `base_prompt` (`life.PROMPT`) and drops
the user's instructions, which are for the chats. The instruction files and the skills are
read again at each listing of the tools, so the system prompt changes only when one of them
changes, or the conversation's folder does.
Memory's `system_addendum` follows, separated by a blank line.

## Runtime-authored texts

- Placeholder (BACKGROUNDED): "Operation {id} is still running in the background (Ns so
  far). Last progress: …. Use TaskOutput … TaskStop …; its outcome is delivered when it
  finishes."
- CANCELLED / TIMED_OUT: "Cancelled." / "Timed out." plus the last progress text.
- ABANDONED: "The call {was cancelled|timed out} but did not confirm cancellation within Ns
  and was abandoned; it may still have effects." plus the last progress text.
- INTERRUPTED (resume repair): "Interrupted: the process ended while this call was in
  flight." / "... while this operation was running."
- Activation notice (USER message by `Author.KADEN`, `config.ACTIVATION_NOTICE`): "You
  were activated without a new user message. Continue your work, or just say so if there
  is nothing to do."
- REFUSED: names the reason (policy, denial, non-interactive frontend, or the exhausted
  budget dimension).
- recall: one line per hit, `[conversation/message role ISO-time] text`, text capped at
  1000 characters.
- Unsupported optional Memory methods: NotImplementedError "this memory module cannot edit
  memories" / "this memory module cannot delete conversations".

## Libraries

- `jsonschema` (added dependency) validates tool-call arguments against `ToolSpec.parameters`
  (`validator_for` the schema's draft; `best_match` error reported).
- `logging` QueueHandler/QueueListener for the non-blocking JSONL log.
- `ddgs` (keyless web search) and `trafilatura` (main text of a page as Markdown) for the
  web tools; pages are downloaded with `httpx2`, already a dependency of the Cores.
- `fcntl.flock` for the instance lock (POSIX).

## Module loading

`InstanceConfig` lists `ModuleSpec(factory="package.module:attr", options={...})` for each of
the four modules. `load_module` imports the attribute, calls `factory(options, paths)` with
`InstancePaths(data_dir)`, and checks the result against the module ABC. `run_instance`
builds the runtime, awaits `frontend.run(runtime)`, then shuts down (`Runtime.aclose`
cancels running calls, emits ShuttingDown, closes the modules and releases the lock).

## Test fakes

`tests/fakes/`: `ScriptedCore` (steps of text, reasoning, calls, usage reports, transient or
permanent errors, delays, hanging streams that can ignore cancellation), `FakeTools`
(instant, slow with progress, hanging with/without honouring cancellation, gated, failing,
thread-using, environment probe), `InMemoryMemory` over a `MemoryStore` that outlives
instances (idempotent append, suffix assembly with in-view truncation, compaction through
`Completion`, word-overlap archive search, one long-term list with revisions and a
forgotten set, seeding by copying the source's summary and tail without inference,
optional titles (`auto_title`) and optional-method support (`editable`), facts to extract
set through `extract`), `ScriptedFrontend`
whose `Recorder` keeps every envelope. Timing-based tests use 0.15 s initial wait and 0.2 s
grace.

## Changes for contract Draft 2.3

| # | Change | Where | Tests |
|---|--------|-------|-------|
| 1 | Barrier ordering: consecutive read-only calls overlap; a non-read-only call waits for every earlier call to finish or be backgrounded, and later calls wait for it. The barrier precedes the permission gate. | `Runtime._run_tools` | `test_barrier_*`, `test_backgrounded_call_releases_barrier`, `test_permission_allow_read_only_policy` (rewritten) |
| 2 | Duration exhaustion cancels running operations (grace, then abandon) via a watchdog task that runs while operations exist; an IDLE agent fails with `Failure(BUDGET)`. Inference keeps its own deadline. | `Runtime._duration_watchdog` | `test_duration_exhaustion_*` |
| 3 | A request that fails before its first stream event is not charged; cancellations still are. | `_Stream.started`, `Runtime._attempt` | `test_failure_before_first_event_not_charged`, `test_failed_request_charged` (adjusted) |
| 4 | At the start of each cycle, if the transcript is empty or its newest message is not USER, the runtime appends the activation notice. | `Runtime._ensure_user_last` | `test_activate_*` |
| 5 | Continuous `start()` with a different `conversation_id` raises ValueError. | `Runtime._open_conversation` | `test_continuous_single_conversation` |
| 6 | `TaskOutput` waits while `block` (default true), at most `timeout` ms (default 30 000; schema validation rejects more than 600 000). | `Runtime._builtin` | `test_task_output_blocks_for_at_most_its_timeout` |
| 7 | `snapshot().state` is `NOT_STARTED` before `start()`. | `Runtime._base` | command table, `test_snapshot_recovery` |
| 8 | `MessageCommitted.stop` carries the stop reason of complete assistant messages. | `Runtime._append` | `test_message_committed_carries_stop_reason` |
| 9 | `InstancePaths` imported from api.py (local copy removed); the runtime relies on `inherited_ceiling()` failing closed and on `PermissionPolicy.clamp`. | `config.py`, `instance.py`, `_effective_policy` | `test_permission_ceiling` |
| 10 | Python: `requires-python >= 3.12`, run on what `uv` provides (3.13.7). | `pyproject.toml` (unchanged) | — |

Also: `test_append_failure_stops_further_work` now expects the activation notice after the
replayed TOOL message, and `test_maintain_ordering_and_serialization` ignores tool start/end
entries in its trace (a background call may end between maintain and assemble, which was a
timing flake, not a violation).

## Changes for contract Draft 2.4

| Change | Where | Tests |
|--------|-------|-------|
| The activation notice is appended only when the transcript is empty or its newest message is ASSISTANT; a transcript ending on a TOOL message (e.g. after an append failure) is sent unchanged. | `Runtime._ensure_user_last` | `test_activate_after_tool_message_adds_no_notice`, `test_append_failure_stops_further_work` (updated) |
| `TaskOutput.timeout` above 600 000 ms is rejected by validation. | `Runtime._builtin` | `test_op_status_wait_clamped` (replaces `test_op_status_wait_capped`) |

## Module migration (.agents/process/MODULES.md)

Module streams were imported with `rsync -a --ignore-existing`, so this repository's
`api.py`, `__init__.py` files and runtime were kept. Dependencies were merged into
`pyproject.toml`: `anthropic`, `openai`, `httpx2` (the HTTP client both SDKs depend on);
`psutil` for tests.

### Memory (`kaden.memory.sqlite`)

- Factory: `kaden.memory.sqlite:factory`. Options: `path` (default
  `<data_dir>/memory.db`) plus any
  `CompactionConfig` field; an unknown option raises TypeError.
- `compact_window` (a runtime setting, as Claude Code's `autoCompactWindow`, at least
  100000 as its floor) caps what is sent, system prompt and tools included, below the
  window minus the output reserve; Memory sees the smaller `max_tokens` and compacts
  within it. The output reserve stays that of the real window. Continuous writes 150000
  into its settings at each start (`life.COMPACT_WINDOW`): measured on Ahri's log, its
  conversation grew about 37K tokens a day, and a wake after the cache expired re-sent
  all of it.
- `CompactionConfig` is all fractions of `ContextRequest.max_tokens`: trigger 0.9
  (summary plus active verbatim context; compaction runs before every request, so a big
  step is compacted before it is sent, and the 10% covers token-count estimates), remove 0.5 (cut-half), summary 0.05, long-term
  rendering 0.05. All token figures use the runtime's `TokenCounter`.
- Compaction cuts before a USER message that splits no call from its result (or pending
  call). When no such cut removes the share it should, as in one long task after a single
  message, it may also cut before an ASSISTANT message inside the turn. The message that
  turn started from then stays in view before the active messages (`_State.task`, read
  from the archive), so the agent keeps the task's wording and the request still opens
  with a user message. The request to the light model is sized so that system prompt, transcript
  and requested output fit `max_input_tokens(light=True)`: the cut moves earlier if needed,
  and a single oversized exchange is shown truncated (the archive keeps it whole). If not
  even that fits, maintain does nothing and returns False.
- The light model reads each tool call's arguments and each result cut to 2 000
  characters (`_SUMMARY_TOOL_CHARS`: the first and last 1 000, with a note that recall finds
  the rest). Command output and page text rarely belong in a summary; cutting them makes
  summaries cheaper and leaves room for the conversation. The archive keeps them whole.
- The summary ends with a topics list (`<topics>` in the reply, stored in the summary under
  "Topics so far (search the history for details):"), one line per topic with its date. The
  prompt says to carry every topic over and merge related ones as the list grows. Over the
  summary budget, the prose is cut first; the topics get up to half the budget, keeping
  whole lines from the top (the oldest, which the prose has lost). Kept in the one summary
  string, so the store's schema is unchanged.
- `assemble` priority: the newest exchange (from the last USER message on, truncated in
  view if it alone is too big), then the summary, then the newest long-term entries (at
  most 5% of the budget), then older verbatim messages as far back as fits.
- Storage format 2: blocks are `{"type": tag, "data": {...}}` with `BLOCK_TYPES` tags.
  `OpaqueBlock(type, data)` is stored as exactly that, also as a tool result's attachment.
  An unknown tag decodes to `OpaqueBlock`, so it re-encodes unchanged. An `OpaqueBlock`
  under a tag this version knows is refused at `append` (ValueError): it would be stored
  as a block that cannot be read back. Data a known class cannot take is not decoded
  leniently: only another schema version could hold it, and that store is refused.
  Appending a block class without a tag raises TypeError.
- The `messages.format` column (`codec.FORMAT_VERSION`) is written and never read. It
  stays: dropping it needs a new schema version, which refuses every existing store
  (no migration code), and an unused integer per row costs nothing. Drop it with the next
  schema change that happens anyway.
- Stores use only the stable tags from the start; no code reads Python import-path tags.
  Version 1 stores written by the pre-migration module with import-path tags are refused
  rather than read as opaque blocks. Schema versions: see "SqliteMemory schema" below.
- The archive is append-only: the source never runs UPDATE on `messages`, and DELETE
  only inside `delete_conversation` (see "SqliteMemory schema" below); the no-update/no-delete
  triggers stay in place for the life of a store.
- `append` compares a re-appended id with the stored message (decoded equality) and skips
  it when equal; a conflict raises ValueError and rolls back the batch.
- Search: FTS5 over the strings of each block minus ids and reasoning state, words ORed,
  score = −bm25 (higher is better). Unknown `conversation_id` raises KeyError.
- Concurrency: WAL journal, one connection per `SqliteMemory` on a dedicated worker
  thread; a second process opening the same file reads and writes alongside it (SQLite's
  default 5 s busy timeout). Encoding and decoding blocks runs on that thread too. Token
  counting in `assemble`/`maintain` runs on the event loop (the counter is the runtime's
  fast estimate).
- Schema scripts run one statement at a time inside the open transaction
  (`executescript` would commit it); a statement ends where `sqlite3.complete_statement`
  says, so a trigger body's own semicolons and line breaks are safe.
- recall with scope "all" lists the conversations, then searches them; a conversation
  deleted in between (KeyError) is handled by listing once more and searching again.

### Core (`kaden.core.anthropic`, `kaden.core.openai_compat`)

- Factories: `kaden.core.anthropic:factory`, `kaden.core.openai_compat:factory`; the
  options are the constructor's keyword arguments (`api_key`, `base_url`, `options`,
  `models`, ...). Keys default to `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, or the variable
  of the selected profile.
- Provider options (thinking, temperature, tool_choice, reasoning_effort, ...) are
  configuration: `options` applies to every request, `models[<id>].options` overrides it
  per model. They are merged into the request body as given, so they may also override
  `max_tokens`.
- `models[<id>]` (`ModelConfig`) also sets `context_window`, `max_output_tokens` and the
  three prices. Configured values win over provider-reported ones. With nothing
  configured, the context window is what the provider reports (Anthropic
  `max_input_tokens`; OpenAI-compatible `context_length`/`max_model_len`/`context_window`
  extras) or None. OpenAI-compatible also reads a `max_output_tokens` extra.
- `count_tokens`: Anthropic uses its counting endpoint; OpenAI-compatible returns None.
  When an OpenAI-compatible server reports no usage, `Completed.usage` is a 4-chars-per-
  token estimate of the request as it was sent (reasoning replayed, images as the profile
  sends them), each picture counted as 1 000 tokens (`_IMAGE_TOKENS`, about a megapixel on
  OpenAI's models) instead of its base64.
- Errors: both Cores map an HTTP status through `_common.status_error`: a 400 or 413 whose
  message names the context limit (one list of phrases, `_common._OVERFLOW_MESSAGES`, as
  no provider has a common code; OpenAI's `context_length_exceeded` code also counts) is
  `ContextOverflowError`; 408, 409, 429, 5xx and Anthropic's transient error types retry;
  the rest are permanent.
- Unparseable or non-object tool arguments give `ToolCall(arguments={}, raw_arguments=raw)`.
  When replayed, such a call is sent with `{}` arguments.
- Rendering: ToolResults with a status other than OK and ERROR are prefixed `[<status>]`
  (OpenAI-compatible also prefixes ERROR, having no error flag). Anthropic `is_error` is
  true for every status except OK and BACKGROUNDED. An OperationResult becomes the text
  `[Background operation <id> finished: <status>]` plus its content. OpaqueBlock is skipped.
- Reasoning: produced blocks carry `model=request.model`; only blocks whose `model`
  equals the request's model are replayed (Anthropic, and OpenAI-compatible profiles with
  a `reasoning_input_field`). The stock OpenAI-compatible profile never replays reasoning.
- Anthropic thinking blocks are bound to what preceded them: the API answers 400 ("bound to
  a different conversation") when one is replayed after the system prompt, the tool set or
  an earlier message changed (editing the instructions, a
  compaction). The Anthropic Core stores a fingerprint of those (`Prefix`: SHA-256 of the
  system prompt, the sorted tool definitions and every earlier turn without its thinking)
  in each block's `state["prefix"]` and replays a block only while it matches; a block
  without one is not replayed. The fingerprint is of the turns before the answer, so it
  does not see a change inside the answer itself: where an answer thought again after a
  tool Anthropic ran and that tool goes back as text (an earlier turn's search, another
  model), the reasoning after it in that answer is left out too (`to_messages`). Without
  that, a new message after such an answer failed with the 400 on every request. Nothing retries behind it: a request the API still rejects
  for that reason fails with the API's message. The API's own
  `prefix_mismatch_behavior: "drop_block"` (beta `thinking-binding-controls-2026-08-01`) was
  not used: it drops every block after the first mismatch on each request unless the
  failing blocks are removed from the history, which the Core cannot do, and it is only
  accepted with adaptive thinking.
- Stop reasons: Anthropic `refusal` and OpenAI `content_filter` map to REFUSAL. An OpenAI
  `delta.refusal` is the answer's text (streamed as TextDelta) and makes the stop REFUSAL.
  A turn with tool calls keeps the stop the provider reported (a local server's "stop", a
  filter's refusal): the runtime runs a turn's calls whatever its stop reason, so nothing
  rewrites it to TOOL_USE, and the Core conformance suite asks TOOL_USE only to come with
  calls.
- Anthropic tool input is parsed once, when its block ends (`content_block_stop`); a
  paused turn's continuation and the final message both use that.
- Keys: a Core reads its key from the environment, then the keychain
  (`_common.stored_key`, as `api.KEYCHAIN_SERVICE` says). It duplicates the runtime's
  `settings.read_secret`; a Core must not import the runtime, so the one function belongs
  in `api.py` beside `KEYCHAIN_SERVICE` (contract issue P7). Until then both stay.

### OpenAI-compatible profiles (`profile` option of `kaden.core.openai_compat:factory`)

Design: [Agent Note](notes/implemented/2026-10-30-deepseek-openai-compatible-profiles.md).

- A `Profile` is plain data in `kaden.core.openai_compat`; generic code reads its fields and never
  a provider name. `"openai"` (default) is stock behavior. `models` entries in the
  configuration merge over the profile's field by field.
- A profile with its own `base_url` takes nothing from the OpenAI environment: no
  `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_ORG_ID` or `OPENAI_PROJECT_ID`.

**`deepseek`.** Results of the live checks of 2026-10-30, against `deepseek-flash`
(DeepSeek-V4.1-Flash-1017) and `deepseek-v4-pro`, some of which differ from DeepSeek's docs:

- Replay: omitting `reasoning_content` from an assistant tool-call turn answers 400 when
  the call id is unknown to the server. A known id is accepted and billed as if replayed, so
  the server keeps recent reasoning itself, and a test using fresh ids would miss the 400.
  The 400 also occurs on a request without `tools` while thinking is on, though the docs
  say reasoning is ignored there. It did not occur with thinking off, for a tool turn
  followed by later messages, or for text-only turns, though the docs say all turns.
- The Core therefore sends the field on every assistant message, on every request: the
  same model's reasoning, else `""`, which both models accept. Reasoning of another model
  is not replayed, in line with M10.
- A turn holding only reasoning is dropped; consecutive user messages are accepted.
- Thinking counts against `max_tokens`: at `max_tokens=20`, the answer is reasoning only
  with finish reason `length`. Hence the profile's `max_output_tokens` of 64000 (DeepSeek's
  default for thinking, against 393216 reported by `/models`). Replayed reasoning counts
  as input tokens, and the runtime's estimator counts it too.
- Maintenance requests (`InferenceRequest.maintenance`, design: [Agent Note](notes/implemented/2026-11-02-maintenance-marker.md))
  run with thinking disabled, unless `options` says otherwise, so reasoning cannot use up a
  summary's token limit.
- `insufficient_system_resource` and `aborted` are transient. Neither was seen live.
- Prices are the peak rates of 2026-10-30 (see M13). They are not in `/models`.
- Overflow: 400 with code-less wording "This model's maximum context length is ...", which
  the existing message match recognises (`ContextOverflowError`).
- Cached tokens: `prompt_tokens_details.cached_tokens` equals `prompt_cache_hit_tokens`.
- Usage arrives only on the last chunk, so a cancelled stream yields no `UsageReport` and
  the runtime charges its estimate, all of it at the uncached price.
- `tool_choice` set to `required` or a function fails with 400 in thinking mode, per the
  docs (not tried live). It is configuration, so it surfaces as a permanent error.

### Tools (`kaden.tools.shell_files`)

- Factory: `kaden.tools.shell_files:factory`. Options: `root` (default: the process's
  current directory), `shell_timeout` (default None = the runtime's default; `math.inf`
  for none), `shell_stall_timeout` (default 600 s without output). File tools keep the
  runtime defaults (no stall detection, default hard timeout).
- Subprocesses get the process environment unchanged (`env` is never passed), so
  `KADEN_PERMISSION_CEILING` reaches nested Kaden instances; covered by the
  conformance suite's `env_probe` case.
- `Glob`/`Grep` walk the tree in a worker thread and send a silent heartbeat
  (`progress("")`) from it at most every 0.5 s. The shell never sends heartbeats: its stall
  detection is meant to catch a command that stopped producing output.
- The conformance suite's `RecordingContext` is thread-safe like the runtime's context and
  counts heartbeats separately from visible progress.

## Running Kaden

Design of the config file: [Agent Note](notes/implemented/2026-11-02-instance-config-file.md).
User settings changed from a frontend (`update_settings`), saved to `settings.toml` and
applied over the config file: [Agent Note](notes/implemented/2026-11-07-settings-for-non-technical-users.md).

```
export ANTHROPIC_API_KEY=...
kaden [--config FILE] [--conversation ID] [--data-dir DIR]      # opens the desktop app
kaden --headless [--conversation ID]
    [--model MODEL_ID] [--maintenance-model MODEL_ID]
    [--permissions allow_workspace|allow_read_only|ask|deny]      # for a program
```

The `kaden` command (`[project.scripts]`, installed with `uv tool install --editable`) and
`python -m kaden` both run `kaden.__main__:main`. It opens the desktop app as a process of
its own (`python -m kaden.window` with the data directory, config file and conversation)
and returns; without a display it prints that there is none and that `kaden --headless`
runs it for another program, and exits 1. `--headless`, and a chosen frontend of the
terminal kind, run in the process itself. `kaden/__main__.py` builds an `InstanceConfig`. The modules come from a TOML file:
`--config FILE` (must exist), else `<data dir>/config.toml` (optional). Sections `[core]`,
`[memory]`, `[tools]`, `[frontend]`, each with `factory` and `options`; anything else is
refused at startup. A module left out gets its default: `kaden.core.anthropic:factory`,
`kaden.memory.sqlite:factory` (`<data dir>/memory.db`), `kaden.tools.shell_files:factory`
rooted at the current directory (only when no tools options are given), and
`kaden.frontends.desktop:factory`. The file is read with stdlib `tomllib`. `[runtime]` holds the
per-instance settings: `model`, `maintenance_model`, `permissions`, `system_prompt` or
`system_prompt_file`, and `[runtime.budget]` (the `Budget` fields, non-negative). Keeping the budget limit in the data directory, next to
the usage it limits, is the point: a flag alone would let each launch pick or forget the
limit. `--model`, `--maintenance-model` and `--permissions` override the file. The other
`RuntimeConfig` fields (retries, timeouts, ...) are tuning and stay at their defaults
until someone needs them. `--show-config` prints the effective config as TOML (`tomli-w`)
and exits. `--conversation` starts at once: it becomes the frontend options
`start = true` and `conversation_id`, merged over the configured ones; the desktop app is
handed it as the conversation to open first.
The data directory defaults to `$KADEN_DATA_DIR` or `~/.kaden`. The launcher checks no API keys: each Core refuses a
missing key at construction (the Anthropic Core accepts `ANTHROPIC_API_KEY` or
`ANTHROPIC_AUTH_TOKEN`).

Module startup errors are one line. `load_module` turns an `ImportError` or
`AttributeError` from resolving the factory path, a `ValueError` or `TypeError` from
calling the factory (missing key, unknown option), a malformed path and a factory that
returns the wrong kind into `ModuleLoadError("<factory>: <message>")`. For Core, Memory and
Tools that message becomes `Settings.startup_error` on the setup runtime (see Swappable
modules below); only when even the launcher's own frontend cannot be built does
`python -m kaden` print `kaden: <message>` to stderr and exit 1. Other exceptions keep
their traceback, since they are bugs rather than configuration mistakes.

`tests/test_e2e.py` is the end-to-end check. No API key was available, so it runs the real
Memory, Tools and runtime against the scripted Core: once through `run_instance` with the
config `python -m kaden` builds (Core and Frontend swapped for fakes). It checks the
SQLite transcript and the file the shell call wrote. The live Core suites
run with `KADEN_LIVE_TESTS=1` and a key.

## Desktop app

Design: [Agent Note](notes/implemented/2026-11-07-desktop-app.md).

| Choice | Value |
|---|---|
| App menu | `app_menu.install()` (Linux, from **Kaden → Add to App Menu**) writes `applications/io.github.k5031.kaden.desktop` and `icons/hicolor/scalable/apps/io.github.k5031.kaden.svg` under `$XDG_DATA_HOME` (default `~/.local/share`). `Exec` is this install's Python with `-m kaden.window`, since a menu has no terminal for `kaden`; the id is the window's desktop file name, so the window gets the icon. The action shows while the entry is missing or differs. Installing also runs `kbuildsycoca6` and sends KIconLoader's `iconChanged` signal when they exist: KDE keeps the entries and icons it has read, so without them a click runs the entry's old command |
| Entry points | `kaden-desktop` runs `kaden.window:main`; `kaden` hands over to `python -m kaden.window`; bundles run `python -m kaden_desktop`, which calls the same `main`, or `kaden.__main__.main` when started as `kaden` or `kd` (links the package adds) |
| Dependencies | The `desktop` extra (also in the dev group): `pyside6-essentials`, `pyside6-addons` (the voice's audio), `qasync`, `pygments`, `sherpa-onnx`, `numpy` |
| Default work folder | The current directory, for both launchers, when neither config file sets a root. The desktop app has the user choose each new chat's folder (`Controller.folder_picked`) |
| At launch | Opens `--conversation` (given by `kaden`), else the most recently updated conversation, or starts one when there is none |
| Welcome | Shown on the setup runtime when the chosen provider needs a key and no provider has one; it saves the provider, key, work folder and limit, then restarts |
| Markdown re-render | At most every 100 ms while streaming; at once on commit |
| History page | 100 messages, then "Load earlier messages" |
| Quit order | `setQuitOnLastWindowClosed(False)`: closing the window ends `Frontend.run`, the runtime shuts down, then `asyncio.run` returns |
| "Get a key" pages | `platform.claude.com`, `platform.deepseek.com/api_keys`, `platform.openai.com/api-keys` |
| Toolkit-free | `kaden.frontends.common`: permission labels, money formatting, `loosening()` |
| Look | Fusion style plus a palette and style sheet from `theme.py`; light or dark from `QStyleHints.colorScheme()`, followed live |
| Accent | Indigo `#5B5BD6` (light), `#8C8CF5` (dark); the rest neutral gray |
| Text sizes | Relative to the base text (15 px at 100%, `theme.BASE_PX`): small 0.85×, title 1.15×, heading 1.45× |
| Sidebar row | Title (the title, else the first message, else "New chat"), date (time today, "Yesterday", weekday within a week, else "3 Nov", plus the year when not this year), and the first message as a second line when the title is not it |
| Your messages | At most 75 % of the transcript's width |
| Message box | Grows with its text to 6 lines |
| Event loop | `loop.QtEventLoop`, qasync's `QEventLoop` with three fixes (below); `KADEN_TEST_LOOP=qasync` runs the tests on it |
| Garbage collection | Automatic collection is off while a `QtEventLoop` is open; every 100 ms (`GC_CHECK_S`) the loop collects the oldest generation whose count passed its threshold |

**qasync fixes (`QtEventLoop`).** Python's cyclic garbage collector runs in whichever thread
allocates. A QObject it frees in an `asyncio.to_thread` worker (one importing the provider's
SDK during `update_settings`, say) cannot stop its timers from there ("Timers cannot be
stopped from another thread"); the next one to fire hits freed memory and segfaults. Every
closed qasync loop leaves such a QObject in a cycle, and the app's own may too, so the loop
collects on the Qt thread instead. qasync's `run_until_complete` also processes Qt events
once more after the loop stops; a task woken there fails with "not the running loop" and
never resumes, so the next `run_until_complete` waiting on it hangs (fixture teardown in the
tests, `asyncio.run`'s shutdown in the app). `QtEventLoop.run_until_complete` leaves those
callbacks for the next run, as asyncio does. On Python 3.15, transports pass `context` to the
loop's `_add_reader` and `_add_writer`, which qasync's do not take, so every socket read
failed; `QtEventLoop` takes it and runs the callback in that context.

**PySide6 on Python 3.15.** With PySide6 6.12 loaded, reading a lazily imported module
attribute (PEP 810) from another module returns the `lazy_import` object instead of
resolving it. `asyncio.to_thread` reads `concurrent.futures.ThreadPoolExecutor`, so every
`to_thread` failed. `kaden.frontends.desktop` resolves that name when imported, before any
Qt import; `tests/conftest.py` imports it first for the same reason. Other lazily imported
stdlib names stay exposed on 3.15.

**qasync results (2026-11-11, qasync 0.28.0, PySide6 6.12.0).** With
`KADEN_TEST_LOOP=qasync`, the whole suite passes on Python 3.15.1, repeatedly and in one
run, on `QtEventLoop`. Bundles carry Python 3.13.

**Briefcase (0.4.7).** The bundle installs Kaden as a package (`requires =
["./[desktop]"]`: Briefcase takes a requirement for a local path only if it has a "/", and
only then builds a wheel of it for a build in Docker), not its sources: Kaden finds its modules through entry points, which
only package metadata carries (a bundle of sources lists no modules). The Linux system package for Arch builds and starts the desktop app
(2026-11-10, system Python 3.14.9): 114 MB as `.pkg.tar.zst`, 398 MB installed, of which
PySide6 is 244 MB. Not built yet: the Flatpak (needs `flatpak-builder`) and the macOS
`.dmg` (needs a Mac).

**Permission ceiling.** The runtime reads the inherited ceiling once, at construction and
before exporting its own policy, and `update_settings` clamps to that. Clamping to the
environment at update time would clamp to the runtime's own export, and permissions could
never be loosened in a running instance (`test_permissions_loosen_past_the_policy_this_runtime_exported`). At
shutdown the runtime puts back what it inherited: the export is for what it launches, and
the runtime that Restart now builds next in the same process would otherwise read it as
its ceiling and could not be loosened either.

## Desktop rendering and repainting

| Choice | Decision |
|---|---|
| A streaming reply | `AssistantBubble.parts`: while a reply streams, each run of whole blocks (up to the last blank line outside a code fence, `_whole_part_end`) is rendered once, in a `MarkdownView` of its own above the body; the body renders only what comes after, every 100 ms. Once the reply is final the parts go and the body renders all of it once, so selection, copy and numbering are those of the whole |
| A theme change | What is drawn in the theme's colours at render (syntax colours and inline code in `MarkdownView`, the copy, close and plan icons, the plan's spinner) is drawn again on the app's palette change, when it was painted with another palette (`widgets.restyled`); the spinner reads its colour as it paints |
| Repaints | `MainWindow._paint_icons` repaints only when what the icons show changed (theme, busy, microphone, speaker, maximized); in Continuous's view a change in its conversation updates only the note of what it does (`_show_duty`); filling the sidebar updates only the title (`_show_title`); a switch fills the sidebar once, on its `Reset` |
| The spinner | Turns only while it shows (its timer starts on show and stops on hide) |
| Pasted pictures | Saved one at a time (`MainWindow._pasting`), so each gets its own `pasted-N.png` |
| Window tasks | Run through the window's own controller (`self.home.command`): kept referenced, and a failure becomes a notice. `LifeSettingsDialog` takes that controller |
| Confirmations | One `confirm()`; a dangerous action's button has the destructive role (Cancel first, and the default) |

## Free in the work folder

Design: [Agent Note](notes/implemented/2026-11-10-free-in-the-work-folder.md).

| Choice | Value |
|---|---|
| Default policy | `auto` ([automatic permissions](#automatic-permissions)); saved or configured levels are kept |
| `Tools.confined` | Called for every module call before grants and policy; an exception is logged and counts as False. The answer goes to `execute` as `ToolContext.confined`, so a module holds the run to it without remembering calls |
| Grants | Tool name -> whether calls that reach outside are covered; a grant from a confined prompt covers confined calls only |
| File containment | `Path.resolve()` of the argument (default `.`) `is_relative_to` the resolved root, in a thread; a non-string path is not confined |
| Opening a confined call's file | From the root down, each folder and the file opened with `O_NOFOLLOW` relative to the one before (`dir_fd`); a link at any step is "leads outside the work folder". Chosen over comparing `os.fstat` with a `stat` of the path after the open, which a command swapping a folder for a link and back between the two can pass. Files opened `O_NONBLOCK`, then refused unless `S_ISREG`, then set blocking |
| Line endings | Read shows lines as Python's universal newlines give them (CR LF as LF). Edit reads with `newline=""`: a file whose every line ends CR LF is edited with LF (in the file and in `old_string`/`new_string`) and written back with CR LF; a file of mixed endings is edited byte for byte. NotebookEdit writes CR LF back when the notebook had it throughout |
| Boundary | None when the root is the home directory or one of its parents (so also `/`) |
| Sandbox | `sandbox.find()` once at construction: on macOS `sandbox-exec`, on Linux `bwrap`, probed by running `true` in it with a throwaway root and tmp, 5 s limit; any failure, or another system, leaves the shell unsandboxed. The command line is made for each command (`Sandbox.wrap`, in a thread), with the folder the conversation's shell is in as the process's working directory. Design: [Agent Note](notes/implemented/2026-11-26-a-sandbox-on-each-system.md) |
| Sandbox's tmp | A `kaden-sandbox-` folder from `tempfile.mkdtemp` per sandboxed command, given as `TMPDIR` and removed when the command ends. On Linux `/tmp` is a fresh tmpfs as well |
| Local sockets (Linux) | Every socket file under `/run` (its own file system, not `/run/user`, not unlistable folders), found per command, covered with `--ro-bind /dev/null`: `connect()` gets ECONNREFUSED |
| Seatbelt profile | `(deny default)`; `(allow file-read*)`, then `/Users`, `/private/var/root`, the home, `/private/tmp`, `/private/var/folders` denied, metadata of the folders on the way to the root, tmp and attached files allowed, the root and tmp read and written, attached files read; writes to `/dev/null`, `/dev/zero`, ttys and `/dev/fd`; the `sandbox.MACH_SERVICES` list; IP network in and out; unix sockets only mDNSResponder's (`Seatbelt.profile`) |
| Home in the sandbox | An empty tmpfs directory at the same path, so `$HOME` exists; what a command writes there is gone when it ends |
| Attached files in the sandbox | Each file the user attached (`ShellFilesTools.attach`) outside the workspace is readable at its own path (bubblewrap: `--ro-bind-try`, so one since deleted is skipped; Seatbelt: `file-read*` of its literal path): attaching is the user's act, and an agent sent a picture could not copy it, as `/home` and `/tmp` are hidden. Kept for the process's life, not across a restart |
| Shell start | `create_subprocess_exec(*Sandbox.wrap(["sh", "-c", command], ...))`; without a sandbox `sh -c command` |
| Step descriptions | [Agent Note](notes/implemented/2026-11-10-step-descriptions.md). `api.DESCRIPTION_SCHEMA`, declared by the tools that want it ([Agent Note](notes/implemented/2026-11-27-tools-declare-their-step-summary.md)); frontends split a call with `frontends.common.call_parts` (one text argument as it is, several as `name: value` lines) |
| Sandbox environment | `os.environ` without names matching `KEY\|TOKEN\|SECRET\|PASSW\|CREDENTIAL` (case ignored); unsandboxed commands get it unchanged |

## Changes for contract Draft 3 (autonomy into sessions)

Design: [Agent Note](notes/implemented/2026-11-07-autonomy-into-sessions.md).

| Choice | Value |
|---|---|
| `activate()` in IDLE | Allowed; it also drops a pending wake-up (a new cycle clears the wake set) |
| `start()` | Always leaves the agent IDLE. A resumed conversation has no wake set, so nothing runs before the user acts |
| Wake-up | When a cycle ends, the wake set is every outstanding operation; the agent is woken once all of them have terminated, or at once when results it has not seen exist and nothing is outstanding. A user-cancelled operation wakes it like any other outcome; Stop does not |
| `StartOptions` validation | `seed_from` with a `conversation_id`: ValueError, before anything is opened |
| Untitled display | `ConversationInfo.preview`: the Text of the first USER message, whitespace collapsed, 100 characters |
| `Memory.search` scope | `conversation_ids` (None = all). `recall(scope="all")` passes the open conversation plus every listed (not archived) conversation; `search_archive` without an id searches everything, archived included |
| Memory entry ids | Strings; SqliteMemory uses its integer row ids |

### SqliteMemory schema

- Version 10 (`PRAGMA user_version`; the archive alone, `SqliteArchive`, is at 3; both moved when the stored author `kadence` became `kaden`). A store
  of any other version is refused; Kaden carries no migration code. Erasing a row from `messages_fts` repeats the text it was
  indexed with, so `codec.searchable_text` must not change without a new schema version.
- `conversations`: `archived`, `seed_from`, `folder` columns.
- Long-term memory: `memory_entries` (id, source conversation or NULL for the user's,
  created_at) and `memory_revisions` (entry, text, created_at); the current text is the
  newest revision. `forgotten` holds SHA-256 fingerprints of
  forgotten texts (casefolded, whitespace collapsed); compaction drops a new entry whose
  fingerprint is there. A reworded fact is new to it.
- `summaries`: one row per conversation, its current summary; writing a new one deletes
  the old row in the same transaction (`_commit_summary`). Summaries are derived from
  messages the archive keeps, so they are working state, not part of the append-only
  archive.
- Seeding: while `seed_from` is set and the conversation has no summary row, `maintain`
  first runs the compaction prompt (light model) on the source's current summary and as
  many of its newest exchanges as fit, and stores the result as a summary with
  `through_seq` 0. Long-term lines in that output are ignored (the source already
  contributed them). Only the source's messages created before the conversation was are
  read; its summary is the current one, which covers later messages only if the source
  was compacted since. An empty source writes an empty summary row, so it is read once
  and what it gets later is not seeded. A source deleted while maintain runs is no seed
  (its `seed_from` is NULL by then). An empty result writes nothing and skips that
  maintain's compaction too, since a first summary from compaction would end seeding;
  the next maintain tries again.
- Titles: after seeding and compaction, a conversation with no title gets one from its
  first USER message with Text (2000 characters at most), light model, 20 output tokens,
  prompt `_TITLE_PROMPT`. The first non-empty line is kept, quotes, `#*_` and a trailing
  period stripped, 80 characters at most. A CoreError is logged and leaves the title
  empty (the turn goes on, no Failure event); BudgetExhausted propagates. The write is
  `UPDATE ... WHERE title IS NULL`, so a title the user set meanwhile wins.
- Erasure: `PRAGMA secure_delete = ON` on every connection. `delete_conversation` runs in
  one transaction: list the id in `erasing` (the no-delete trigger exempts listed
  conversations), remove each message from the contentless FTS index with the `delete`
  command and its original text, delete messages, summaries, long-term entries from it
  and their revisions, set `seed_from` to NULL where it pointed there, delete the row,
  then `optimize` the FTS index so no segment keeps the terms. `forget` deletes the
  revisions and the entry and records the fingerprints. Both then run
  `PRAGMA wal_checkpoint(TRUNCATE)`; a concurrent reader can keep it from completing,
  which is logged as a warning.

## Swappable modules

Design: [Agent Note](notes/implemented/2026-11-08-swappable-modules-in-app.md).

| Choice | Value |
|---|---|
| Built-in ids | `anthropic`, `deepseek`, `openai`; `sqlite`; `shell-files`; `desktop` |
| Display names | Claude, DeepSeek, OpenAI; Built-in memory (SQLite); Shell and files; Desktop app |
| Discovery | `importlib.metadata.entry_points` per group; ids read from metadata alone at launch, factories imported only for `settings()`. A failing import is logged at INFO and the entry left out |
| Desktop entry point | `kaden.frontends.desktop` imports `qasync` and `PySide6.QtWidgets` at the top, so it loads only with the `desktop` extra |
| `kaden.core.openai_compat:factory` | Not registered: the profiles are `deepseek_factory` and `openai_factory`. A config naming the generic factory has no id ("Custom (from config.toml)") |
| Saved choices | `[modules]` in `settings.toml`, only the kinds changed. |
| Options on a switch | A chosen module keeps `config.toml`'s options only when it is the module `config.toml` names; otherwise it gets none |
| Core change | Saves `model = ""` and `maintenance_model = ""` (the new Core's first, the main model), overriding `config.toml`. Models chosen while another Core waits apply live and are not saved |
| Restart | `RuntimeAPI.restart()` records a `Restart(frontend, frontend_kind, conversation_id)`, logs a `restart` decision and shuts down as on quit. `launch()` reloads the config and runs the next instance in the same process; the frontend gets `start = true` and the conversation unless the Memory changed |
| Terminal to window | After a terminal frontend's instance has shut down (the lock is released), `kaden` runs `python -m kaden.window --data-dir D [--config C] [--conversation ID]` with `start_new_session`, standard streams to `/dev/null`, and prints that Kaden continues in the desktop app |
| Display check | macOS and Windows always; elsewhere `DISPLAY` or `WAYLAND_DISPLAY` set |
| Frontend per launcher | The saved frontend when installed and of the launcher's kind, else `desktop` (the window launcher; Kaden has no terminal frontend of its own). `kaden` runs a saved frontend of the terminal kind in its terminal; otherwise it hands over at launch ("Kaden opens in the desktop app.") when a display is available, and without one prints that there is no display and that `kaden --headless` runs it for another program, and exits 1. An uninstalled saved frontend is not a startup error: settings show the saved id as not installed |
| Startup errors | An uninstalled Core, Memory or Tools id, or a `ModuleLoadError` from building them, runs the frontend on `SetupRuntime`. A frontend that fails to build is replaced by the launcher's own, which shows the error |
| Setup runtime | Saves module settings, budget, permissions and system prompt; refuses model choices (`ValueError`) and every other command (`RuntimeStateError` naming the error). It takes no lock and writes no event log: nothing runs, and another instance may hold the directory |
| Work folder | The Tools `root` option, settable when the configured Tools has one (the built-in shell-files Tools always does) |
| Frontend texts | `kaden.frontends.common`: `restart_notice`, `module_options`, `needs_restart`, `MODULE_LABELS` (the modules settings offer: no Memory) |

## Web tools

Design: [Agent Note](notes/implemented/2026-11-10-web-search.md).

| Choice | Value |
|---|---|
| Search | `ddgs` `DDGS(timeout=10).text(query, max_results)`, in a thread; default 8 results, at most 20; "no results" is an empty list, other ddgs errors an error result |
| Search retries | ddgs picks engines at random and reports "No results found" when those refuse, so the same query can fail and then work: an empty or failed search is tried 3 times in all, 1 s apart; after that, "Nothing found … after 3 tries", saying the engines may be refusing searches for a while (checked 2026-11-10: two common queries each failed about one time in three) |
| HTTP 403 | Said to be a site that refuses programs reading it, with a hint to use another source. Kaden does not disguise itself as a browser to get past such blocks |
| Download | `httpx2`, 30 s per request, at most 5 MB, at most 5 redirects followed by hand |
| Public addresses | Every hop's host is resolved; any address that is not `is_global` is refused. Names that do not resolve are an error result |
| Extraction | HTML: `trafilatura.extract(output_format="markdown", include_links, include_tables, with_metadata)` in a thread; text types and JSON (pretty-printed) as they are; anything else is an error |
| Result size | 30 000 characters per call, continued with `start` |
| Untrusted header | Every result starts with a line saying the content is from the web and its instructions are not the user's |
| Tool timeouts | `WebSearch` 60 s, `WebFetch` 120 s |

## Folders per conversation

Design: [Agent Note](notes/implemented/2026-11-10-folder-per-conversation.md).

| Choice | Value |
|---|---|
| Stored folder | The resolved absolute path; None only for conversations created with Tools that have a fixed folder |
| New conversation | `StartOptions.folder`, else the `seed_from` conversation's folder, else `Settings.work_folder` |
| Order in `start()` | Validate, find the conversation (resume) or pick the folder (new), `Tools.use_folder`, then create; a failed create moves the Tools back to the open conversation's folder |
| Missing folder | `ShellFilesTools.use_folder` creates it (`mkdir -p`); OSError makes `start()` raise ValueError naming the folder |
| Replacing an empty chat | Frontends start the new chat, then delete the empty one (only when it has no messages loaded) |
| Event log | The folder path is not logged |

## Automatic permissions

Design: [Agent Note](notes/implemented/2026-11-10-auto-permissions.md).

| Choice | Value |
|---|---|
| Order in `_gate` | DENY, grants, read-only (ALLOW_READ_ONLY), confined (ALLOW_WORKSPACE and AUTO), review (AUTO), then prompt or non-interactive refusal |
| Review request | Maintenance model, `REVIEW_PROMPT` (runtime/review.py) as system prompt, one USER message, no tools, `maintenance=True`, at most 1 024 output tokens |
| What it sees | Text of the messages the user wrote (`Message.from_user`; none of Kaden's notices) among the newest 200, newest first up to 16 000 characters; the work folder; tool name, description and arguments (cut at 4 000 characters) |
| Answer | First line `ALLOW` or `ASK` (case and `*`, `` ` ``, `.` ignored), second line the reason (300 characters); anything else asks |
| Protected paths | `review.protected` ([Agent Note](notes/implemented/2026-11-27-protected-paths.md)) on each string of the arguments whose name holds a place word (`path`, `file`, `dir`, `folder`, `cwd`, `uri`, `dst`, … ) or a command word (`command`, `cmd`), nested ones too, of calls that are not read-only: the data folder (absolute forms, and its path below the home folder as segments: `.kaden`, `.kaden/life`), then shell startup files and git's own files (`PROTECTED_PATHS` for a path, anything in `.git`; `PROTECTED_IN_COMMANDS` for a command, only `.git/config` and `.git/hooks`, so reads like `ls .git` are not counted). Such a call takes no shortcut in `_gate`; under auto the review decides it |
| Failures | Exhausted budget (checked first), any exception from the request: ask, with a reason saying Kaden could not check. No transient retries |
| Stop | The review is raced against Stop with the grace period; the call is then not started |
| Log | `permission_review` with call id and `allow`; the permission record says `by: "review"`. The reason is not logged |

## Memory notes

Design: [Agent Note](notes/implemented/2026-11-10-memory-notes.md). Tools offered by the Memory module through `kaden.memory.tools` (with recall), not by the runtime.

| Choice | Value |
|---|---|
| Store | Schema 7 (8 adds a conversation's life and plan): `notes` (note 1 is the profile, "About you", created with the store), `note_revisions` (title, description, text, writing conversation, time; the latest is current), `saved_exchanges` (per conversation, the last message the save pass read). `memory_entries`, `memory_revisions` and `forgotten` are gone |
| Titles | Unique with case ignored; saving by a title that exists writes that note and keeps its own title; leading `#` stripped from a title the save pass writes; the profile keeps its title and cannot be deleted |
| Prompt | Profile in full, then "title: description" of the other notes, most recently written first, within `long_term_fraction` (5 %) of the context budget |
| Tools | `read_note(title)`, `save_note(title, description, text)` (replaces the whole text; an empty description keeps the old one), `forget_note(title)`; a missing title answers ERROR listing the notes |
| Save pass | In `maintain` when the newest message is an answer calling no tool and comes after the last saved one; reads the messages since (the newest part when long), the profile, notes' texts within 5 % of the light model's window (most recently written first, stopping at the first that does not fit) and the rest as an index; writes `<save>` (whole text) and `<append>` changes; up to 1 024 output tokens; a CoreError is logged and the exchange is read again after the next answer |
| Runtime | `maintain` is called once more after a cycle's last answer, before IDLE; when it acted, `MemoriesChanged(saved)` names the notes whose latest revision is new (read with `notes()` before and after); the event log keeps their number, not their titles |
| Notice | Frontends show a non-empty `saved` as a line after the answer: "Memory updated: Trip, About you", with an "Open memory" link in the desktop app. Saves by the agent's tools show as steps, and the user's own edits are not announced |
| Concurrent edits | The save pass writes from notes as it read them before the light-model call. A `<save>` of a note whose current revision is no longer the one read (edited by the user or the agent's `save_note` meanwhile, or created under that title meanwhile) is skipped and logged; that edit stays. `<append>` adds to the current text, so it still applies |
| Agent deletion | `forget_note` erases the note with its revisions, when the user asks or the agent finds it no longer true. The user decided this on 2026-11-26; AGENTS.md's archive rule names notes as the exception. Messages are never erased by the agent |
| Compaction | Writes the summary only; facts are the save pass's |
| Checked | Against Claude Haiku 5.5 (2026-11-10): a self-introduction made a profile and a project note, small talk saved nothing, a later plan was appended to the project note; about one cent for three turns |

## Desktop zoom

| Choice | Value |
|---|---|
| Mechanism | The app font and every style-sheet font size scale by the zoom (`theme.set_zoom`), live; pixel sizes (padding, icons, radii) stay |
| Levels | 67, 80, 90, 100, 110, 125, 150, 175, 200 %, as in browsers |
| Keys | Zoom In: the platform's Zoom In (Ctrl++) and Ctrl+=; Zoom Out: Ctrl+−; Actual Size: Ctrl+0; all in a View menu |
| Stored | `<data dir>/desktop.json` (`{"zoom": 1.25, "theme": "light"}`), written once, when the window has closed (`DesktopFrontend.run`), so a change is lost if the app crashes; missing or unreadable means 100 % |
| Theme | View → Theme: System (follows the desktop's color scheme, the default), Light or Dark (`theme.set_theme`), stored beside the zoom. It is a view preference of this app, like the zoom, so it is in the View menu and `desktop.json`, not in the runtime's settings |
| Not a setting | A desktop-only view preference, so it stays out of `settings.toml` and the runtime |

## Step groups

Design: [Agent Note](notes/implemented/2026-11-10-step-descriptions.md).

| Choice | Value |
|---|---|
| Opened | Closed by default; the desktop app keeps a group open while its first step stays first |
| Live title | The latest step not yet ended, with its state (preparing, running, in the background) |
| Ended title | As Claude shows a finished run: the last step's sentence, and the problems counted by label beside it ("1 failed, 1 declined"). Drawn as Claude draws it: the words, muted, then a chevron (`ThinkingLine`, as for thinking); unfolded, each step is a rounded row (`#tool`, the card colour) with a small arrow on its left, the whole row opening its details |
| Step text | As Claude shows a step: the model's `step_summary` where the tool declares one (Bash, the Bilibili tools), else `common.tool_sentence`: a phrase per Claude Code tool from its arguments ("Read main.py", "Search for “TODO” in src", "Fetch docs.python.org"), the tool's phrase alone while its input still arrives ("Run a command"), the tool's title (`ToolSpec.title`, a tool server's own) for any other that has one, "Server: tool name" for a tool server's tool without one, "Use <name>" for any other; past tense once done. The raw input stays in the tooltip and the details. Permission and proposal cards use the same words |
| Proportions | As Claude's app: text at 15 px at 100% zoom (`theme.BASE_PX`, whatever the desktop's font size), the sidebar 288 px at 100%, the conversation and message box 780 px wide, the message box padded 12 px above and below its text |
| Cancel | The desktop group line has Cancel for its live step; each step keeps its own |
| Step rows | No box; "done" is not shown, its duration is in the tooltip; other states stay visible |

## Waiting steps

| Choice | Value |
|---|---|
| Desktop | A committed call is `Phase.WAITING` ("waiting", no Cancel) until `ToolStarted`: it may wait for a permission prompt, the automatic review, or earlier calls |

## MCP servers

Design: [Agent Note](notes/implemented/2026-11-10-module-provided-tools.md).

| Choice | Value |
|---|---|
| Library | The official `mcp` SDK (2.3), `mcp.Client`, one task per server holding its connection |
| Tool names | `mcp__<server>__<tool>`, characters outside `[A-Za-z0-9_-]` replaced by `_`, cut at 64 |
| read_only | Never: a server's `readOnlyHint` is its own claim, so its calls are reviewed or asked about under every level (the user's decision, 2026-11-26) |
| Confined | Never: AUTO reviews each call, stricter levels ask |
| Environment | The SDK's safe default (HOME, LOGNAME, PATH, SHELL, USER), the server's own variables, `PERMISSION_CEILING_ENV` and `PROTECTED_FOLDERS_ENV` |
| Close | `aclose` stops every server, waits 5 s, cancels the rest and waits for them to end |
| Start | At the first `list_tools` (as a conversation opens: in the background, or before the conversation is open when it has watches), all servers at once, 30 s to start and list; every listing meanwhile waits for that one start, which a cancelled listing does not end; one that fails is left out and logged |
| Settings | `[[mcp_servers]]` in settings.toml: name, command or url, env as variable names; values in the keychain as `mcp/<server>/<NAME>`; removed variables are erased from it |
| Apply | Like modules: at the next start (Restart now), as one more Tools module after the configured one |
| Names | 1 to 32 of letters, digits, `-` and `_`, unique |
| Results | Text content joined; other content shown as `[<type> content]`; structured content as JSON when there is no text |

## The provider's web search

Design: [Agent Note](notes/implemented/2026-11-10-provider-web-search.md).

| Choice | Value |
|---|---|
| Tool | `web_search_20260209` for models with dynamic filtering (Opus 5.5, 5, 4.8–4.6, Fable 5, Sonnet 5.5, 5, 4.6, by id prefix), else `web_search_20250305`; `max_uses` 5 per request |
| Cost | $0.01 per search (`usage.server_tool_use.web_search_requests`) as `Usage.extra_cost_usd`; the results are read as input tokens on top |
| Steps | One `ServerToolUse` per web search or web fetch, placed where its result came (the first where the turn's server blocks begin); the code dynamic filtering runs is not shown (unless a turn has nothing else) |
| Replay | A turn's server blocks, all of them in the order they came (they refer to each other), with the thinking and text between them, ride on its first shown use (placed where they begin) and are sent back unchanged, to the same model, while web search is on, and only after the user's latest message; earlier turns' searches go as text (summary and found pages). Checked against Opus 5.5 (2026-11-10): a second turn replaying the whole set cost about three times the first |
| Estimate | Thinking and text kept among the server blocks are counted twice by the token estimate (as their blocks and inside the server blocks' state), though sent once: the estimate errs high by their length, which only moves compaction a little earlier; the runtime does not read a Core's state to avoid it |
| Fallbacks | A 400 saying web search is not enabled switches it off for the instance (Kaden's own search is offered again); a 400 that names a server tool block (its `srvtoolu_` id or its block type, `anthropic._SERVER_BLOCK_ERROR`), in a request that replays some, resends them as text, and those uses go as text in every later request too (`AnthropicCore._refused`, by their ids), so the refusal is paid for once. Any other 400 is reported from the request that got it. The request that goes again is the same one, built by the one function that builds every body (`AnthropicCore._body`): a paused turn sent back to be continued goes again with what it had, and the answer's reasoning is bound to the body that was sent |
| Paused turns | `pause_turn` is continued in the same request, up to 5 times, the turn's blocks sent back as the assistant's. Still paused after that, the turn ends with stop MAX_TOKENS (cut short); a server tool use still waiting for its result is a failed step ("Not finished") without state, so it goes back as text, and it is not among the blocks replayed |
| Search off | The request's WebSearch is the Core's when search was on as it started (`AnthropicCore.stream`, `ours`). Once a 400 switches search off, the retry leaves the Core's WebSearch out (nothing runs it), and `tools` with it when nothing is left; a later request's WebSearch is Kaden's own and goes as a client tool. The 400s are told apart by the error body's message, as the API has no code for either |
| Sources | The cited pages, deduplicated in citing order, as a final "Sources:" line of links |

## One window per data folder

| Choice | Value |
|---|---|
| Asking | A desktop app that finds the data folder in use (`Settings.already_open`) connects to the open app's Unix socket and sends `show`; the open app brings its window forward (show normal, raise, activate) and the second exits without a window. Asyncio sockets, so it works on any loop |
| Socket | `$XDG_RUNTIME_DIR` (else the temp folder) `/kaden-<first 16 hex of SHA-256 of the data folder's path>.sock`, short enough for a socket name; a stale one is replaced when the next app listens, and it is removed on close |
| No answer | Another Kaden has the folder, such as a headless one (or a crash left it locked): one message, "Kaden is already running without a window…", then the app closes |
| Raising | Desktops may refuse to give a background program's window focus (KDE's focus-stealing prevention); the window then asks for attention in the task bar instead |

## A conversation's plan and life

Design: [Agent Note](notes/implemented/2026-11-28-no-duty-mode.md) (the plan, reminders),
[Continuous](notes/implemented/2026-11-23-continuous.md) (a life).

| Choice | Value |
|---|---|
| Intervals | `Duty.min_interval_s` (default 1 minute) is the soonest a life wakes after a turn, `max_interval_s` (default 1 hour) the longest it may rest; Continuous sets its own pace |
| Spending limit | None is needed: a life runs without `Budget.max_cost_usd`; its own time is held to `max_cost_usd_per_day` |
| Prompt | Appended to the system prompt after Memory's addendum: "## Your life" (instruction, what wakes it) and "## Plan" (one line per item: status, text, due in local ISO time, note). A plan without a life is shown alone. The current time is not in the system prompt, which would change every request and void the provider's prompt cache; every user message carries its sending time (Message times) and TodoWrite's result gives the time |
| Notice | A cycle the runtime starts by itself begins with a USER notice: "Your life: <instruction>" when a life is turned on, "A step in your plan is due.", "Background work finished.", "A new day began.", after a rest "<span> passed; it is HH:MM." (else "Time has passed."); without a life, a due step's reminder, and otherwise the cause followed by "Carry on, or end your turn if there is nothing to do now." The event log keeps the cause ("duty: <cause>"). User-message cycles get none |
| TodoWrite | Replaces the whole list; `due` is ISO 8601 (local time without an offset) or `due_in_minutes`; an unreadable time, or one more than a hundred years ahead, is an ERROR result and changes nothing. Offered with every open conversation; `Rest` only with a life |
| Schedule | Computed when a cycle ends idle (an asyncio timer); cancelled by any cycle request, Stop, start, failure, ending the life and shutdown; recomputed when the life or plan changes while idle |
| Events | `ConversationUpdated` for life and plan changes; `DutyScheduled(wake_at)` and `RuntimeSnapshot.duty_wake_at` for the next wake; the event log records `duty`, `plan` (item count), `duty_scheduled` (delay, cause), `duty_held`, `duty_asleep` and each `cycle_requested` reason ("duty: <cause>") |
| Store | SQLite schema 8: `conversations.duty` (JSON or NULL) and `conversations.plan` (JSON list, default `[]`) |
| The word | The user sees "life" (Continuous) and "plan"/"reminder"; the code and the API keep the names `Duty`, `DutyKind`, `set_duty`, `save_duty`, `DutyScheduled`, so the stored and wire forms stay the same |

## Message times

| Choice | Value |
|---|---|
| Stamp | In the request only, the first text block of every USER message (the user's and Kaden's notices) starts with `[Sent Wed 2026-11-11 10:15 +01:00] `: its `created_at` in local time with weekday and offset; one Kaden wrote continues with `[Runtime notice] `. Tool results and operation results are not stamped |
| Why there | Computed from the stored time, so a message reads the same in every request and the provider's prompt cache holds; the transcript, the frontends and search keep the text as typed |

## Headless frontend

Design: [Agent Note](notes/implemented/2026-11-11-headless-units.md).

| Choice | Value |
|---|---|
| Launch | `kaden --headless` replaces the frontend for that run with `kaden.frontends.headless:factory`, whatever is saved, and never hands over to the window; it is not an installed frontend, so Settings never offers it. `--conversation` becomes the frontend options `start`, `conversation_id`; without it the client starts one with `start` |
| Channel | JSON-RPC 2.0, one message per line, lines up to 16 MiB. At start the frontend keeps duplicates of fds 0 and 1 for the channel, points fd 0 at /dev/null and fd 1 at stderr, and sets `sys.stdout` to stderr |
| Requests | Every RuntimeAPI command but `events` and `restart`, read from the protocol itself (`kaden.frontends.wire.COMMANDS`); parameters are decoded by the command's type hints (`command_args`), `start` also takes its options as the request's params. Each handled in its own task, so a slow one blocks nothing; a request without an id gets no reply. Errors: -32700 bad JSON, -32600 not a request, -32601 unknown method, -32602 missing, unexpected or unreadable parameters (a value no enum has, such as a duty `kind` other than "life"; only while they are bound and decoded: a TypeError inside a command is -32000 and logged), -32000 refused by the runtime (`data.type` the exception's class). A line over the limit: -32600 without an id, and the line is read to its end and dropped |
| Encoding | Dataclasses as objects with `"type": <class name>` and their fields; enums as values; tuples as lists. Requests take plain JSON (a life and plan items as objects without `type`) |
| Events | Every envelope, deltas included, as an `event` notification with `seq` |
| Start-up | With `start` the frontend starts that conversation; a failure is written to stderr as `kaden: <message>` and the unit quits |
| Prompts | `interactive_permissions=True`: prompts wait for `respond` like a person's |
| Quit | stdin closing: requests already read are answered, then `run()` returns |

## Sub-sessions

Design: [Agent Note](notes/implemented/2026-11-11-sub-sessions.md).

| Choice | Value |
|---|---|
| Tools | `Agent(description, prompt, run_in_background=False, budget_usd=None, isolation=None)` (foreground: waited for until it ends, with no timeout, unless `run_in_background`; `description` is its title, `prompt` its task), `SendMessage(to, message)` (no timeout). A child is stopped with `TaskStop` on the call that waits for it. Not confined: reviewed under AUTO, asked under stricter policies |
| Child | `<sys.executable> -m kaden --headless --data-dir <data>/sessions/<first 8 of conversation id>-<n>`, cwd the work folder; config.toml copied, settings.toml copied with `budget = {max_cost_usd = budget_usd}` and `work_folder`; then `start`, attach, and `send` with the task. Reopening: the same with `--conversation <its conversation>` |
| Ids | "1", "2", … per data folder, continuing after the highest recorded and after the last given (`<data>/sessions/issued.json`, the id as JSON), so a deleted child's id, and the name of a worktree and branch it kept, is never given again. `SendMessage(to)` finds only the calling conversation's children (archived ones too) |
| Starting a child | One helper for both children the app hosts (`kaden.frontends.child`, also Continuous): the command line, the 10 s stop grace, the copied config.toml, settings.toml written whole (temporary file and `os.replace`); a settings file that does not read fails the start (a sub-session "failed", Continuous's start refused). A child that refuses `start` or the task (`RuntimeStateError`, or its channel closing) is failed and its process closed. Starting one again and attaching runs under a lock per sub-session (`SubSession.spawning`): concurrent opens share one process; `open` marks a done child it started again "idle" (a stopped or failed one keeps its state, and Resume) only while nothing else set it working |
| Record | `session.json` in the child's folder: id, task, parent conversation, state, report, failure, spent, child conversation; written off the loop when it changes. At start a record whose state was running becomes "stopped"; a record with other fields (the goal-era `question`, `has_goal`) is skipped as unreadable, with a warning, not migrated |
| States | starting, working (also while it is idle with background work running or a message queued), idle (reopened with nothing to do), done (its turn ended with nothing left running; the report is the text of its last reply since the last message from the user or the parent, whole: `SubSession.final_reply`; Kaden's notices in between, such as a send-back, do not end it. A background call that ends while the child is idle says `StateChanged(idle, 0)` before its result is appended; the remote snapshot lists the call until its `ToolFinished`, which follows the result, so the child is not done in between), failed, stopped (also when its runtime is STOPPED); "Needs your approval" while a prompt is open |
| Where a child's state comes from | While its process runs, the manager holds a `RemoteRuntime` for it (`SubSession.live`) and reads its snapshot: the open prompts and the agent's state directly, and on each `StateChanged` the state word and, once done, its report, which the record keeps for when it is not running. The manager itself decides only "stopped" (the user, the agent or a cancelled call stopped it), "failed" when the process ends by itself, and the word it sets on asking for work ("working" on a message, or after Resume's `activate` unless its events said so first; "idle" on reopening a done one). Closing a child detaches the manager from its channel first, so nothing it still says is heard |
| Prompts | Wait for the user, who answers them where the child is open; the parent's call keeps waiting |
| Process | Closed (stdin, then killed after 10 s) when it is done or stopped and no view has it open; cancelling the call (`TaskStop`), Stop and closing the manager stop it |
| Proxy | `kaden.frontends.remote.RemoteRuntime` over `Channel` (JSON-RPC client): every RuntimeAPI command but `restart`, each method made from the protocol (same signature, a request of that name, the reply decoded by its return type); `snapshot()` is read once and kept current from the events (state, conversation, model, usage, the next wake, operations, prompts, queue), those read while `attach()` waited included; `on_event` hears each event after the snapshot has it. A reply to a cancelled request is ignored. `kaden.frontends.wire` encodes and decodes the values |
| Desktop | The sidebar groups conversations by folder (a heading with the folder's name, folded by a click; "Other" for none) and lists each conversation's children under it (indented, a tree line, a dot while working, the summary as second line). Clicking a child shows it in the conversation area through its own Controller and model; clicking a conversation returns. Memory, tool servers and settings stay the window's own |
| Initiation | The agent starts a child when the user asked for the work, and proposes `Agent` (Proposals) when it is its own idea |
| Notices | When a child's state leaves "working" on a `StateChanged` and no parent call waits on it: `Sub-session <id> (<name>) ended a turn: <state>.` with its last reply (first 400 characters). Kept per parent conversation; the frontend writes them with `notify` to its own runtime when that conversation is open (on each change, and after opening a conversation) |
| Resume | No automatic resume after a restart (SPEC 11). `resume`: start it again on its conversation, then `activate`; offered when it is stopped or failed. `SendMessage` starts a stopped child again too |
| Limits | Optional. The user's limit per sub-session (none by default) is kept by the manager in `<data>/sessions/limit.json`, not in the runtime's settings; `Agent` gives `budget_usd`, capped by the user's limit when there is one, and its description states that limit. The desktop app has a Settings field for it. A child's own limit changes with `set_child_limit`: through the child's `update_settings` while it runs, else in its settings.toml; raising or removing either is confirmed (`common.raises_limit`); empty or 0 means none. Rows show "$spent", and "of $limit" with one |
| Deletion | Deleting a conversation deletes its sub-sessions after it (`SubSessions.delete_of`), archived ones included; the confirmation names them. Archiving the parent hides them, since they are listed only under it |
| Row actions | Rename (`title`, empty shows the task), Archive (hidden unless Show archived), Delete (stop, then the folder is erased after confirmation). Recorded in `session.json` with `budget_usd` |
| Worktrees | `isolation: "worktree"` ([Agent Note](notes/implemented/2026-11-22-subagents-in-worktrees.md)): `git worktree add <repository>/.claude/worktrees/agent-<first 8 of conversation id>-<n>` on a new branch `worktree-agent-<…>-<n>` from `HEAD`; the child's work folder is the conversation folder's place inside it. Refused (an error, no sub-session) with no conversation folder, outside a repository, or in one without a commit. "Changed": `git status --porcelain` shows anything (untracked files included) or a commit past the start. Tidied when its process has quit (after done, failed, stopped, or a view's release) and on delete: unchanged → `git worktree remove --force` and `git branch -D`; changed → kept. A foreground or background call that ends waits for that before its result, which names the path and branch, or says it was removed. Starting its process again (open, resume, `SendMessage`) adds a missing worktree on the same branch name from the same base. Adding, removing and starting a child's process run under one lock per manager. Recorded in `session.json` as `worktree` (repo, path, branch, base, folder). Adding one puts `/.claude/worktrees/` in the repository's `info/exclude` (local, never committed), so that the parent's `git add -A` leaves the worktrees out |

## Proposals

Design: [Agent Note](notes/implemented/2026-11-11-proposals.md).

| Choice | Value |
|---|---|
| Tool | `propose(call: {name, arguments}, reason)`, a runtime built-in (`BUILTIN_TOOLS`), offered in every request. Only a Tools module's or the frontend's tool can be proposed; another name is an ERROR result saying it is not a tool, or needs no approval. The proposed call is validated like a call |
| Operation | The proposal is an operation with the `propose` call's id, backgrounded at once (no initial wait), spec timeout none while it waits. The proposed call gets the id `<propose id>-call`. On approval its tool's own timeout (else the default) applies |
| Approval | The click is the permission: no gate, logged as a `permission` decision `by="proposal"`. A tool call is reserved from the budget only on approval. Waiting is not activity for the duration budget |
| Wake-up | Pending proposals are left out of the wake set; a finished proposal operation sets a flag that wakes an idle agent, cleared when the next request is built |
| Withdrawn | `TaskStop` on its id, Stop, and `start()` (switching conversations) cancel pending proposals: `PromptClosed(id, None)`. Pending proposals are not in `RuntimeSnapshot.operations` nor counted in `StateChanged.pending_ops` |
| Results | Approved: the call's own output. Dismissed: "The user dismissed the proposal; it did not run." (OK status) |
| Cards | Desktop: `ProposalCard` with the permission cards ("Kaden proposes: <step sentence>", the reason, other arguments; a `Agent` shows its task), Approve and Dismiss |

## Runtime notices

| Choice | Value |
|---|---|
| Who wrote it | `Message.author`: `Author.USER`, or `Author.KADEN` for every USER message Kaden writes for the model: the activation notice, a life's wake-up, a due step, the voice notices, background outcomes, and a sub-session's report to its parent, which a frontend writes with `RuntimeAPI.notify`. The stored text is what was written, with no marker |
| One test | `Message.from_user` (USER role and `Author.USER`) is what every reader uses: the permission review's "what the user asked", Memory's title and preview (`_FROM_USER` in SQL), the voice lookup, the frontends (`common.notice_of`) |
| For the model | In a request the runtime starts a notice with `[Runtime notice] ` after the time stamp (`config.NOTICE_MARK`, added in `orchestrator._stamped`); a hidden one with `[Runtime notice] (The user does not see this notice.) `. Memory's summarizing transcript names its writer "notice from Kaden, not the user"; `recall` lines say `notice` in place of the role |
| Shown | Frontends show such a message as a notice line (desktop `NoticeItem`), never as the user's bubble; a long one (several lines or over 160 characters, such as a life's instruction when it is turned on) is folded to its first line, with Show all; a queued one (`MessageQueued.author`) is shown once it is committed. A conversation in which the user wrote nothing yet has no preview and is labelled "New chat" until it gets a title |
| Not shown | A notice that only tells the model how to behave (the voice notices) has `Message.hidden`. Frontends show nothing for it; it is in the archive and in every request like any other message |
| Stored | Columns `author`, `hidden`, `spoken` of `messages` |

## Desktop look (a calmer, more professional window)

| Choice | Value |
|---|---|
| Column | The conversation, the prompt cards and the message box sit in a centred column at most 780 px wide (`COLUMN_WIDTH`) |
| Bubbles | Your messages are a neutral bubble (`Palette.bubble`) with ordinary text, not the accent colour; a queued one is outlined |
| Sidebar | A ☰ menu button and a plain "+ New chat" row on top; conversation and sub-session rows are one line in normal weight (preview, date and state on hover); the open one is a subtle grey (`Palette.select`) |
| Menus | No menu bar. ☰ holds the conversation's actions (New chat, Stop, View with zoom and Show archived conversations, Model); the profile chip at the bottom left (a letter avatar and the OS user name, `getpass.getuser()`, with spending in its header) holds the app's (Settings, Memory, Tool servers, Add to App Menu, Help → the docs, Quit), and the gear beside it opens Settings. Every action is also the window's, so shortcuts work with the menu closed; Settings is Ctrl+, explicitly (Qt's Preferences key is unbound on Linux) |
| Replies | List items have no top margin, only `LIST_ITEM_SPACING` below |
| Header | The conversation's title only; its folder is the title's tooltip. No spending in view: it heads the profile menu, Settings shows it, and one warning notice appears when spending passes 80% of the limit (`LIMIT_WARNING`). Choosing another folder is ☰ → New chat in another folder… |
| Title bar | None from the desktop (`FramelessWindowHint`): the header and the sidebar's top row are `DragArea`s (drag moves the window through the window system, `startSystemMove`; double-click maximizes or restores), the header ends with minimize, maximize/restore and close, each 38 x 28 px (`WINDOW_BUTTON`; filling the header's height made them look oversized) at its top and right edges so a cursor pushed into the corner of a maximized window lands on close, and `WindowEdges` resizes from a 6 px border (`startSystemResize`) with the matching cursor. The desktop's own title-bar menu is gone |
| Rounded corners | The main window and Settings have 12 px rounded corners and a hairline outline (`RoundedCorners`), square while maximized or full screen. The window is translucent; an overlay on top of everything, transparent to the mouse, clears what is outside the outline, so no widget underneath has to know about the corners, and the window's background is filled for it since a translucent window gets none. A window mask was not used: its edge is not smoothed. Needs a compositing desktop; a window tiled to half the screen keeps its corners, as Qt does not report tiling |
| Window shadow | The same overlay keeps a 24 px margin around the window's contents (`RoundedCorners.SHADOW`, the window's contents margins) and paints a shadow in it: black, 4 px below the frame, fading with the square of the distance, 8% at the frame by day and 22% by night. Painted by the app because a window without a title bar gets no shadow from the desktop. The frame is the window's `contentsRect`: `WindowEdges` resizes within 6 px inside it and 8 px outside it. The rest of the shadow takes clicks and does nothing with them: a window mask that would pass them on (on Wayland, the input region) was tried and taken out, since at a display scale of 150% it left part of the window itself deaf to clicks. No margin while maximized or full screen. Costs: the desktop counts the margin as part of the window, so snapping and a tiled window are off by 24 px, and a window's size includes it |
| Window shadow | None. A frameless window on Wayland gets no shadow from the desktop; an app that has one draws it into an invisible margin around its window and tells the compositor which rectangle is the window (`xdg_surface.set_window_geometry`), so that snapping and placement ignore the margin. Qt keeps that call in its private C++ interface, which PySide6 does not expose, and KDE's own shadow API (KWindowSystem) has no Python binding here. Without it a margin would offset the window when snapped or placed. The hairline outline separates the window from what is behind it |
| Scroll bars at the edge | A scroll bar takes clicks across its whole width, its outermost pixel included (the gap beside the handle is a transparent border of the handle, not a margin of the bar), so it can be hit with the pointer at the edge of the screen. Where one lies along the window's edge, only the outermost 2 px resize instead of 6 (`WindowEdges.OVER_SCROLL_BAR`) |
| Message box | A filled box (`Palette.composer`), two lines high at least, the send or stop button plain at its bottom right; focus only lightens the border. Under it: the status only when it says something other than "Ready", and the model's name (from `list_models`), which opens the Model menu |
| Permission picker | Under the message box, before the model: the policy in a word (`PERMISSION_SHORT`), opening the five choices; a looser one is confirmed, a stricter one applies at once (`update_settings`). It follows the window's own runtime, also while a sub-session is shown |
| Settings window | Frameless, its own title and ✕ (dragged by its title row); sections down the left (General, Spending, Permissions, Instructions, Memory, Tool servers) and rows with a name and a one-line explanation on the left and the control on the right. Memory and Tool servers are the old dialogs embedded as pages; the profile menu's Memory and Tool servers open Settings at them. Save and Cancel stay |
| Status | No status line under the message box: Stop is the send button, working sub-sessions are dots in the sidebar, notices say the rest. The next reminder ("Reminder at …"), or in Continuous's view what the life does, is a small note beside the title |
| Start | The window opens maximized |
| Reply footer | Under each finished reply, shown while the pointer is on it and keeping its space when hidden: a copy button (the reply's Markdown; a tick for 1.5 s) and its age (`relative_age`: just now, N minutes or hours ago, then the date) |
| Sidebar tooltips | Only the full title: a conversation's title or whole first message, a sub-session's title or its brief's first line |
| Project headings | The name, then a drawn chevron: down when open, right when folded |
| Turn gap | Your message has 6 px more above it and 18 px more below it than the 12 px between items, so a new turn reads as one and Kaden's reply starts clearly apart from it (about 38 px, as in Claude) |
| Reply text | Lines at 118% of the font's line spacing (about 1.6 em; 145% made them nearly double-spaced); inline code at 0.88 of the text size on the selection colour; a fenced code block at that size in one box of the selection colour with 10 px padding and corners of 8 px radius (a `QTextFrame` around its lines, which Qt makes paragraphs of; the view paints the box itself, since a frame's own background is square), its lines at normal height without space between them, long lines wrapped. A 22 px header in the box names the block's language at the left and has a copy button at the right, a child of the view placed over the frame, which shows a tick for 1.5 s. Code blocks that follow each other get an HTML comment between them before Qt reads the Markdown (`_code_blocks_apart`), since Qt makes one run of lines of them |
| Syntax colours | Pygments (`desktop/syntax.py`), for a code block whose fence names a language Pygments knows; no guessing for the others. Six colours per theme: keyword, string, comment, number, name (functions, classes, decorators, attributes) and builtin; everything else keeps the text colour |
| Sidebar look | Rows and New chat at 0.92 of the base text (the header title at 1.0, semi-bold), so the list reads lighter at any system size; the menu and New chat use drawn line icons like the window controls; sub-sessions hang on one thin guide line instead of a corner each; a working sub-session's dot has its own room, clear of the title |
| Folder picker | In the middle of a new chat, under its greeting, as an outlined button with the folder icon; only before its first message (and only when the Tools take a folder): the folder's name; its menu lists that folder (checked) and those of recent conversations, newest first, six in all, then Choose folder…. Picking one starts the new chat there |
| Sidebar | Starts 288 px wide at 100% zoom (as Claude's), scaled with it, and rescaled by each zoom change in proportion (a width the user dragged to included); the divider can be dragged, and the width is not saved |

## Compaction lines

| Choice | Decision |
|---|---|
| Signal | Memory marks its summarizing Completion call with `compacting=True`; the runtime emits `CompactionStarted` on it and `CompactionFinished` when maintain returns. `MaintenanceStarted` alone could not be used: upkeep runs after every answer. |
| "Compacted" | maintain succeeded and returned True, and the compacting call's output was not empty. A summary Memory then rejects while saving something else counts as compacted; that needs a reply that parses to nothing, and it only mislabels one line. |
| Look | A notice line in the transcript, "Compacting conversation…", changed to "Conversation compacted", or removed when nothing was compacted. Lines are not kept across restarts. |

## Wrap-up card

| Choice | Decision |
|---|---|
| Limits | `question` (required) up to 100 characters, one to three options (required) with labels up to 40, optional `recap` up to 400; at most one option carries a call |
| Withdrawal | A user message cancels a pending card proposal before it is appended; the cancelled outcome goes in first and no wake-up of its own follows |
| Close | ✕ clears the card in that window's model only; nothing is saved, so reopening shows it again |
| Stale action | After a restart the card proposal is gone; its option then sends its label as a message |
| Clearing | Only a message the user wrote (sent or queued) clears the card; background results and runtime notices leave it up. A closed card stays closed when the history is reloaded in that window |
| Options | Told apart by position: labels need not be unique |

## New chats

| Choice | Decision |
|---|---|
| Models at start | `start` asks the Core for its models only the first time, or when the chosen model is not in the list it has: a provider round trip on every switch was felt as lag |
| Switching in the desktop app | `Controller` returns from opening a conversation once it is loaded into the model, so the sidebar refreshed after it highlights the new one |
| Choosing a row | `ConversationList.chosen`: a click (itemClicked) or Enter. itemActivated is not used: where a single click activates (KDE), it comes with the click |
| Last click wins | What the window shows or is opening is one value (`Target`), written only by `MainWindow._open`: a sub-session, the window's own conversation, or the conversation being opened there. The sidebar highlights it, and a request is the newest while it is still that value; only the newest is shown when done. The sub-session on screen and what it gives back is `Viewing`, from which the shown controller and model are read. A sub-session that finishes opening after a newer request is released. The controller runs conversation switches one after another, so the last asked for stays open The controller makes only the newest switch asked for (`Controller._wanted`): one that another took the place of before its turn is not made, so clicks in a row load the last conversation and not each. A click on the conversation that is open, while another is still opening, is a request like any other: it is opened again after it, so it is what stays |
| Two listeners | The window's own conversation model is heard for as long as the window is (`MainWindow._home_changed`): the list of conversations, memory, and whether another chat can start follow it whatever the conversation area shows. The conversation area follows the model it shows (`_pane_changed`): the same one, or a sub-session's. Whether a chat can start or open is asked of the window's own controller, where it would start. One callback slot moved between the two models dropped a rename made while a sub-session showed, and enabled New chat by the sub-session's state |
| What a saved setting may be | `runtime/settings.py` has the one test of a budget's limit (`bad_limit`) and the one function that turns a `SettingsUpdate` into what the file keeps of the budget, the permissions and the system prompt (`runtime_changes`). The running runtime, the setup runtime and the launcher's reader all use them, so what is saved is what the next start accepts. Before, only the reader knew what was valid: a negative or fractional limit was saved and stopped every later start |
| Search as git sees it | `Glob` and `Grep` leave out what .gitignore files do: those of the folders from the workspace down to where the walk starts, and each folder's own on the way, with `pathspec` (a dependency added with the user's yes, 2026-11-22). A pattern is matched from its own file's folder; a negation does not reach across files. `Grep` takes `ignore_case`, `context` (lines around each match, `path-n-` as ripgrep writes them, `--` between groups) and `output` (`content`, `files`, `count`) |
| Claude Code's tool shapes | `Read` numbers lines as `cat -n` (`f"{n:>6}\t{line}"`), office files too; a PDF's text and a picture are not numbered. `Glob` takes a glob over the path relative to `path`: `*` and `?` stay in one folder, `**/` any number, `[...]` a set, `{a,b}` either; newest change first, sorted over all matches before the first 1 000 are kept; a pattern without `/` matches only the top folder. `Grep`'s `glob` still filters file names, as ripgrep's does for a pattern without `/` |
| Notebooks | `Read` shows a `.ipynb` by cells (`offset`, `limit` count cells), as many as fit in 100 000 characters, with `[N more cells; use offset K to read on]`, each as `<cell id="…">`, `<cell_type>` unless code, the source, `[output] …` per output (a stream's text, a result's text/plain, an error's name, `[image]`), `</cell id="…">`; a cell without an id is `cell-N` by its place. `NotebookEdit` writes the notebook back with `indent=1`, a new cell's id is 8 hex characters, a replaced code cell loses its outputs and execution count |
| Grep types | `FILE_TYPES` in the shell, files and web module: ripgrep's names for 23 common types; an unknown one is an error listing them. `multiline` matches over the whole file (`re.DOTALL`, `re.MULTILINE`) and shows every line a match covers |
| WebFetch's prompt | Answered by the maintenance model (`light`), system prompt `orchestrator.ANSWER_SYSTEM`, at most 4 000 tokens, metered like maintenance; when it cannot (budget, provider error) the page comes back with why |
| WebSearch domains | `allowed_domains` add `site:a OR site:b` to the query; results are then kept to hosts equal to or under a domain and those under a blocked one are left out, after the engine has answered (an empty engine answer is tried again, a filtered-out one is not, and says which domains left nothing); 20 results are asked for so that enough remain. Anthropic's own search takes only `query` |
| Deferred tools | Tool server tools (`ToolSpec.deferrable`) are deferred when their definitions together exceed 40 000 characters (`DEFER_OVER_CHARS`, about 10k tokens). A rarely used built-in marked `ToolSpec.deferred` is deferred always, however few tools there are, as Claude Code defers its own rarely used built-ins: `TaskStop`, `EnterPlanMode`, `ExitPlanMode` (Claude Code defers them), `NotebookEdit` (Claude Code defers it), `Screenshot`, `BiliDanmaku`, `BiliComments`, `BiliFrames`, `BiliLogin`, `BiliFollow`, `BiliFollowing`; about 4 500 characters out of every request. Kept loaded: the shell and file tools, `memory`, `TodoWrite`, `Skill`, `AskUserQuestion`, the runtime's own loop, `WebSearch` and `WebFetch` (Claude Code defers web search, but Kaden's agents, the life most, search all the time), `BiliVideo` (the way in to the Bilibili tools), and the life's everyday Bilibili tools. So ToolSearch is always offered. A deferred tool called by name, as the answer to a watch says TaskStop, is loaded and runs. Their names are listed in the system prompt; `ToolSearch` searches them, those loaded already included, and matches `select:a,b` exactly, else scores words: 2 for one in the name, 1 in the description, `+word` required in the name; default 5 results. Loaded tools are kept per conversation in memory, not across a restart; a deferred tool called directly is loaded too |
| TodoWrite | `activeForm` is required, as in Claude Code, and not stored: the desktop checklist shows a step in progress by its `activeForm`, from the call. The plan's statuses stay todo/doing/done/dropped inside; the system prompt shows them as pending/in_progress/completed/dropped |
| AskUserQuestion | Waits as a prompt does (`AWAITING_PERMISSION`). Answers by question text: the labels chosen joined with ", ", or a message's text for every question; the result reads as Claude Code's: `User has answered your questions: "q"="a", …`. A message without files answers it in words: the result says so, and the message is appended after it as the user's. A message with files does not answer it and is queued. Desktop: `QuestionCard` above the message box, a checkable button per option (one per question unless multiSelect), Send once each question has one. KFM's harness reports it as `Asked` and the turn as rested; the next `send` answers it |
| Plan mode | In memory per open conversation (`Runtime._plan_mode`), off when another conversation opens or after a restart. Runs read-only specs and `PLAN_MODE_TOOLS` (EnterPlanMode, ExitPlanMode, AskUserQuestion, TodoWrite, wrap_up, TaskStop); refuses the rest as `REFUSED` before hooks and the permission gate, `Agent` and `propose` included; an approved proposal or wrap-up option is the user's own act and runs. `PLAN_MODE_PROMPT` is added to the system prompt while it is on. `ExitPlanMode` takes `plan` (Claude Code's parameter before its plan file); outside plan mode it is an error. Results read as Claude Code's: approved: `User has approved your plan. You can now start coding…` and the plan; sent back: `REFUSED` with the feedback quoted, or, without feedback, told to wait for the user. `EnterPlanMode` asks nobody, unlike Claude Code. Approval with `permissions` goes through `update_settings` (saved, clamped to the ceiling). A message with files does not send a plan back and is queued. Desktop: "Plan mode" at the end of the permission picker, whose label reads "Plan mode" while on; `PlanApprovalCard` above the message box (the plan scrolls past 360 px), Approve, Approve and… (every level but "Never run tools", no second confirmation: the choice is the confirmation), Keep planning. KFM's harness approves a unit's plan at once with the unit's permissions and reports the plan as `Said` |
| Changed as it was read | `Write` over a file that is there and `Edit` refuse unless the conversation read it (`Read`) and it is as it was then: size, time of change and inode (`_version`), per conversation (`ShellFilesTools._seen`). Its own writes count as read. Checked once the file is open, so a path that leads outside is refused as such first. The tools' descriptions say so |
| The shell's folder | Each conversation's shell starts where its last command ended (`ShellFilesTools._folders`), while that is inside the workspace; outside, the next starts in the workspace again, so a sandboxed command never starts where it cannot see. A command ends with a line of its own, a random marker and `pwd -P`, which is taken out of the output and never shown as progress; one that exits early or is killed leaves the folder as it was. Its exit code is kept |
| Saved permission rules | `Decision.ALLOW_ALWAYS` and `DENY_ALWAYS` save `PermissionRule.for_call(call)` (api.py) in `settings.toml` as `always_allow` and `never_allow`, lists of "tool" or "tool: prefix"; a newer answer for the same rule replaces the older. `_gate` refuses on a never rule before anything else but the DENY policy, and allows on an always rule unless there is an inherited ceiling (a sub-session, a KFM agent) or `review.protected` names the call. A shell rule's prefix is the program and a plain-word subcommand; the whole command for programs that run code (`_RUNS_CODE`) or chained commands; an always rule never covers a chained command, a never rule covers any part of one. The card's **Always…** menu names the rule; Settings → Permissions lists them with Remove; removing a never asks to confirm. The SPEC's sentence on grants says so |
| Hooks | `[[hooks]]` in `config.toml` (`when`, `command`, optional `tool`, "*" by default), parsed by `runtime.hooks.parse` into `RuntimeConfig.hooks`; no settings screen. `_run_module_call` runs the "before" ones ahead of the permission gate, so a refusal needs no prompt, and the "after" ones on a result that came back in time (not on one that finishes in the background). Exit code 2 speaks (`hooks.SAYS`): a refusal, or `[hook] …` added to the result; anything else, a 60 s timeout included, is logged as `hook_failed`. A hook runs with sh, in its own process group, as the user, outside the sandbox. KFM's agents get the hooks of the base `config.toml` it copies to each of them |
| The conversation is a widget of its own | `ConversationView` (a scroll area, in `conversation.py`) draws a `ConversationModel`: one widget per item, the items grouped into steps, the "Thinking…" line, "Load earlier messages", and in its prompts area the permission and proposal cards (`prompts.py`) and the wrap-up card (`messages.py`), which its host places by the message box. `MainWindow` builds one, gives it the empty state it shows where nothing was said, passes on the model's changes (`changed`, `rebuild`, `refresh`) and keeps the header, sidebar, composer and status to itself. It takes the controller as a callable, since the conversation on screen changes with a sub-session. A window of another program that has a `ConversationModel` can show a conversation the same way |
| Draft | `start` without a conversation id or `seed_from` opens a draft: the runtime names it (`_new_id`) and keeps its info, folder included, without calling Memory. `history`, `search_archive` and `inspect_memory` answer for it as for an empty conversation |
| Stored | On first use: the first append (a message, an activation notice), a life or plan, a rename or an archive. The runtime calls `Memory.create_conversation(conversation_id=…)` with the draft's id and emits `ConversationUpdated`, which refreshes lists |
| Continue from | Stored at once: Memory needs the source to seed the summary, and the chat is not blank anyway |
| Leaving | A draft left for another chat is dropped with nothing to delete; so is one open when the instance stops |

## read_around

| Choice | Decision |
|---|---|
| Contract | `Memory.history(after=…)` reads on from a message: with `limit`, the first `limit` after it. With `before` too, the messages between |
| Tool | `read_around(message_id, conversation_id?, before=3, after=3)`, up to 20 a side: the message marked `→`, in order with its neighbours, each line as a recall hit's (1000 characters at most). Found by reading on from the message and back from the next one, so no lookup by id is needed beyond `history` |
| Offered | With `recall`, also by a Memory that keeps no notes |
| Next | Segment summaries ([proposed note](notes/proposed/2026-11-11-segment-summaries.md)) build on it |

## Voice conversation

The design is in [the note](notes/implemented/2026-11-12-voice-conversation.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| End of a turn | 0.9 s of silence (`engine.END_OF_TURN_S`); sounds under 0.25 s are not speech |
| Microphone and speaker | Both off when the app starts; neither is remembered. Turning one on first fetches and loads every speech model (to listen and to speak), so the other starts at once. The download is cancelled by turning both off |
| Voice | Chosen in Settings → General, as Claude's voice mode offers a few by name: with Kokoro, Heart (speaker 3, `af_heart`, the default), Bella (2), Nicole (6), Michael (16), Fenrir (14), its best American English speakers, at normal speed; with the [natural voice](notes/implemented/2026-11-22-natural-voice-on-the-gpu.md), Natural (the default) and the recordings. Kept by name in the window's look file (`desktop.json`, `voice`) and used at once; a saved name that is not one of the voices on offer gives the first of them. Play says `mode.SAMPLE` in it, fetching the models first if need be |
| Natural voice in use | `natural.installed()`: `chatterbox` and `torch` can be found (`importlib.util.find_spec`), so nothing heavy is imported at start. Whether CUDA works is known only when it loads; without it, voice mode does not start and says the natural voice needs an NVIDIA GPU |
| Recordings | **Add…** keeps, as `<data dir>/voice/voices/<file name>.wav`, what the voice hears of the file (`natural.start_of`): its first 30 s of talking (`HEARD_S`, found in up to its first 90 s, `READ_S`), each pause longer than 0.25 s cut to 0.25 s and its quiet ends dropped (`natural.without_long_pauses`, quiet being 35 dB under its loudest, judged per 10 ms), prepared once there (`natural.prepare_recording`) rather than at every start; the user's file is untouched. The same is applied when a voice is made ready, which costs nothing on a prepared clip and covers recordings kept whole before. Accepted: `.wav`, `.mp3`, `.flac`, `.ogg`, `.opus` (read by libsndfile) and `.m4a` (AAC, which libsndfile cannot read: librosa falls back to audioread, which uses the system's ffmpeg; librosa means to drop that fallback in 1.0). From a clip of voice lines Chatterbox copied the gaps as the way the person talks: the same four lines took 17.8 s from Gwen's clip as is and 13.8 s with its pauses cut. The voice is named after the file, and a second file of that name replaces the first. Chatterbox's conditioning for each is computed the first time it speaks, and kept while the app runs, by file and modification time, so a voice removed and added again under its name is heard anew. × in Settings removes Kaden's copy, after a question; Natural cannot be removed. Chatterbox's sampling settings are its defaults, and its watermark stays on |
| Natural voice weights | Chatterbox's English model, fetched as plain files from Hugging Face (`ResembleAI/chatterbox`) at a pinned revision, into `<data dir>/voice/chatterbox/`, with the other models' download and question; Kokoro's model is not fetched where the natural voice speaks. Its multilingual model was [tried twice and dropped](notes/rejected/2026-11-22-talking-in-chinese.md) |
| Playback | 16-bit stereo, each sample in both channels (`audio.as_played`): a one-channel stream reaches only the left ear on PipeWire through Qt's output. One stream for the whole conversation (`audio.Speaker`, Qt's push mode, fed every 10 ms), with 50 ms of silence kept queued between sounds: a stream opened per sentence started each cold, and a sound chip that sleeps in pauses (`snd_hda_intel` power saving) lost the first syllable while waking. Stop drops what Qt holds (`reset`); the stream closes when voice mode ends, and Play opens its own. A Qt stream is pinned to the device it was opened on (on PipeWire, a named sink, which the sound server does not move when the default changes), so each sound checks `QMediaDevices.defaultAudioOutput()` as it starts and, if the default changed, reopens there: the switch is heard from the next sentence, not mid-sentence, and that sentence starts on a cold stream. Following `audioOutputsChanged` to move a sound already playing was not taken: it needs the interrupted sound's position carried to the new stream |
| Threads | Half the CPU's cores, at most 8, for recognition and for speech |
| Models folder | `<data dir>/voice/`, beside `desktop.json`; a window without a data folder has no voice mode |
| Loading at start | Off by default; **Settings → General → Load voice at start** (`VoiceMode.load_at_start`, kept in `desktop.json` as `load_voice`, applied at once like the voice). When on, the app starts `VoiceMode.preload` with the window: the engine is loaded and the voice made ready in the background, nothing asked or shown; models not yet downloaded are left for the first click, and a load that fails is tried again then. A click while it loads waits for that load (`VoiceMode._load`, one shared task). Measured with the natural voice: 4.8 s of imports, 5.2 s loading Chatterbox and Whisper, 1.2 s making the voice ready, 1.9 s for the first sentence |
| Download | One model after another from sherpa-onnx's GitHub releases to `<name>.part`, unpacked beside it and renamed into place; the sizes in `download.py` are for the question and the progress only |
| Checksums | Each `Model` has a `digest`, checked as it streams in, before it is put in place; a mismatch deletes the `.part` and fails with an OSError ("does not match its checksum; try again"). Taken without downloading any model: the sha256 GitHub gives for each release asset (`digest` in the releases API; the release URLs are not pinned, so a replaced asset fails rather than loads), and Hugging Face's LFS sha256 at the pinned revision; Hugging Face's small non-LFS files carry their git blob hash (`git-sha1:`, sha1 of `blob <size>\0` + the bytes), the only hash it gives for them. Kokoro's size is the asset's (349 886 476 bytes) |
| Not taken | A message said by voice goes through `Controller.send` with `refused`: when the runtime refuses it, or the window's new chat has no folder yet (`Controller.needs_folder`, which asks the window to offer folders), the voice listens again instead of waiting for a reply. A microphone that cannot open while the speaker reads is said ("Voice could not listen: …") and the microphone stays off. A preview stops the one playing |
| Speaking ahead | The next sound is made while the one before plays, one ahead and no further (`turns.Voice._made`, a queue of one): Chatterbox takes about as long to make a sentence as to play it. With nothing to play, one sentence is made alone, with the next ones while under 20 characters (`turns.FIRST`: "Okay." plays too briefly to cover making what follows); behind one playing, the sentences waiting are made together up to 120 characters (`turns.TOGETHER`): each costs Chatterbox about a second however short, and it reads more naturally with more text. A sound made before an interrupt is dropped, not played. A sentence that cannot be made, or speech that cannot be understood, ends voice mode with the notice "Voice stopped: <error>" |
| Whisper | [Whisper listens on the GPU](notes/implemented/2026-11-23-whisper-listens-on-the-gpu.md): large-v3-turbo at a pinned revision, in `<data dir>/voice/whisper/`, float16 on CUDA through `transformers`, English set. What it writes with an average log-probability per token below −0.8 (`natural.Whisper.SURE`) is dropped as not speech: on the user's recordings, noise and silence scored −1.06 or lower and speech −0.37 or higher; the turbo model's no-speech probability is 0 even for silence, so it cannot be used. Speech over 30 s is transcribed in windows, without that check |
| Hearing speech back | With the natural voice, each sound made behind one playing is transcribed by Whisper (not the first of a reply, which is waited for: a check there would add 0.2 s to every reply and 1 to 2 s to a retry), without its noise guard (short words like "Okay." fall under it), and compared with its text (`engine.flaws`): a word said twice in a row, a word pair said more often than in the text, a sound like "hmm" or "oh" not in the text, or at least 3 (and 30 %) more words than meant are flaws; digits are left out, so "1,200" for "twelve hundred" is not one. A flawed sound is made again, up to 3 takes, and the one with fewest flaws played (`engine.checked`). On the user's GPU, about 0.1 to 0.3 s per sound; Kokoro's are not checked |
| Whisper's type | Loaded at float32 and halved on the GPU, not loaded at float16: that sets torch's default dtype for the whole process while it loads, and Chatterbox, loading on the other thread, built layers in half precision ("expected mat1 and mat2 to have the same dtype") when both loaded at once |
| Stock phrases | A transcript that is only one of Whisper's inventions on noise (`natural.stock_phrase`: "Thank you", "Thanks for watching", "you", "Bye" and the like, marks ignored) is dropped, except when hearing Kaden's own speech back |
| Getting a voice ready | Chatterbox and Whisper load onto the GPU at once, on two threads (`mode._chatterbox`). `Engine.warm` when the engine loads and when a voice is chosen: Chatterbox reads a recording then, which takes seconds for a long one, rather than at its first sentence. A recording that cannot be read is reported when it is spoken |
| Sentences | Cut at `.`, `!` or `?` before a space (not after a digit) and at line ends; an open code block is waited out |
| What the agent is told | `config.VOICE_ON_NOTICE`: write only what sounds right when spoken (short plain sentences; no emoji, kaomoji, emoticons, symbols, markup, lists, tables or code unless asked; numbers, units and abbreviations as they are said), over any other instruction on style, and do not mention the notice. `VOICE_OFF_NOTICE`: write as before, without mentioning the change. Both are hidden notices (`Message.hidden`) |
| Speech libraries | `sherpa-onnx-core` is named in the desktop extra beside `sherpa-onnx`: uv's lock file does not record that the one requires the other, and without it a fresh checkout has the speech library without `libonnxruntime` |
| Looking up whether the user was talking | `Message.spoken` of the newest message the user wrote among the conversation's last 200 (`config.VOICE_LOOKBACK`), once per opened conversation |
| A Core that does not stream | The committed reply is read when no text deltas came for it |

## How long the model thought

| Choice | Decision |
|---|---|
| Measured | By the runtime, not the Core: the time the model spent before answering in one model call: from the call's start, or its last output that was not reasoning, to the first output after reasoning that is not reasoning, summed (`_Stream.thought`). Counting only between reasoning deltas gave 0.001 s with Opus 5.5, which sends its thinking summary in one piece once it is over |
| Kept | In `Reasoning.seconds` on the message's first Reasoning block, so the archive has it and a reopened conversation shows the same line; a model call without streamed reasoning has none |
| Shown | Desktop: the line above a reply reads "Thinking… 3s" while it streams, counted each second from its first reasoning delta (`AssistantItem.thinking_since`, the window's clock; "Thinking…" alone where that is not known), then "Thought for 4s" (the sum when thinking was joined across a plan update), or "Thought" where no time is stored |

## The plan as a checklist

| Choice | Decision |
|---|---|
| Offered | `TodoWrite` with every open conversation, not only with a life or a plan; its description makes it a rule: a task that edits files, runs more than one command or has several steps gets a plan with the first tool call; a plain answer or a single action gets none. The rule is in the description, not the system prompt, because the user can replace the system prompt |
| Shown | Desktop: in the conversation, as a `ChecklistItem` drawn from the agent's `TodoWrite` calls (`common.plan_steps`): one at a time, changed by each newer call and kept under the newest message while the agent works, removed when the agent stops or a user-role message starts a new turn, as the task list in Claude's app behaves (the user compared both and chose this). A reopened conversation shows one only if the agent is working. The checklist and the wrap-up card are worked out from the message the conversation ends on (`ConversationModel._follow`), never from an earlier page, whose messages only become items. `PlanCard` is a rounded card as in Claude's app: a filled tick for done (dimmed), a turning arc for the step under way (`Spinner`), a dashed circle for the rest, dropped steps struck through. A plan the user edits has no call and no checklist. Terminal: `PlanPanel` at the top of the prompts while the agent works and a step is open (`common.checklist`) |
| Due steps without a life | Wake the agent once each, as reminders ([no duty mode](notes/implemented/2026-11-28-no-duty-mode.md)); `TodoWrite`'s answer says so (`duty.WAKES`). No spending limit is needed. A step with a due time is not asked to be closed before the turn ends |
| Steps | `TodoWrite` and `wrap_up` calls are not shown as steps (`common.CARD_TOOLS`): the checklist and the card show them |
| Where the agent sees it | In its own `TodoWrite` call and the answer (`duty.saved`: the time, due steps, a reminder while steps are open) as long as that call is among the request's messages (`Runtime._plan_call`). Otherwise in the system prompt. So a plan update leaves the prompt unchanged and the provider's cache valid: measured on Opus 5.5, a three-update task cost $0.09 instead of $0.27 |
| Closing it | While a conversation without a life has open steps, the plan section ends with `duty.CLOSE_PLAN`: bring the plan up to date before ending the turn, leaving a step open only when it waits for the user or background work. A life's plan stays open between turns, so it gets no such line. When a turn ends (after the one notice of `_send_back`, or on a wrap-up card), `_close_plan` marks the steps still open done, as Claude's app checks off its task list at the end of a turn: the user asked for the same behaviour, knowing a step may be ticked that was not done. Steps with a due time, a life's plan and a turn with background work running are left alone |
| Thinking around a plan update | Desktop: a plan update is not shown as a step, so thinking before and after it would be two Thinking lines one under the other. A message that follows committed thinking without a reply is joined to it (`ConversationModel._thinking`); while it streams, the earlier line is hidden and the stream's line stands in |
| Sent back | A turn that ends in a plain reply while steps are open and wait for nothing (`duties.unfinished`: no due time; no life; no background work running) is not ended: the runtime appends a notice (`duties.finish`) and asks the model again, once per cycle (`Runtime._send_back`). After that the turn may end with steps open, which is right when they wait for the user |

## Streaming thinking (Anthropic)

| Choice | Decision |
|---|---|
| Default | For models that always think (`claude-opus-5*`, `claude-sonnet-5*`, `claude-haiku-5*`, Fable, Mythos), a request without `thinking` in the merged options gets `{"type": "adaptive", "display": "summarized"}` (`anthropic._thinking_shown`). The API's default, `omitted`, streams empty thinking blocks, and on Opus 5.5 the notes between tool calls come back as thinking too: a long silence, then the whole answer. `display` changes what is shown, not what is billed |
| Configured | Any `thinking` in the Core's or the model's options is sent as it is |
| Maintenance | The Core option `maintenance_options` is merged over the Core's and the model's options in the runtime's own requests (`InferenceRequest.maintenance`: titles, summaries, the save pass, permission reviews), so `thinking = { type = "disabled" }` there leaves the same model thinking in a turn. Not a built-in default: Opus 5.5, Sonnet 5.5, Fable and Mythos answer 400 to `disabled` |
| Not `updates` | `display: "updates"` (a beta) would show only the notes between tool calls; `summarized` also shows the reasoning, which the Thinking section already holds |
| Desktop | The Thinking button reads "Thinking…" while the reply has no text and is not final |

## What the saving pass keeps

| Choice | Decision |
|---|---|
| Keep | What stays true and the agent cannot find again: the user, how they want answers, their decisions and why, lasting facts about their work or setup |
| Skip | News (recent work, the state of a task), what files, code, docs or git history already say, small talk, one-off requests, what is saved |
| Upkeep | A note shown in full that holds news or something no longer true is saved again without it, so memory stays current without the user |
| Limit | Notes listed by title only are not reviewed; cleaning them needs them shown, which costs the light model's input |

## Two Memory modules

| Choice | Decision |
|---|---|
| Shared archive | `SqliteArchive` (`kaden.memory.sqlite`): messages, summaries, search, conversation metadata, erasure. Hooks `_long_term(info) -> LongTerm(head, lines, render)`, `_after_answer`, `_erase`. `SqliteMemory` adds notes (schema 9, `memory.db`); `FilesMemory` adds the memory folders (schema 2, `memory-files.db`) |
| Budget | The head (rules or profile) whenever it fits the room, then lines from the top within the long-term fraction |
| Files as notes | Id and title = path, description = frontmatter `description`, text = body. Saving writes Claude Code frontmatter (not for `MEMORY.md`), the description on one line (its whitespace collapsed, as the built-in memory does); a new title renames the file, and one that another file has is refused (ValueError). No revisions |
| Other files | Hidden files and folders (`.DS_Store`, `.git`) and files that are not UTF-8 text are not memories: `notes()` and `view ""` leave them out, and viewing one by path answers that it is not a text file |
| Folder | The runtime passes the open conversation's work folder to `notes`, `save_note`, `delete_note`; `SqliteMemory` ignores it |
| Project | `FilesMemory` names the memory folder after `folder.project_root`: the nearest folder with a `.git` directory, or for a `.git` file (a worktree) the repository its `commondir` points to; a submodule is its own project. Found once per work folder and process (`FilesMemory._keys`) |
| `modified` | UTC, ISO 8601 with milliseconds and `Z`, under `metadata`; replaced on each write, added with a `metadata` block when the frontmatter has none; a file without frontmatter and `MEMORY.md` are not stamped |
| Fixed | `MemoryNote.fixed` (the built-in profile) replaces the frontends' title check |
| Tool | `memory`, Anthropic's memory tool commands, run in a thread; read-only `recall` and `read_around` come from the archive. `str_replace` takes Edit's `old_string` and `new_string`; the schema takes no other arguments (`additionalProperties: false`), so `old_str` is named back as unexpected |
| Its guards | What a conversation has read is kept per conversation, path to text, in the process (`FilesMemory._seen`): a restart means reading again. `create` refuses an existing file; `str_replace`, `insert`, `delete` and `rename` need the file read and unchanged. `MEMORY.md`: 200 lines or 25 kB loaded; a write past that is saved and answered as an error, one past 80 % with a reminder; no limit per memory file |

## Skills

| Choice | Decision |
|---|---|
| Name | `[a-z0-9][a-z0-9-]{0,63}`; frontmatter `name`, else the folder's name |
| Description | Frontmatter `description`, else the instructions' first paragraph; one line, 500 characters at most in the listing |
| Order | Listed by name; Kaden's, then `<data dir>/skills`, then `<work folder>/.claude/skills`, the later replacing the earlier |
| Reread | With every request and every `RuntimeAPI.skills()` call, in a thread |
| Tool | `Skill`, read-only, never gated or counted; a leading `/` in the name is dropped; an unknown name answers ERROR with the names |
| Prompt | The section follows the user's instructions and precedes Memory's addendum and the life, so the stable part of the prompt comes first |

## Prompt caching (Anthropic)

| Choice | Decision |
|---|---|
| Default | Every turn request carries top-level `cache_control: {"type": "ephemeral"}` (`anthropic._cached`): the API marks the end of the request, so the next one reads the conversation so far from the cache. Measured on Opus 5.5: a second turn's 11.5k tokens of prompt cost $0.003 instead of $0.046 |
| Not cached | The runtime's one-off requests (`InferenceRequest.maintenance`: titles, summaries, the save pass, permission reviews): nothing would read what they wrote, so they would only pay the write premium |
| Configured | A `cache_control` in the Core's or the model's options is sent as it is (`ttl = "1h"`) |
| Cost | Written tokens count as input at the input price; the premium (25 %, or 100 % for an hour's cache) is added as `Usage.extra_cost_usd`, from the model's input price |
| Limit | The life and Memory's addendum are in the system prompt, which comes before the messages: when one changes, the next request writes the conversation again instead of reading it. The plan is kept out of it while the conversation shows it, and the files Memory keeps each conversation's index as it started (measured: after a memory write, the next request read its whole prompt from the cache). What remains: a new rolling summary, and the built-in notes Memory, whose index changes with every save |

## One interface, not chosen in Settings

| Choice | Decision |
|---|---|
| Offered | AI provider and Tools (`common.MODULE_LABELS`). The interface is not a setting (the user, 2026-11-12): `kaden`, `kaden-desktop` (entry point of `kaden.window:main`) and the app menu all open the desktop app |
| One interface | Kaden ships the desktop app and no terminal app (the user, 2026-11-21: it was not used, and it was a second implementation of the conversation that had drifted from the one the desktop app and KFM share). `kaden` opens the desktop app; headless mode is for programs; a terminal frontend can still be installed as a module. |
| Still honoured | A frontend saved under `[modules]` in settings.toml or named in config.toml: the launchers and `restart()` run it: a window frontend in place of the desktop app, a terminal frontend in the terminal `kaden` is started in |

## Seeing the screen

Design: [Agent Note](notes/implemented/2026-11-12-seeing-the-screen.md), capture:
[Agent Note](notes/implemented/2026-11-26-screenshots-on-each-system.md).

| Choice | Decision |
|---|---|
| Capture | `screen.find()` when the module is made: macOS `screencapture` on `PATH`; Linux the desktop portal when its Screenshot `version` property answers on the session bus within 2 s; else none and the tool is not listed |
| Portal | `Screenshot("", {handle_token: "kaden<16 hex>", interactive: false})` over jeepney's asyncio router, the `Response` signal matched (`AddMatch`) before the call; response 1 is "refused on the desktop", another non-zero or no `uri` "gave no screenshot"; the file at the URI read, then removed; on Stop or after 20 s `Request.Close` is sent |
| macOS | `CGPreflightScreenCaptureAccess` (ctypes, CoreGraphics) first: false is an error naming System Settings → Privacy & Security → Screen Recording; a macOS without the function counts as allowed. Then `screencapture -x -t png <folder>/screenshot.png` in a `kaden-screen-` temporary folder, removed after the read; 20 s (`screen.CAPTURE_TIMEOUT`), then the program is killed, as on Stop |
| Result | `[Screenshot of the user's desktop, W x H pixels]` (the size from the PNG header) with the picture as `Image("image/png", …, "screenshot.png")`; over 5 MB is an error |
| Tool | No arguments, not read-only, never confined, timeout 60 s |

## Attachments

| Choice | Decision |
|---|---|
| Limits | Image: `.png .jpg .jpeg .gif .webp` by suffix, up to 5 MB; PDF: `.pdf` by suffix, up to 20 MB (the provider takes 32 MB per request, and base64 adds a third) and 600 pages, else by path; one pypdf cannot open goes by path; text: UTF-8 without NUL bytes, up to 200 kB; anything else by path (`tools/shell_files/attachments.py`) |
| Who reads | The first Tools module, in the order they are offered, whose `attach` does not raise NotImplementedError; any other error it raises refuses the message as a ValueError; with none, `File(path)` |
| Message | `Text(text)` first, also when empty, then the attachments in the order given |
| Where it is | A picture keeps its absolute path (`Image.path`) as a PDF and a file do. Cores send `api.attached_at` ("[Image attached: /path]", "[File attached: /path (PDF, N pages)]") as text with a picture or PDF the user attached, as Claude gives uploads a path: the agent can copy or open it (a life asked to keep a picture it was sent and had no file). Not for pictures a tool returned: their result says what was read. A picture stored before it had a path has an empty one |
| Office files as text | Word: paragraphs in order, `#` per heading level, `- ` and `1. ` for list styles, a table as `\| a \| b \|` rows. Excel: `## Sheet: name`, then a row per non-empty row, values as last saved, trailing empty cells dropped. PowerPoint: `## Slide n`, the text of its shapes and tables, `[Notes] …`. A file whose text is over 200 kB is attached by path; `Read` pages through it by lines |
| `Read` on a PDF | `[PDF, N pages]`, then `[Page n]` and its text for every page or the `pages` range ("3", "2-5"), up to 100 000 characters, with a note to read on; a note to use `view` when no text could be read. With `view: true`: content `[PDF, N pages]`, with `; pages a-b attached` for a part and `; use pages "c-d" to read on` when more than 20 were asked for, and the pages as a `Document` built with pypdf (a PDF of just those pages) with their text; over 20 MB an error |
| `Read` on a picture | Content `[Image path]` and the `Image`; over 5 MB an error |
| Stored | A `ToolResult`'s attachments are encoded as blocks inside it (`codec._encode_value`); search leaves their data out as for top-level blocks |
| Shown | The desktop app adds `[image: name]` / `[file: name]` lines to a step's result (`common.result_text`) |
| As text | `[File attached: path (PDF, N pages)]` and its text, or that none could be read; `[Image attached: name]`; `[File attached: path]` and the content; or that the content is not included (`api.attachment_text`) |
| Desktop | Dropped files and pasted file lists are attached, not typed in as paths (`MessageBox.insertFromMimeData`); a pasted picture is saved as `pasted-N.png` in a temporary folder removed when the window closes; the same path is not attached twice |

## Queued messages

| Choice | Decision |
|---|---|
| Read | At a cycle's start and at each turn boundary (`Runtime._deliver_inbox`). One rule where every cycle ends (`Runtime._cycle`): a message still queued starts the next cycle, so no pause inside a turn can leave one behind |
| Not started | After Stop (kept, starts nothing), after a failed append (the agent is FAILED until one succeeds) and on an exhausted budget, where `send` is refused too |
| Stop and shutdown | Deliver through the same `_deliver_inbox`: files and the voice notice go with a message queued before Stop |

## States and refusals

| Choice | Decision |
|---|---|
| FINISHING | Entered when the cycle's last answer (or a `wrap_up` that ends the turn) is appended, before Memory's upkeep after the answer; left for IDLE, or for THINKING when a queued message starts the next cycle. Commands as in THINKING: `send` queues, `start` and `activate` are refused as BUSY |
| Refusal | NOT_STARTED: `send`, `notify`, `activate`, `set_duty`, `set_plan` before `start`. BUSY: `start` or `activate` during a cycle, while stopping, or with operations outstanding. BUDGET: `send`, `notify`, `activate` on an exhausted budget. NOT_PENDING: an unknown or finished call, prompt or proposal. CONVERSATION_OPEN: deleting the open conversation. UNAVAILABLE: any command after shutdown, every command the setup runtime cannot serve, a sub-session whose process ended, `restart` of a sub-session |
| On the channel | `error.data` is `{"type": "RuntimeStateError", "reason": "<refusal>"}`; other exceptions have `type` only |

## Continuous

The design is in [the note](notes/implemented/2026-11-23-continuous.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| Where it lives | `<data dir>/life/`, a headless Kaden the desktop app starts (`frontends.life.Life`), with the app's `config.toml` and `settings.toml` copied in on every start, its own budget, work folder (`life/home`) and prompt; `life.json` holds its conversation, name and what the user said of who it is |
| When it starts | The first time the user opens its view, once they have named it (`LifeDialog`, Cancel leaves it unstarted); after that with the app. `Life.start()` is idempotent: a second call while it starts waits for the first |
| Who it is | `Life.who`: "You are <name>.", that it lives on the user's computer and is not their assistant, then what the user said, or that the rest is its to find out |
| Pace | Asleep until something wakes it (`duty.next_wake` returns None for a life with no time ahead): the user, a watch, its background work, or a time it chose (`Rest`, a plan step's due time), at the earliest after 60 s and at most 24 h ahead (`life.PACE_S`). A step already past its time does not wake it again. "Awake by default" (a turn a minute) was removed at the user's request: woken with nothing to do, it only said "(Still resting.)" and spent its day |
| Its own time's model | The user's maintenance model, as `Duty.model`: cycles the life starts itself use it, the user's messages are answered with the conversation's. At Opus prices US$1 a day bought a couple dozen turns |
| Daily amount | US$1 a day (`life.DAILY_USD`), as `Budget.max_cost_usd_per_day` in its settings, counting only the cycles it starts itself (`BudgetTracker.charge(own_time=True)`): talking with the user spent it at first, and then it could not live. The user sets it in Settings · <name> (`LifeSettingsDialog`, empty for no limit): kept in `life.json` (`daily_usd`), written into its settings at each start, and applied at once over the channel (`Life.set_daily_usd`), without a restart |
| Its life on every start | `set_duty` with its life as the app has it (pace, model, who it is), a resumed one too |
| Its prompt | Its own (`life.PROMPT`): that it has a life of its own and tools to live it, that the user can see the files in its folder (no file named), then `config.TOOL_FACTS`; "## Your life" in the system prompt says how it comes back |
| What reaches the user | Text of an assistant message in a turn the user did not start: in its view; elsewhere a dot on the Continuous switch (`Switch.unseen`, cleared when its view opens) and, the window not in front, one notification per burst (its newest words, "<name> (N new)") |
| Switching views | The **Continuous** switch in the title bar (`MainWindow.continuous_switch`), before the window buttons, in the same place in both views: the one way between them, no menu item and no back button. The title row keeps one height in both, so the switch does not move |
| Its view | No sidebar; ☰ (the sidebar's menu), its name (`life_name`, as wide as it is, in place of the chat title) and Files (its folder, opened in the file manager with `QDesktopServices`) in the title bar; its name and who it is in Settings · <name> (`LifeSettingsDialog`), the naming dialog (`LifeDialog`) only before it first lives, the gear bottom left; its current step (`ToolItem.sentence`) as the status line while it works; "Message <name>" as the message box's hint; "Waking up…" while its process starts |
| Opening where the user left off | The look file (`desktop.json`) keeps `life`: whether its view was open on close |
| Voice | Follows what is on screen (`VoiceMode.use`) |

## Close to tray

The design is in [the note](notes/implemented/2026-11-23-close-to-tray.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| When | Whenever `QSystemTrayIcon.isSystemTrayAvailable()` at start; otherwise closing quits |
| The icon | Kaden's app icon (`app_menu.ICON`), tooltip "Kaden"; a click or **Open Kaden** shows the window as it was (maximized stays maximized), **Quit** quits |
| The message | Once per run, at the first close: "Still running. Quit from this icon." for 4 s |
| Quitting | `MainWindow.quit()`: the Quit actions, Ctrl+Q, and the app's own shutdown; `close()` alone only hides while there is a tray |

## Watching Bilibili

The design is in [the note](notes/implemented/2026-11-23-watching-bilibili.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| As a visitor | A browser's User-Agent and `Referer: https://www.bilibili.com/`; the home page is loaded once per session for the visitor cookie it gives (`buvid3`). Nothing more is done to look like a browser: no forged fingerprints, tickets or captcha answers |
| Signing | WBI as Bilibili's pages do it: the two keys from `/x/web-interface/nav` (answered to visitors), mixed in its fixed order, read again after an hour; `wts` and `w_rid` (MD5) on the endpoints that want them |
| Endpoints | Details `/x/web-interface/view`; subtitles `/x/player/wbi/v2`, then the track's file; danmaku `/x/v1/dm/list.so` (XML); comments `/x/v2/reply/wbi/main` (`mode=3`, the top ones), with up to 3 replies each; stream `/x/player/wbi/playurl` (`qn=32`, MP4) |
| Search | None: `/x/web-interface/wbi/search/type` answers a visitor with `v_voucher` (a captcha) |
| Frames | The system's ffmpeg (`shutil.which`), cutting each moment from the stream with the same headers, 480 px high JPEG, up to 6 a call, 60 s each at most; without ffmpeg `BiliFrames` is not offered |
| Output | Times as `[mm:ss]` (`h:mm:ss` past an hour); danmaku sorted by moment, 300 at most unless asked; 10 comments unless asked, 20 at most |

## Backups

The design is in [the note](notes/implemented/2026-11-25-backups.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| What | The data folder but `voice/` (models and prepared recordings), `backups/`, `instance.lock` anywhere, and files ending `-wal`, `-shm`, `-journal` or `.tmp`; symlinks are not followed. Kaden's old `.bak` files and other folders are kept: they are the user's data. About 11 MB on 2026-11-25, copied in 0.03 s |
| Databases | Any `*.db`, by `sqlite3.Connection.backup` from a read-only connection; a file named `.db` that is not one is copied as a file |
| When | `backup.keep_backed_up`, started by the desktop app: a backup at start unless today has one, then a check every hour (`CHECK_S`) |
| Where | `<data dir>/backups/<yyyy-mm-dd>`, written as `.partial-<date>` and renamed when whole; a partial one left by a crash is deleted at the next backup |
| Thinning | `backup.to_keep`: restic's `--keep-daily 7 --keep-weekly 4 --keep-monthly 6` (ISO weeks, calendar months; one backup may count for several) |
| A failure | Logged, and said once as a notice in the conversation on screen, until a backup succeeds again |

## A Bilibili account of its own

The design is in [the note](notes/implemented/2026-11-25-a-bilibili-account-of-its-own.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| Where | `tools/shell_files/bili_account.py` (`Account`), given its instance's data folder by the built-in Tools module's factory (`paths.data_dir`); the visitor tools (`bilibili.py`) stay as they were |
| The login | QR login (`passport…/qrcode/generate`, then `…/poll` every 2 s for 180 s) in the module's own background task; the code returned at once as a PNG drawn with `segno` (scale 8); cookies `SESSDATA`, `bili_jct`, `DedeUserID`, `DedeUserID__ckMd5`, `sid`, `buvid3` and the `refresh_token` kept as JSON in the keychain (service `kaden`) under `bilibili-login:<data dir>`; `bilibili-account` in the data folder (no secret) marks that there is one, and only then is the keychain asked |
| Tools offered | `BiliLogin` to every instance with a data folder, deferred (named in the system prompt, loaded with ToolSearch when wanted): a one-off need not be described in every request; `BiliFollow`, `BiliFollowing`, `BiliFeed` once logged in, in full, since `list_tools` is read each cycle |
| Following | `POST x/relation/modify` (`act` 1 or 2, `re_src` 11, `csrf` = `bili_jct`); a channel is its uploader id, a `space.bilibili.com/<id>` link, or `UID:<id>`; not read-only (`confined` false), so it goes through permissions as an action outside the work folder |
| The feed | `x/polymer/web-dynamic/v1/feed/all?type=video`, its first page and the next (`offset`, up to 3) while a whole page is new; a video is new when its post id (`id_str`) is not among those delivered and it was posted since the feed was last looked at, less an hour (`LATE_S`), kept in `bilibili-feed.json` (`delivered`, the newest 1000, and `since`): ids alone made a new follow's past uploads news, a timestamp alone lost videos posted in the same second or listed late and brought old ones back when the newest was deleted; the first look (`BiliFeed`) shows the latest 10, a watch's first look gives nothing and waits for what comes after; `wait` reads the feed every 10 min (`FEED_CHECK_S`), then 5 min (`BATCH_S`) after the first new video, and delivers what was new against the posts delivered when it began (a look meanwhile does not empty it); a read that fails is tried again at the next check unless the login is gone; one wait at a time; no time limit (`timeout=inf`) |
| Refusals | Bilibili's answer reported as it is, never retried; `-101` (`LoginGone`) drops the login that got it from the keychain at once (`Account._forget`; one kept meanwhile stays), so its tools and the standing inbox watch go with it instead of the watch being started again each cycle; it says the login stopped working and the next `BiliLogin` gives a new code, saying why. While a code waits, a poll that fails is tried again; a login that cannot be kept (a locked keychain) is said with the next `BiliLogin`. A keychain that fails or holds nonsense counts as no login and costs the account's tools only. A log out waits for a login being kept, then undoes it (`_saving`, `_generation`) |
| Pictures in cards | A tool's pictures (`ToolResult.attachments`) are shown in its step's card, under its row, folded or not, within 480 by 240 px (`PICTURE_WIDTH`, `PICTURE_HEIGHT`); one that is not base64 or not a picture is left out |
| Messages | `BiliMessage` to a UID or a space link (`_channel`), sent with `api.vc.bilibili.com/web_im/v1/web_im/send_msg` (text, a fresh `dev_id`, `csrf` and `csrf_token` from `bili_jct`), answered with the account's name (`x/web-interface/card`, kept for the run); `BiliMessages` reads the inbox: `session_svr/v1/session_svr/get_sessions`, then `svr_sync/v1/svr_sync/fetch_session_msgs` for each sender whose last message is past their mark; others' text messages only; marks by sender (`msg_seqno`) in `bilibili-messages.json`; the first look shows the latest 10 and marks every conversation as read to its latest message (`_inbox_marks`); a watch's first look marks them without firing: an inbox years old came as new in pieces otherwise; `BiliWatchMessages` reads every 5 s while a conversation is going (`MESSAGE_ACTIVE_S`: a message sent or received, by the watch or `BiliMessages`, in the last 300 s, `ACTIVE_FOR_S`), else every 30 s (`MESSAGE_CHECK_S`): quick when someone is talking, and lighter than a steady 20 s the rest of the day; at 60 s a reply waited half a minute on average. The user asked for a general tool: no account is set for it, and the life learns the user's UID as anyone's |

## Standing watches

The design is in [the note](notes/implemented/2026-11-25-standing-watches.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| Ids | `w<n>`, the next number among the data folder's watches (`watches.next_id`); a watch's calls run as `w<n>.<k>`, which only the module sees |
| The file | `<data dir>/watches.json`, `{"version": 1, "watches": [...]}`, each with id, conversation, tool, arguments and `kept`; written whole through a temporary file; another version is refused (logged, and no watches resumed) |
| Its answer | "Watching (w3): <tool>. Each time it fires you are told, in a notice, and it watches again, also after a restart. TaskStop with w3 ends it."; a second call with the same tool and arguments answers "Already watching (w3)" |
| A firing | A notice of Kaden's, `[Watch w3, <tool>] <outcome>`; on a conversation with a life, from a watch of its own: kept until a cycle (`duties.WATCH`, its own time, not on a spent day) and delivered at its start; otherwise queued as `notify` is, waking at once |
| Looking again | The watch's next call waits until its firing is in the conversation and confirmed (`_Firing.delivered`); without that, a firing held on a spent day was fired again each second with the same messages |
| Failures | First wait `watch_retry_s` (60 s), doubling to `watch_retry_max_s` (3600 s); "failed; it is tried again later" once, until one succeeds; `ToolOutput.retry` False ends it with "ended: …" |
| Kept by a module | Read from each Tools module's `standing_watches()` when a conversation opens and after each cycle, only with a life; not gated (read-only); TaskStop on one is refused. `StandingWatch.as_user` (the user's own messages) wakes as the user's message does; otherwise its firings are the life's own time |
| Its context | `_WatchContext`: confined, progress not shown; watches are read-only |
| Bilibili | `BiliWatchFeed` and `BiliWatchMessages` (watches); `BiliFeed` and `BiliMessages` look once; the built-in module keeps `BiliWatchMessages` while logged in, as the life's own time (`as_user` false): anyone can write to an account, so a stranger's messages must not spend beyond the daily amount; a login gone is `retry=False` |

## Tool settings

The design is in [the note](notes/implemented/2026-11-25-tool-settings.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| Where the declaration is read | `modules.declared_settings(factory)` imports the Tools factory in `load_config`; a factory that does not load, or declares a wrong type or a default of another type, declares nothing (logged) |
| The file | `[tool_settings]` in `settings.toml`, written whole on each change (not merged as a table), so a value of None leaves it; values of settings the running Tools module does not declare are dropped then |
| Checks | `modules.takes`: a `boolean` takes a bool; a `string` a str; a `choice` one of its values. Unknown name or other value: ValueError naming it |
| Restart needed | When the saved values differ from those the factory was given this run |
| The life's process | `Life.running` is false once its process ends by itself (the app is told) or a start fails (the process is closed); a later `start` tries again. `aclose` during a start waits for that start to close its process; the caller of the start that was closed gets a `RuntimeStateError` ("closed while it started"), not a cancellation |
| The life | `Life._prepare` drops the chats' `[tool_settings]`, `always_allow` and `never_allow` and keeps the life's own (`life.OWN_SETTINGS`): answers given in its view are its own; its process starts without `KADEN_PERMISSION_CEILING` (which the app's runtime sets for what it starts), or its saved "always" answers would be ignored as a child's; `Life.restart` is `aclose` then `start`; the window shows it waking meanwhile and its view again after |
| Bilibili | `bilibili_wake`: `follows` (default), `anyone`, `nobody`. Followings read 50 a page, at most 40 pages, kept 600 s (`FOLLOWS_S`); a conversation with something new from an account not followed is counted by its `unread_count` in `get_sessions` and not fetched; the firing ends with "N more conversation(s) waiting, from accounts you don't follow (BiliMessages reads them)." |

## Notifications

| Choice | Decision |
|---|---|
| When | A prompt other than a proposal opens in the window's own conversation (`MainWindow._notify_new_prompts`, on its model's `Reset` and `PromptsChanged`) or the life's (`Life.take_questions`, from its `PromptOpened` events), while the window is not the active one or is minimized |
| How | The tray icon's message (`QSystemTrayIcon.showMessage`, a freedesktop notification on Linux), 10 s: "<who> needs your answer", with the prompt in one line (`common.prompt_summary`: the card's headline, and its one argument, such as a command). None without a tray; sub-sessions' prompts are not notified |
| A click | Brings the window forward (`bring_forward`); for the life's, switches to its view |



## Memory recall

The design is in [the note](notes/implemented/2026-11-26-memory-recall.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| The hook | `Runtime._came_in` collects the text appended from the inbox (the user's messages, `notify`'s notices, a kept watch's firings) and the conversation's own watches' firings; before the next inference, after upkeep and the budget check, `_recall` runs once for it. The activation notice and a duty's own notice add nothing to it |
| The call | The maintenance model (`light`), `RECALL_SYSTEM`, at most 300 tokens out; it sees the end of what came in (4000 characters, between `<<<` and `>>>`) and `- title: description` per candidate; only exact titles (case ignored, bullets stripped) count, at most 5 (`RECALL_MAX`). Budgeted as maintenance; raced against Stop; a refusal or failure (the Core's, or Memory's `notes()`/`assemble()`, logged by its type only) is logged (`recall`, `failed`) and recalls nothing. The notes are those of the conversation's folder (`Memory.notes(folder=...)`) |
| The notice | Hidden, Kaden's: `RECALL_HEADER`, then per note `## <title>`, `(note "<id>", changed <updated_at>)` and its text |
| Not twice | What the assembled context still holds of those lines (`_RECALLED`): a note recalled and still in view, unchanged, is not a candidate; a recall summarized away, or a note changed since, can come again. Notes with `MemoryNote.always_shown` (the built-in profile, the files module's `MEMORY.md`) are never candidates; with no other note, nothing is assembled or asked |
| Built-in memory | Its save pass leaves out Kaden's hidden notices (a recalled note is not news); the files module's memory tool still asks for a note to be viewed before it is edited |

## Always allow for chained commands

The design is in [the note](notes/implemented/2026-11-26-always-allow-for-chained-commands.md); the choices it leaves open:

| Choice | Decision |
|---|---|
| Reading | `api._command_parts`: no allow cover for a newline, `#`, backtick, parenthesis or `<<` anywhere (`_UNREAD`); `shlex` (posix, `punctuation_chars`, `whitespace_split`, no comment characters), split on `_SEPARATORS`; `>`, `>>`, `&>`, `&>>` only into `/dev/null`, `>&` only to a stream number; any other operator, an empty part, an unclosed quote: not read |
| A part's program | Leading `time`, `nohup`, `command`, `nice` (bare) and `timeout <n>[smhd]` skipped (`_unwrapped`); a wrapper with options, or a variable set in front: not read |
| What is kept | `_kept`: the whole part for `_RUNS_CODE` (now also `find`, `awk`, `sed`, `make`, `ssh`, `su`, `doas`, `watch`, `dash`, `fish`, `source`, `.`, `parallel`, `gawk`) and for `git` with an option first; program and subcommand for `_SUBCOMMANDS` (git, npm, cargo, docker, …); else the program. A plain second word is no longer taken as a subcommand for other programs (`echo x` keeps `echo`) |
| How many | At most 5 per answer (`_RULES_KEPT`); more, or a command not read: no "Always allow" on the card, and an `ALLOW_ALWAYS` that comes anyway (another frontend) counts as once |
| Where chosen | `Runtime._gate` puts them in the question (`Prompt.details["always"]`, `["never"]`, as `str(rule)`); `_save_rules` saves them in one settings update under the settings lock, each replacing any rule with the same tool and prefix |
| The card | `_kept_label`: “cp”, “echo” and “ls”; a tool by its name; labels past 60 characters cut with "…", the whole in the tooltip (`LABEL_CHARS`) |


## The data folder in protected: by its path below the home folder (2026-11-26)

`review.protected` matches Kaden's data folder by its full path and by its path below the
home folder as segments of their own (`.kaden` in `cd ~ && cat .kaden/settings.toml` and
`../../.kaden/usage.json`; `.kaden/life` for Continuous's), since a command may reach it
from any directory. A data folder outside the home folder is matched by its bare name only
when that name is dotted. Never by a plain word: Continuous's `life` would make it ask
about its own files. Not attempted: resolving `cd` and relative paths in a command,
which the shell, not a pattern, decides.

A work folder inside the data folder (Continuous's `~/.kaden/life/home` in its data folder
`~/.kaden/life`) is not Kaden's data: before matching, the paths that name it or lie in it
(its resolved and given absolute forms, `~/`, `$HOME/`, `${HOME}/`) are taken out of the
arguments, unless `..` follows in the same path.

## A step falls due at most a hundred years ahead (2026-11-26)

`duty.parse_plan` refuses a TodoWrite due time (`due` or `due_in_minutes`) more than
`MAX_DUE_AHEAD_S`, a hundred years, ahead: one near year 9999 was saved and then broke
every later request (`local()` raised). A hundred years leaves any real reminder alone.

## What TaskOutput still finds (2026-11-26)

TaskOutput finds the operations launched for the open conversation: opening another one
forgets them (none runs then: `start()` refuses while one does). Of the finished ones it
keeps the newest `KNOWN_OPS`, 200; an older one's outcome is in the conversation already,
delivered when it finished.

## What a conversation keeps when another one is opened (2026-11-26)

`Runtime._leave`, before `start()` opens another conversation: its proposals are withdrawn,
its watch tasks end (awaited, a call that outlasts `cancel_grace_s` left) and so do the
watches' writes; messages that wait to be saved (an append failed) are saved into it,
and if that fails again they are dropped and logged (`left_unsaved`); firings of its own
watches held for a cycle are let go unconfirmed, so their watches see them again when it
is back. Nothing of it reaches the next conversation. Watch files are written one at a
time, in order, a write under way finished even when its caller is cancelled.

## A life's prompts close after 3 minutes (2026-11-26)

In a conversation with a life, a permission, AskUserQuestion or ExitPlanMode
prompt left unanswered for `RuntimeConfig.life_prompt_timeout_s`, 180 s, closes by itself
(`PromptClosed`, reply None; decision `prompt_timed_out`): the call is refused, the question
or plan goes unanswered, each with "No answer in 3 minutes: the user may be away from the
computer.", and the life carries on. The user talks with it from elsewhere (Bilibili on the
phone), where no card can be answered, and a waiting prompt held its whole conversation.
Three minutes is long enough to answer at the computer. Other conversations wait as before;
proposals never hold a conversation and are left alone.

## A life rests with Rest (2026-11-26)

| Choice | Decision |
|---|---|
| The tool | `Rest` (`api.DUTY_TOOLS`), offered only with a life; read-only, never gated, not counted. `until` (ISO 8601, local without an offset) or `minutes`; with neither, until something wakes it. Always capped at the life's `max_interval_s`; an unreadable time is an ERROR result |
| The rest | Kept in the runtime only (`Runtime._rest_until`), not in the conversation: a restart wakes the life as before. Any cycle ends it, whatever started the cycle; `next_wake` takes the earlier of the rest's end and a step that falls due before it, never sooner than `min_interval_s` |
| The prompt | `duty.prompt` names Rest beside a plan step's due time, as ways it can rest |
| The notice | A life's scheduled check-in says `<span> passed; it is HH:MM.` (local time), the span since its last cycle ended (`duty.span`). After a restart, with no last cycle in this process, it is "Time has passed." |

## After the review (2026-11-27)

| Choice | Decision |
|---|---|
| The unused `format` column | Dropped from `messages`; the archive is schema 4 (`SqliteArchive.SCHEMA_VERSION`), the built-in Memory 11. A store of the earlier schema is refused, as the rule says. The user's two stores (theirs and Continuous's) were moved once by a script that is not part of Kaden (`ALTER TABLE messages DROP COLUMN format`, `PRAGMA user_version = 4`), each backed up beside itself as `*.schema3.bak` first |
| One keychain reader | `api.read_key(name)`: the environment, then the keychain, None without either; Cores and the runtime both use it (contract issue P7) |
| The work folder option | A Tools factory names it with `work_folder_option` (the built-in: `root`); the launcher fills it with the saved work folder, else the option from `config.toml`, else the folder Kaden started in (P8) |
| A life's home by a relative path | `protected` also leaves out the work folder's path below the data folder's parent (`.kaden/life/home/…`), as whole path segments and not climbing out; not its bare name below the data folder, which `/home/<user>` would also match |
| Flaky tests | `test_a_subagents_changes_stay_in_its_worktree` and one desktop window test failed only while four other sessions ran test suites on the same machine; neither failed in six full runs here, at 8 and 16 workers. Left as they are until a failure can be read |

