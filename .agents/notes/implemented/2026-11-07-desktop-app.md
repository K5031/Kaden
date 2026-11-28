# Agent Note: Desktop app

Status: implemented — a PySide6 (Qt Widgets) frontend on a qasync event loop, packaged with Briefcase

## Problem

Kaden is meant for people who never open a terminal, and the user chose a desktop app as
the first graphical frontend, before any web frontend
([settings note](2026-11-07-settings-for-non-technical-users.md)). Without one, using Kaden
means installing Python and uv and working from a shell. Settings live in the runtime
(`RuntimeAPI.settings()` / `update_settings()`), so a frontend can offer them; what was
missing was an app a person can open by double-clicking, set up by pasting an API key, and
chat with.

The app has to meet the runtime's rules. The runtime runs on asyncio and must never be
blocked. Late-attach recovery follows the `RuntimeSnapshot` order. Loosening permissions
and raising or removing the spending limit need the user's confirmation first.

## Decision

### Toolkit: PySide6 Qt Widgets on a qasync loop

The app (`kaden.frontends.desktop`) is built on PySide6 (LGPL-3.0) Qt Widgets, without
QtWebEngine. The asyncio loop is qasync's `QEventLoop`, so Qt and the runtime share one
loop on the main thread.

- **One loop, no threads.** `loop.run_on_qt` creates the `QApplication` and runs
  `asyncio.run(main, loop_factory=QtEventLoop)`. Runtime tasks, Core streams,
  `asyncio.to_thread` and the shell tool's subprocesses run as under plain `asyncio.run`.
  Qt signals call into coroutines with `asyncio.create_task`.
- **`QtEventLoop`** is qasync's loop with fixes of Kaden's own (garbage collection on the
  Qt thread, callbacks left for the next run, the `context` argument on Python 3.15);
  `.agents/decisions.md`, Desktop app, says why each is needed.
- **Rich text without a web engine.** `QTextBrowser.setMarkdown` renders Markdown, tables
  and code blocks included, so no QtWebEngine is needed.
