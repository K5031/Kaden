# Agent Note: Claude Code's tools

Status: implemented — the shell, file and web tools take Claude Code's names, parameters and output shapes

## Problem

Kadence's shell, file and web tools did what Claude Code's do, under other names and
parameters: `shell`, `read_file`, `edit_file`, `search_files` and so on. Models from
Anthropic and others are trained at length on Claude Code's tools (`Bash`, `Read`, `Edit`,
`Grep`), with their exact parameters, and on what their results look like. A tool shaped
the same way is one the model already knows how to use well; a near copy under other names
is one it has to learn from the description each time. The user asked for Kadence's tools
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
- Only what Kadence does is matched. Claude Code parameters for behaviour Kadence does not
  have are left out rather than accepted and ignored: `WebFetch`'s `prompt` (a Tools
  module cannot ask a model), `WebSearch`'s domain lists, `Grep`'s `type` and
  `multiline`. Claude Code's per-call `description` is Kadence's
  `step_summary`, which the runtime adds to every tool.
- `Bash` runs a command until it ends or its `timeout`, or in the background when asked:
  see [Bash chooses its background](2026-11-22-bash-chooses-its-background.md).
- Kept as they are, having no Claude Code tool of the same meaning: `screenshot`, the
  Memory tools (`memory` already has the shape of Anthropic's memory tool), and the
  runtime's built-ins (`update_plan`, `propose`, `wrap_up`, background tasks, skills,
  sub-sessions), whose nearest Claude Code tools do something else.

## Alternatives considered

- **Keep Kadence's names.** Each tool already did what its Claude Code counterpart does,
  and the descriptions said how to use them. Rejected at the user's word: the model's
  training is a better guide than a description, and nothing is gained by differing.
- **Rename only, keep the parameters.** Half the benefit: the model reaches for `Read` and
  then has to learn that it takes `path`, not `file_path`.
- **Match every Claude Code tool, the runtime's too** (`TodoWrite` for the plan, `Agent`
  for sub-sessions). The plan has due times and notes and is a duty's to-do list, and a
  sub-session is a conversation the user sees: the same name would promise behaviour
  these do not have.

## Consequences

- The model meets tools it knows, on every provider: the names are plain function names.
- Kadence's tool names mix Claude Code's (`Bash`, `Read`) with its own snake_case ones
  (`update_plan`, `memory`).
- Saved permission answers and hooks that name an old tool no longer match it; with no
  migration, they are the user's to re-make. Conversations from before keep calls under
  the old names in their history, as calls of tools no longer offered.
- `Read`'s numbers cost a few characters per line, and a pattern like `*.py` in `Glob` no
  longer reaches into subfolders.
