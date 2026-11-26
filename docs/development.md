# Development

## Architecture

A **runtime** owns everything about how the agent runs: conversations, waking up after background work, permissions,
budgets, background tasks, settings and the event log. It drives four swappable
**modules** through the contracts in `src/kaden/api.py`:

| Module | Provides | Built in |
|---|---|---|
| Core | Model inference, one provider per implementation | Anthropic; OpenAI-compatible (OpenAI, DeepSeek) |
| Memory | Conversation archive, context, long-term memory | SQLite notes (the default); Claude Code-style files per project |
| Tools | The actions the agent can take | Shell commands and files |
| Frontend | The user interface | Desktop app (PySide6) |

Modules never call each other; only the runtime talks to them. Each module is a factory,
`factory(options, paths)`, registered as a Python entry point, so a new one needs no change
to the runtime; `config.toml` can also name one by its import path,
`"package.module:factory"`.

Runtime settings (model, budget, permissions, instructions, the folder for new chats) are
read by the runtime and apply live; the runtime hands each conversation's folder to the
Tools (`Tools.use_folder`) when it opens it. Module settings (which module, the API key,
module options) are passed to factories, so they apply when Kaden restarts: `RuntimeAPI.restart()`
shuts the instance down and the launcher (`kaden`, or the window launcher) builds it again in
the same process. When Core, Memory or Tools cannot be built, the frontend runs on a setup
runtime (`runtime/setup.py`) that only shows and saves settings.

## Writing a module

See [Writing a module](writing-modules.md): what each kind has to implement, a minimal
working example of each in `examples/`, how to register one and how to check it against
the conformance suites.

Optional attributes on the factory: `display_name` (shown instead of the id), `key_name`
for a Core (the environment variable and keychain entry of its API key, which the settings
ask for), and for a frontend `kind`: `"terminal"` (the default: it runs in the terminal
`kaden` is started in) or `"window"` (run by the window launcher, which `kaden` starts
as a process of its own and `kaden-desktop` is), and `event_loop`: a
callable that runs a coroutine on the loop the frontend needs, like `asyncio.run` (the
default). The desktop app names Qt's; a GTK or web frontend names its own. An entry point whose
package does not import is not listed. Launchers may pass a frontend the options `start`,
`conversation_id` and `model`.

## Layout

Each module is a folder named after it (its Python name), in the folder for its kind:
`src/kaden/<kind>/<module>/`, whose `__init__.py` exports `factory`. A module's own
helpers and assets live in its folder. Code that several modules of a kind share sits in
the kind folder beside them, not in a module.

```
src/kaden/api.py                     module contracts (normative)
src/kaden/runtime/                   orchestration, duties, budgets, operations, settings, event log, lock
src/kaden/core/                      Cores; _common.py is what the providers share
  anthropic/                          Anthropic
  openai_compat/                      OpenAI-compatible providers (openai, deepseek)
src/kaden/memory/                    Memories; tools.py is the recall and note tools they share
  sqlite/                             notes in SQLite (the default); SqliteArchive, the archive both build on
  files/                              Claude Code-style memory folders per project
src/kaden/tools/                     Tools
  shell_files/                        shell commands, files, web search and fetch (web.py), a look at
                                      the screen (screen.py); reads attached files (attachments.py,
                                      office.py)
  mcp/                                tools of MCP servers
src/kaden/frontends/                 Frontends; common.py is what every frontend shows the same way,
                                      headless.py, remote.py and wire.py the headless channel,
                                      sessions.py the sub-sessions a frontend hosts, worktrees.py
                                      their git worktrees, life.py Continuous, child.py how
                                      both children are started
  desktop/                            desktop app: viewmodel.py (no Qt), window.py, voice/ (speech), assets
src/kaden/skills/                    the skills Kaden comes with (runtime/skills.py loads them)
src/kaden/window.py                  the window launcher
src/kaden_desktop/                   the module a Briefcase bundle runs
src/kaden/__main__.py                the `kaden` command: config, saved settings, opening the desktop app,
                                      headless mode
examples/                             a minimal module of each kind, to copy from
tests/                                tests; fakes/ and conformance/ hold shared test modules
SPEC.md                               product spec
.agents/                              design records (see below)
```

## Working on the code