- **Dependencies:** the `desktop` extra in `pyproject.toml`: PySide6 (essentials, and
  addons for the voice's audio), `qasync`, `pygments`, and the voice's `sherpa-onnx` and
  `numpy`.

### Launching

A GUI toolkit's loop must be the one that runs, so a frontend's factory may carry an
`event_loop`: a callable that runs a coroutine to completion (`api.Frontend`; the launcher
reads it with `frontend_loop`). The desktop factory's is `run_on_qt`.

- `kaden-desktop` (`kaden.window:main`), the window launcher, reads `config.toml` and
  `settings.toml` with the same `load_config` as `kaden` and runs the saved frontend when
  it is a window frontend, else the desktop app. `kaden` with a display hands over to
  `python -m kaden.window`.
- A Briefcase bundle runs `python -m kaden_desktop`, which calls the same `main`, or the
  command launcher when run as `kaden` or `kd`.
- The Tools start in the folder Kaden is started in, or the saved work folder; each new
  chat is then given a folder of its own
  ([a desktop chat is given its folder](2026-11-14-a-desktop-chat-is-given-its-folder.md)).
- `setQuitOnLastWindowClosed(False)`: the runtime shuts down before the loop stops.
  Closing the window keeps Kaden in the tray ([close to tray](2026-11-23-close-to-tray.md)).
- A second launch shows the window already open (`single.py`).
- A failure to start shows in a dialog, since a double-clicked app has no visible stderr.

### Talking to Kaden

The app is an ordinary `Frontend` with `interactive_permissions=True`. It has two layers:

- **A view model with no toolkit** (`viewmodel.py`): it turns `EventEnvelope`s into
  conversation state (entries, the stream in progress, steps, prompts, status), and holds
  the recovery logic and the settings form. It is tested without a display, against the
  runtime with `ScriptedCore` and `FakeTools`, and is the part a web frontend could reuse.
  Toolkit-free helpers every frontend can use sit in `kaden.frontends.common`:
  permission labels, money formatting, `loosening()`, step sentences.
- **Qt widgets** render that state: `window.py` (the main window, load and save), with
  `widgets.py`, `sidebar.py`, `messages.py`, `prompts.py`, `frame.py`, `composer.py`,
  `conversation.py`, `dialogs.py` and `settings.py` beside it, and `theme.py` for the look;
  among the rest, `loop.py`, `tray.py`, `app_menu.py`, `single.py`, `syntax.py` and
  `voice/`.

A pump task copies `runtime.events()` into an inbox, and one consumer applies envelopes in
`seq` order. Runtime commands run as separate tasks, so a slow `stop()` or
`update_settings()` never freezes the window; their `RuntimeStateError` and `ValueError`
messages appear as inline notices. Dialogs use `open()` and signals, never `exec()`, which
would start a nested event loop inside the runtime's.

- **The transcript.** Replies stream, with Markdown re-rendered at most every 100 ms and at
  once on commit, and reasoning in a collapsed "Thinking" section. `ModelRetry` clears the
  partial reply. A queued message shows dimmed until its commit. Steps fold into groups
  with a plain sentence each ([step descriptions](2026-11-10-step-descriptions.md)); a
  running step has Cancel (`cancel_operation`). Compaction shows "Compacting
  conversation…", then "Conversation compacted". Failures show as banners; a budget
  failure points to Settings.
- **Prompts.** Permissions, questions, plans and proposals are inline cards settled by one
  command ([one way to answer the agent](2026-11-22-one-way-to-answer-the-agent.md)).
- **Busy and stop.** "Thinking…" shows while the agent works; Send becomes Stop (Esc)
  while a cycle runs, and a message typed meanwhile is queued.
- **Recovery** runs at startup and on any `seq` gap, in the `RuntimeSnapshot` order:
  subscribe, take `snapshot()` and drop envelopes up to its `seq`, load the newest 100
  messages of `history()` (de-duplicated by `Message.id`), then rebuild running steps,
  queued messages and open prompts from the snapshot. "Load earlier messages" pages back
  100 at a time.
- **Conversations.** A sidebar lists conversations, newest first, with New chat; a row
  shows the title, else the first message, else "New chat". At launch the app opens
  `--conversation`, else the most recently updated one, or starts one. The model menu
  lists `list_models()` and calls `select_model`.

### Settings and first run

- **Settings** sends a `SettingsUpdate` with only the changed fields. Before sending, the
  app asks `loosening()` whether the change loosens anything: a permission later in
  `PermissionPolicy` order, a higher spending limit, or removing one. If so, a dialog names
  each loosening, and nothing is sent unless the user confirms.
- **First run** (`needs_welcome`): when Kaden could not start and the chosen provider needs
  a key that no provider has, a three-page welcome opens on the setup runtime: provider
  (with a "Get a key" link), key, then work folder and spending limit. It saves them all,
  then restarts Kaden, which builds the provider with its key.

### Look

Qt's Fusion style with a palette and style sheet from `theme.py`, and a few custom-painted
widgets. One indigo accent (`#5B5BD6` light, `#8C8CF5` dark); the rest neutral gray. Light
and dark follow the system and switch live. Your messages are bubbles on the right; Kaden's
replies are plain text. Only the widget modules and `theme.py` know about the look.

### Packaging

Briefcase builds the bundles (`[tool.briefcase]` in `pyproject.toml`). The release workflow
builds a system package for each Linux target
([releases](2026-11-25-releases.md)); each runs on its distribution's Python. The Flatpak
is configured (Secret Service access for the keychain) but not built. macOS and Windows
have notes of their own:
[macOS, the rest](../proposed/2026-11-25-macos-the-rest.md),
[Windows](../proposed/2026-11-25-windows.md).

## Alternatives considered

- **Toga (BeeWare).** Native widgets, an asyncio loop on the main thread and async
  handlers, so it fits the runtime best. It lost: it is pre-1.0, it has no rich-text or
  Markdown widget (the transcript would be HTML in a `WebView`, needing WebKitGTK on
  Linux), and its widget set is thin for steps and inline prompts. Briefcase, its
  packager, is used anyway.
- **pywebview with an HTML/JS UI.** High web reuse, but `webview.start()` must own the main
  thread, so the runtime would run in a second thread and every event and command would
  cross threads through a JS bridge; Linux needs PyGObject plus WebKit2GTK or QtWebEngine.
- **NiceGUI native mode.** Asyncio-native, and the same code becomes a web app. It serves
  the UI over a local HTTP port, so any local process could drive the runtime: answer
  prompts, loosen permissions (SPEC 3 warns about malicious frontends). It also opens
  pywebview in a separate process.
- **Flet.** Async-native with a web mode, but a Material or Cupertino look, `flet build`
  needs the Flutter SDK, and its 1.0 API was still settling.
- **QtAsyncio instead of qasync.** First-party, but a technical preview without
  subprocesses, sockets or transports, so the Core's HTTP client and the shell tool cannot
  run on it.
- **PySide6 with QtWebEngine for the transcript.** Reusable HTML, but it bundles Chromium,
  which multiplies the bundle size; `QTextBrowser` Markdown covers what a reply needs.
- **The platform's own widget style.** No styling code, but the user found it gray and
  dated for a chat app, and it looks different on every platform.
- **QML (Qt Quick).** Easier chat visuals, but a second UI technology beside Widgets; a
  style sheet reached the approved design with small widget changes.
- **PyQt6.** Same Qt and loop story, but a GPL or commercial license.
- **A React UI in Electron or Tauri, with Kaden as a Python sidecar.** The most polished UI
  for the effort and the same UI for a later web frontend, but the UI cannot call
  `RuntimeAPI` directly: the runtime would be exposed over a local connection that has to
  be secured against other local processes, and the app becomes two processes, two
  languages and two runtimes per bundle. The user chose the single-process app.
- **Tauri or PyTauri.** A tiny shell on system web views, but PyTauri was unmaintained,
  it adds Rust and Node tooling, and bundling Python removes the size advantage.
- **Kivy, Dear PyGui.** GPU-drawn, non-native UIs with weak screen-reader support; Kivy had
  no Python 3.14 wheels.
- **The runtime in a worker thread under any toolkit.** Works with every toolkit, but every
  call and event would cross threads; qasync avoids that.
- **PyInstaller or `pyside6-deploy` (Nuitka).** Both freeze the app but build no signed
  installers, Flatpaks or notarized DMGs.

## Consequences

- One process, one thread, one loop: no cross-thread plumbing, but a slow paint (a very
  long Markdown reply) delays runtime tasks. Throttled re-rendering and paged history keep
  that small.
- qasync has had no release since 0.28.0 and needs Kaden's `QtEventLoop` fixes; the whole
  suite runs on it (`KADEN_TEST_LOOP=qasync`). If it is abandoned it can be vendored
  (BSD-2-Clause), or replaced by QtAsyncio once that supports subprocesses and sockets.
- PySide6 on Python 3.15 breaks lazily imported names read across modules; the app
  resolves the one asyncio needs, and others in the stdlib stay exposed.
- The bundle is large, mostly PySide6 (sizes in [releases](2026-11-25-releases.md)).
- A Linux desktop without a Secret Service cannot keep the key in a keychain; such a user
  must set an environment variable.
- The look is Kaden's own on every platform: it follows the system's font size and light or
  dark, not its accent color, widget shapes or high-contrast themes.
- Web reuse is limited to the view model and `frontends.common`; a web frontend needs its
  own rendering layer.

## Sources (checked 2026-11-07)

- PyPI JSON API for versions, dates and licenses: PySide6, qasync, toga, briefcase,
  pywebview, flet, nicegui, pyinstaller, nuitka.
- QtAsyncio status: https://doc.qt.io/qtforpython-6/PySide6/QtAsyncio/index.html
- qasync: https://github.com/CabbageDevelopment/qasync
- Toga event loop: https://toga.beeware.org/en/stable/reference/api/application/app/
- Briefcase platforms: https://briefcase.beeware.org/en/stable/reference/platforms/
- pywebview threading: https://pywebview.flowrl.com/guide/usage.html
- NiceGUI deployment:
  https://nicegui.io/documentation/section_configuration_deployment
- Flet 1.0: https://flet.dev/blog/flet-1-0
- PyTauri: https://github.com/pytauri/pytauri
