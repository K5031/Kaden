# Agent Note: Claude Code's tools

Status: implemented — the shell, file and web tools take Claude Code's names, parameters and output shapes

## Problem

Kaden's shell, file and web tools did what Claude Code's do, under other names and
parameters: `shell`, `read_file`, `edit_file`, `search_files` and so on. Models from
Anthropic and others are trained at length on Claude Code's tools (`Bash`, `Read`, `Edit`,
`Grep`), with their exact parameters, and on what their results look like. A tool shaped
the same way is one the model already knows how to use well; a near copy under other names
is one it has to learn from the description each time. The user asked for Kaden's tools
to match Claude's.

## Decision

The shell, files and web module offers:

| Tool | Parameters |
|---|---|
| `Bash` | `command`, `timeout`, `run_in_background`, `dangerouslyDisableSandbox` |
| `Read` | `file_path`, `offset`, `limit`, `pages`, `view` |
| `Write` | `file_path`, `content` |
| `Edit` | `file_path`, `old_string`, `new_string`, `replace_all` |
| `Glob` | `pattern` (required), `path` |
| `Grep` | `pattern`, `path`, `glob`, `output_mode`, `-i`, `-A`, `-B`, `-C`, `head_limit` |
| `WebSearch` | `query`, `max_results` |
| `WebFetch` | `url`, `start` |

- **Read** numbers its lines as `cat -n` does (the number right-aligned in six columns, a
  tab, the line), as Claude Code's does; `Edit` takes the text without that prefix.
- **Glob** matches a real glob against the path relative to `path`, the most recently
  changed first: `*.py` is the top folder only, `**/*.py` every folder.
- **Grep** lists only the files that match unless `output_mode` says `content` or `count`;
  `-A`, `-B` and `-C` are lines after, before and around each match; `head_limit` keeps
  the first entries of the result.
- The Anthropic Core offers its own search as `WebSearch` too, sent to the API under
  Anthropic's name `web_search`, so it still takes the module's place.
- Claude Code's per-call `description` on `Bash` is Kaden's `step_summary`, declared in
  its schema: see [tools declare their own step summary](2026-11-27-tools-declare-their-step-summary.md).
- `Bash` runs a command until it ends or its `timeout`, or in the background when asked:
  see [Bash chooses its background](2026-11-22-bash-chooses-its-background.md).
- The rest of Claude Code's tools and parameters (`WebFetch`'s `prompt`, `Grep`'s `type`
  and `multiline`, `NotebookEdit`, the runtime's tools) are in
  [all of Claude Code's tools](2026-11-22-all-of-claude-codes-tools.md).

## Alternatives considered

- **Keep Kaden's names.** Each tool already did what its Claude Code counterpart does,
  and the descriptions said how to use them. Rejected at the user's word: the model's
  training is a better guide than a description, and nothing is gained by differing.
- **Rename only, keep the parameters.** Half the benefit: the model reaches for `Read` and
  then has to learn that it takes `path`, not `file_path`.
- **Match every Claude Code tool, the runtime's too** (`TodoWrite` for the plan, `Agent`
  for sub-sessions). The plan has due times and notes and is how a reminder is kept, and a
  sub-session is a conversation the user sees: the same name would promise behaviour
  these do not have.

## Consequences

- The model meets tools it knows, on every provider: the names are plain function names.
- Kaden's tool names mix Claude Code's (`Bash`, `Read`) with its own snake_case ones
  (`memory`, `wrap_up`).
- Saved permission answers and hooks that name an old tool no longer match it; with no
  migration, they are the user's to re-make. Conversations from before keep calls under
  the old names in their history, as calls of tools no longer offered.
- `Read`'s numbers cost a few characters per line, and a pattern like `*.py` in `Glob` no
  longer reaches into subfolders.