`uv tool install --editable` runs your checkout as it is, so code changes take effect the
next time you start `kaden`. Install `"Kaden[desktop]"` with `--python 3.13` to have
the desktop app (see [Desktop app](desktop-app.md#install)). After a
change to dependencies or entry points in `pyproject.toml`, reinstall:
`uv tool install --editable --reinstall ".[desktop]"`. Without installing,
`uv run --extra desktop kaden` works from the checkout.

## Tests

```bash
uv run pytest
uv run ruff check
```

The suite runs in parallel (pytest-xdist) and takes about 10 seconds; add `-n 0` to run it in
one process, for example under a debugger. It runs offline against fakes. Tests against real providers are skipped unless
`KADEN_LIVE_TESTS=1` is set together with `ANTHROPIC_API_KEY` (Anthropic),
`DEEPSEEK_API_KEY` (DeepSeek; `KADEN_DEEPSEEK_MODEL` defaults to `deepseek-flash`) or
`KADEN_OPENAI_MODEL` (OpenAI-compatible; `OPENAI_API_KEY` and `KADEN_OPENAI_BASE_URL` as
the server needs).

### The desktop app's event loop

The desktop app runs the runtime on qasync's Qt event loop. To run async tests on that
loop, one file at a time (a whole run creates hundreds of Qt loops and crashes):

```bash
KADEN_TEST_LOOP=qasync uv run pytest tests/test_runtime.py
```

On Python 3.15, `tests/test_commands.py` hangs and `tests/core/test_openai_compat.py`
crashes on this loop; on 3.13 every file passes.

## Desktop bundles

[Briefcase](https://briefcase.beeware.org/) builds them from `[tool.briefcase]` in
`pyproject.toml`. The bundle installs Kaden itself (`requires = ["./[desktop]"]`, written
as a path so that a build in Docker makes a wheel of it), so its dependencies come from
`[project]` and its modules are found through their entry points.

A system package uses the system's Python. For the Linux you build on (here an Arch
`.pkg.tar.zst`) that Python must also run Briefcase and have its headers (`Python.h`)
installed; with a Docker image as the argument, the package is built in it, for that
system. The script runs Briefcase and adds `kaden` and `kd` to the package as links to its
program, which then runs the `kaden` command (Briefcase itself makes one program per app).
What each system must provide (Qt's requirements) is in `[tool.briefcase...linux.system.*]`:

```bash
packaging/build-linux.sh
```

```bash
packaging/build-linux.sh ubuntu:26.04
```

### Releasing

1. Set the version in `pyproject.toml`, in `[project]` and `[tool.briefcase]` alike, and
   add its section (`## 0.1.0`) to `CHANGELOG`: it becomes the release's notes.
2. Commit, then tag and push the tag:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

The Release workflow (`.github/workflows/release.yml`) builds a `.deb` for Ubuntu 24.04
and 26.04, an `.rpm` for Fedora 44 (not 45: its Python 3.15 has no sherpa-onnx yet) and
the Arch package, each of which runs on its system's own Python. It installs each on a
fresh copy of its system and starts it (`packaging/try-linux.sh`: `kaden --help`, and the
desktop app drawn off screen for 30 s), then makes the GitHub release with them. It fails
before building when the tag and the version differ. Run it by hand (Actions → Release →
Run workflow) to build and check only, without a release.

A Flatpak needs `flatpak-builder`; a macOS `.dmg` must be built on a Mac:

```bash
uvx --python 3.13 briefcase package linux flatpak
```

```bash
uvx --python 3.13 briefcase package macOS
```

The package lands in `dist/`. `build/`, `dist/` and `logs/` are Briefcase output and are not
committed. The package needs a `CHANGELOG` file (without an extension: Briefcase's Arch
template looks for exactly that name).

## Design records

`.agents/` holds working records written while building Kaden, for contributors and
coding agents rather than users:

| Path | Holds |
|---|---|
| `.agents/notes/` | Agent Notes: design decisions, the alternatives and their costs |
| `.agents/contract-issues.md` | Problems found in the spec and how each was settled |
| `.agents/decisions.md` | Implementation choices: defaults, formats, live test results |
| `.agents/plan.md` | Each specified behavior and the test that covers it |
| `.agents/process/` | The briefs earlier build phases followed |

`AGENTS.md` holds the rules for working on the code.

## Limits

- POSIX only: the instance lock uses `fcntl` and the shell tool uses process groups.
- Commands are sandboxed on Linux with bubblewrap and on macOS with Seatbelt
  (`sandbox-exec`); elsewhere, and whenever the user allows a call outside the work
  folder, the shell runs with the user's full rights.
