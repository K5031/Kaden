# Settings

## The settings screen

In the [desktop app](desktop-app.md), choose the gear at the bottom left (or **Ctrl+,**).
Change what you need and choose **Save**; only what you changed is saved.

| Setting | Notes | Applies |
|---|---|---|
| AI provider | Claude, DeepSeek, OpenAI, or another one you installed. | At restart |
| API key | Stored in your system's keychain, never in a file. Leave empty to keep the current one. | At restart |
| Model | The list shows each model's price per million tokens. | At the next step |
| Model for memory upkeep | A cheaper model for summarizing long conversations. | At the next step |
| Spending limit | In US dollars; empty means no limit. | At once |
| What the agent may do | See [Permissions](#permissions). | At the next step |
| Instructions | The system prompt: who the agent is and how it should behave. | At the next step |
| Tools | What the agent can do on your computer. | At restart |

Raising or removing the spending limit, or letting the agent do more, asks you to confirm
first.

## Changes that apply at restart

The AI provider, its key, Tools and tool servers (MCP) are saved at once but
used from the next start of Kaden; until then you keep working as before. The settings
screen says so and offers **Restart now**: Kaden stops what it is doing, as when you quit,
and starts again with the new settings in the same conversation.

After a provider change, the new provider starts with its first model; choose another one
after the restart.

**A different Memory is a different archive.** Your conversations and long-term memory stay
where they are, but are not shown while another Memory is used; they come back when you
switch back. The settings screen asks before saving such a change.

## When Kaden cannot start

If Kaden cannot start with your settings (no API key yet, a provider or module that is no
longer installed, wrong options in `config.toml`), it still opens, with the settings and a
message saying what is wrong. Fix it there and choose **Restart now**. At the first start a
welcome window asks for your API key instead; see
[First start](desktop-app.md#first-start).

## Installed modules

The provider and Tools lists show every module installed with Kaden: its own,
and those of any package you install that provides one. A module is a program that runs
with your permissions, so install only ones you trust; see
[Writing a module](writing-modules.md).

The memory module, where conversations and long-term memory are kept, is not in the
settings screen: another one would be another archive, without your conversations. It is
set with `[memory]` in [config.toml](#configtoml). Kaden comes with two:

- **Notes** (the default): About you and one note per topic, shared by all your
  conversations and saved after each answer. See [Memory](concepts.md#memory).
- **Files per project**, as in Claude Code: each project gets its own memory
  folder, `~/.kaden/projects/<project>/memory/`, with a `MEMORY.md` index and one
  Markdown file per fact. A project is a git repository, so chats in its subfolders and
  worktrees share one memory; a folder outside any repository is its own project. The agent writes them itself while it works, and every
  conversation in that folder sees the index as it was when the conversation started: a
  memory saved or edited meanwhile shows up in the next conversation. The agent reads a
  memory before it changes it and edits it in place. Hidden files (such as `.DS_Store`)
  and files that are not text are not memories and are left out. A conversation gets the first 200
  lines of the index, so the agent is told to condense it when it grows past that. Choose
  it with:

  ```toml
  [memory]
  factory = "kaden.memory.files:factory"
  ```

  It keeps its conversations in `memory-files.db`, so it starts empty; your other
  conversations come back when you remove these lines.

## Permissions

| Level | The agent |
|---|---|
| Decide automatically, ask me only when unsure | Works freely in its work folder; a quick automatic check decides about anything outside it, and asks you when it is unsure (the default) |
| Work freely in the work folder, ask before anything else | Reads and changes files in its work folder and runs commands there without asking; asks before anything outside it |
| Read freely, ask before changes | Reads, lists and searches files anywhere on its own; asks before commands and changes |
| Ask me before every action | Asks before every tool call |
| Never run tools | Can only talk |

**Deciding automatically.** When the agent wants to do something outside its work folder
(run a command outside the sandbox, read a file elsewhere, push to GitHub), Kaden first
asks the model for memory upkeep whether it fits what you asked for. It runs without
asking when it clearly does and is easy to undo, or only reads. Kaden asks you when it
deletes or overwrites something you did not mention, sends data anywhere, publishes,
spends money, changes system settings, or when the check is unsure; the question then
says why. If the check fails or the spending limit is reached, Kaden asks too.

- The check sees your messages and the action, not what the agent read, so a web page or
  a file cannot talk it into allowing something.
- Changing Kaden's own data folder (its conversations, settings and spending limit), a
  shell startup file such as `~/.bashrc`, or git's own files (a `.git` folder,
  `.gitconfig`) never runs unchecked, even in the work folder or after you allowed it:
  the check decides it here, and the other levels ask. Kaden's data folder holds the very
  settings that limit the agent; a startup file or git hook runs commands later. A work
  folder inside the data folder, such as Continuous's home, is not counted. Claude Code
  protects the same kinds of files. Kaden goes by the files an action names: a command
  that changes one without naming it, such as `git config --global`, is not caught.
- Reading keys and passwords (`.env` files, SSH keys, cloud credentials) has no rule of
  its own, as in Claude Code: in the work folder the agent reads them freely, elsewhere
  the check decides, and it asks for keys. Anything the agent reads goes to your AI
  provider, so to keep a file out, use **Never allow** (below).
- Each check is a small request to your provider and counts toward the spending limit. Set
  a cheap **Model for memory upkeep** to keep it cheap.
- It can be wrong. Choose "Work freely in the work folder, ask before anything else" to
  decide everything outside the folder yourself.

**Saved answers.** When Kaden asks, **Always allow** keeps your answer for every
conversation: calls like it run without asking from then on. **Never allow**, under ▾
beside Deny, refuses them without asking. In Continuous's view they are its own answers,
apart from your chats', listed in its settings (the gear in its view). A command is kept by its program (`ls`), or its program and
subcommand for programs that have them (`git status` covers `git status -s` too); a
command that runs what it is given, such as `python fix.py` or `find … -exec`, is kept
whole. For commands joined with `&&`, `;` or `|`, each part is kept (the button names
them: "Always allow “cp” and “ls”"), and such a chain runs without asking only when every
part is allowed. Writing into a file with `>`, and commands with `$(…)`, a newline or a
`#`, are never covered: they are checked each time. Settings → Permissions
lists what you saved, and **Remove** takes one back. An "always" never covers changing
Kaden's own data folder, shell startup files or git's own files. A "never" also refuses a call you
approve from a proposal or a wrap-up card.

**How "in the work folder" is enforced.** A file action counts as inside when its path is
in the work folder; a link that leads out of it counts as outside. Commands run in a
sandbox ([bubblewrap](https://github.com/containers/bubblewrap) on Linux, the system's own
sandbox on macOS) in which they can:

- read and change the work folder;
- read the files you attached to a message, wherever they are, so the agent can copy a
  picture you sent;
- read the system, so installed programs run;
- use the network.

They cannot change anything else, and cannot see your home folder, other programs, your
desktop session (so not your keychain either), the services running on your computer
(Docker, the system bus) or environment variables whose names look like secrets (API
keys, tokens, passwords). Searching the web and reading web pages also count as inside: they touch
nothing on your computer. When a command needs more, for example a tool installed in your home
folder, the agent asks to run it outside the sandbox, and the question says so.

Things to know:

- **Keep secrets out of the work folder.** A command there can send the folder's contents
  anywhere.
- **Without the sandbox, commands ask.** That is the case on Linux without bubblewrap;
  file actions in the work folder still run freely.
- **On macOS, some programs do not run in the sandbox**: those that write only to `/tmp`,
  or need a system service it keeps out. The agent asks to run them outside it.
- **Your home folder is not a work folder.** If the work folder is your home folder, or a
  folder that contains it, nothing counts as inside and every action asks.

## Spending limit

The limit counts everything spent in this data folder, across all conversations, at the
providers' list prices. When it is reached, Kaden stops the current step and refuses new
ones until you raise the limit. The settings screen shows what has been spent so far.

## Where settings live

Everything is in Kaden's data folder, `~/.kaden` (or `--data-dir`, or `$KADEN_DATA_DIR`):

| File | Holds |
|---|---|
| `settings.toml` | What you changed in the settings screen, the chosen modules under `[modules]`, tool servers under `[[mcp_servers]]` (their variables by name only) |
| `config.toml` | Optional hand-written configuration, for advanced use |
| `memory.db` | Your conversations and long-term memory |
| `memory-files.db`, `projects/` | The same, with the files-per-project memory |
| `usage.json` | What has been spent so far; delete it, while Kaden is closed, to start counting from zero. One Kaden cannot read (another version, damaged) stops the first conversation from opening, and is left as it is |
| `usage.json` | What has been spent so far; delete it, while Kaden is closed, to start counting from zero |
| `events.jsonl` | Activity log of what Kaden did and when; it holds no message text, tool input or tool output, and no duty, plan, question, MCP command or permission rule text |

Settings from the screen override `config.toml`, and command-line options override both.
To see the result, with defaults filled in and secrets hidden:

```bash
kaden --show-config
```

## config.toml

For things the settings screen does not cover. Example: DeepSeek with a $5 limit and custom
instructions from a file next to the config:

```toml
[core]
factory = "kaden.core.openai_compat:deepseek_factory"

[runtime]
model = "deepseek-flash"
maintenance_model = "deepseek-flash"
permissions = "auto"                # auto | allow_workspace | allow_read_only | ask | deny
system_prompt_file = "prompt.md"    # or: system_prompt = "..."

[runtime.budget]                    # each limit optional
max_cost_usd = 5.0
max_tokens = 1000000
max_duration_s = 3600               # time spent working
max_tool_calls = 200
```

`[core]`, `[memory]`, `[tools]` and `[frontend]` each take a `factory` and `options`; a
section left out uses Kaden's default. A module chosen in the settings screen replaces the
one named here, keeping these `options` only when it is the same module. Options a module
understands (prices, context windows, base URLs, compaction) are set here only, and apply
at the next start. The output of `kaden --show-config` is a valid `config.toml` to start
from. A file that is not valid stops Kaden with a one-line message naming the mistake; a
module that rejects its options opens the settings with the message instead.

### Hooks

Your own commands, run before or after the agent uses a tool. For example, format code
after every edit, and stop any command that deletes:

```toml
[[hooks]]
when = "after"
tool = "Edit"             # one tool by name (Bash, Read, Write, Edit, Glob, Grep, ...); leave it out for every tool
command = "ruff format ."

[[hooks]]
when = "before"
tool = "Bash"
command = "~/bin/no-deleting.sh"
```

A hook gets the call as JSON on its input (`tool`, `arguments`, `folder`, and after the
call its `result`) and runs in the conversation's folder, as you, outside the sandbox.
When it exits with code **2**, what it wrote to standard error goes to the agent: before a
call, the call is refused with that reason; after one, it is added to the result. Any
other exit code, or a hook still running after a minute, changes nothing. Hooks also run
for a call you approve from a proposal or a wrap-up card. Kaden's own tools (the plan,
background work) run no hooks.

With Claude models that always think (Opus 5.x, Sonnet 5.x, Haiku 5.x), Kaden asks for a
summary of the thinking to be streamed, so you see it under **Thinking…** as it happens.
To change that, set `thinking` in the Core's options for that model, for example
`thinking = { type = "adaptive", display = "omitted" }`.

Titles, summaries and permission reviews are short jobs where thinking adds cost and delay.
To switch it off for those only, leaving the same model thinking when it works:

```toml
[core.options]
maintenance_options = { thinking = { type = "disabled" } }
```

Haiku 5.5, Sonnet 5 and Opus 5 accept this. Opus 5.5, Sonnet 5.5 and Fable cannot switch
thinking off and refuse the request, so use it only with a maintenance model that can.

With Claude, Kaden asks Anthropic to cache each conversation, so every request after the
first pays about a tenth or less for what was already sent. The cache lasts five minutes
after the last request. For work with longer pauses (a conversation with a duty that checks in every half
hour), keep it for an hour instead, at a higher price for the first request:

```toml
[core.options.options]
cache_control = { type = "ephemeral", ttl = "1h" }
```

## API keys

Kaden looks for a key in the environment first (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`,
`OPENAI_API_KEY`), then in the keychain where the settings screen stores it. Keys never go
into Kaden's files or logs. Variables of tool servers are kept in the keychain too, as
`mcp/<server>/<NAME>`.
