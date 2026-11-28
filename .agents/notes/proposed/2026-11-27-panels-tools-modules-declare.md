# Agent Note: Panels a Tools module declares

Status: proposed — a Tools module describes panels as data, the runtime carries them as events and in the snapshot, and each frontend draws them natively

## Problem

What a Tools module keeps is visible only through the agent. For example, the Bilibili
account keeps a login, followed channels and an inbox. To see whom Kaden's account
follows, the user has to ask Kaden, which costs a turn and tokens, and in Continuous
interrupts its own time. To stop following a channel, the user asks again. Other apps
let an extension show its state beside the main view: VS Code has tree views
(`contributes.views` and a `TreeDataProvider` whose `onDidChangeTreeData` refreshes
them), and Claude has a side view of its workers. Kaden's frontends know no tool by
name, so a module has no way to show anything of its own.

## Proposal

### Contract (api.py)

- `Panel(id, title, parts)`. `parts` is a sequence of:
  - `PanelText(markdown)`;
  - `PanelRows(rows)`, where each `PanelRow` has `text`, an optional `detail` and
    `buttons`;
  - `PanelProgress(label, fraction | None)`;
  - `PanelButton`.

  A panel is not tied to a call: any module with state to show yields one. Parts are
  added only when a module needs one.
  Every value is plain data, so the headless wire (`frontends/wire.py`) carries it with
  no new code.
- `PanelButton(id, label, tool, arguments, confirm: str | None = None)`. A click runs the
  module's own tool with those arguments. `confirm` is a question the frontend asks
  first, for something that cannot be undone. A button may name only a tool of the same
  module, which the runtime checks, so a panel never makes modules call each other.
- `Tools.panels() -> AsyncIterator[Sequence[Panel]]`, optional, with no panels by default.
  - Each item the module yields replaces all of its panels, and it yields again whenever
    they change.
  - The module decides when, as `onDidChangeTreeData` does: after a login, a follow, an
    inbox poll.
  - The runtime reads it in a task of its own from start to close. A failure is logged and
    costs that module's panels only, as `standing_watches` does.
- The runtime prefixes panel ids with the module's index, so two modules never clash.
  - It emits `PanelsChanged(panels)` (all panels, in module order) when any change, and
    keeps them in `RuntimeSnapshot.panels`, as `ToolTitles` and `tool_titles` work.
  - `RemoteRuntime._applied` follows it, so a sub-session's cached snapshot has its
    child's panels.
- `RuntimeAPI.press(panel_id, button_id)` runs the button's call.
  - **It goes through the permission system as any call does.** RuntimeAPI is also
    driven by programs (headless, the wire, a parent Kaden), so a press is no proof that
    a person clicked. Hooks run before and after it. `_gate` applies everything that does
    not depend on who asked:
    - the DENY policy, the parent's ceiling included, since `_effective_policy` lowers
      the policy to it;
    - the user's never-rules;
    - protected paths.

    What the press replaces is only the question the gate would ask of the person: the
    review under AUTO, or the prompt under stricter levels. The press counts as that
    answer (ALLOW_ONCE, logged `by="press"`), unless a ceiling is set or the call has a
    protected reason. In those two cases the gate runs unchanged: a child Kaden's panel
    gets no shortcut that its agent's calls lack.
  - **Any tool of the module, with any arguments.** The module, installed code, writes
    both, as it writes what its tools do, so a button is no wider than the module's own
    code. The gate above is what keeps a driving program inside the ceiling.
  - It is not added to the transcript: the agent was not part of it, and the module's next
    panel shows the effect.
  - It is logged in the event log (`panel_press`, the tool, the outcome).
  - It returns the `ToolOutput` or the refusal, and a frontend shows an error in a
    notice.
  - **When it can run.** In any state once `start()` has run, as `cancel_operation`.
    Before that it raises `Refusal.NOT_STARTED`, and there are no panels to press then,
    since the runtime reads `Tools.panels()` from `start()` on. After `start()` a
    conversation is always open, and the call runs with its ToolContext, as a call of the
    runtime's own. If a call of the agent's that is not read-only is running on the same
    module, the press waits for it, as such calls wait for each other.
- Panels are per instance, not per conversation: a module's state, such as an account,
  outlives conversations. A module that has per-conversation state yields again when
  `use_folder` tells it a conversation opened.
- **Whose panels a view shows: those of the runtime it shows.**
  - The main window shows its own runtime's panels.
  - The life view (`frontends/life.py`) shows the life `RemoteRuntime`'s, from that
    instance's snapshot and events.
  - A sub-session's view shows its child's.

  A press goes to that runtime. Each instance keeps its own data folder and its own
  Bilibili login (keyed by data folder), so the Bilibili panel appears in the view of the
  instance that is logged in, which in practice is the life.

### Frontends

- **Desktop:** a side pane on the right, hidden when no module has a panel. Each panel is
  a section with its title, drawn with Qt widgets (labels, a list, a progress bar,
  buttons). A button with `confirm` uses the existing `confirm()` dialog.
- **Headless:** the event and the command come through the wire as they are.

### First user: the Bilibili account

One panel, "Bilibili":
- the account's name and a Log out button (`BiliLogin`, `log_out: true`, with confirm);
- the followed channels, each with Unfollow (`BiliFollow`, `follow: false`);
- the number of unread messages.

The module builds the panel from its own state, without the tools' "since you last
looked" marks, so the panel never uses up what the agent has yet to read.

## Alternatives considered

- **Webviews** (a panel as HTML in a browser engine, as VS Code webviews and MCP Apps'
  `ui://` resources). Any look is possible, but only the desktop has a browser engine,
  and Qt's adds a large dependency. MCP Apps is a stable optional extension (spec
  2026-01-26), but its client support is uneven, with reports of widgets not rendering in
  Claude Desktop and Cursor. It could become a later layer for MCP servers that ship their
  own UI, once it settles.
- **The runtime asks each module for its panels after every cycle**, as with
  `standing_watches`. That adds no push, but a panel would go stale while Kaden is idle,
  when the user is most likely to look, such as a new message during a rest.
- **The module pushes through a callback that the runtime hands it.** This is the same as
  the async iterator but adds a second way in alongside `list_tools` and `execute`. The
  iterator also ends cleanly at close.
- **Buttons send the agent a message** ("Unfollow X") instead of calling a tool. The agent
  learns of it, but a click costs a turn and tokens, and the agent may decline.

## Implementation plan

Contract changes, all in api.py:
- `Panel`, the parts, `PanelButton`;
- `Tools.panels()`;
- `PanelsChanged` in RuntimeEvent;
- `RuntimeSnapshot.panels`;
- `RuntimeAPI.press`;
- SPEC 16 gets a paragraph.

Files:
- `runtime/orchestrator.py`: one panel task per module, `press`, the event log entries;
- `frontends/remote.py`: `_applied`, and `press` over the wire (automatic, since
  `COMMANDS` come from RuntimeAPI);
- `frontends/desktop/panels.py`: a new pane, wired in `window.py`;
- `tools/shell_files/bili_account.py`: the panel;
- `docs/using-kaden.md`;
- `.agents/decisions.md`.

Tests first:
- `tests/test_runtime.py` with a fake module that yields panels:
  - PanelsChanged and the snapshot;
  - ids prefixed per module;
  - a failing iterator costs only that module;
  - `press` runs the tool with no prompt or review and no transcript entry, and is logged
    `by="press"`;
  - `press` is refused under DENY and by a never-rule;
  - with a ceiling set, or with a protected path, the gate runs unchanged (the review or
    a prompt);
  - before and after hooks run on it;
  - before `start()` it raises NOT_STARTED;
  - a button naming another module's tool is refused.
- `tests/test_sub_sessions.py`: the cached snapshot follows the event.
- `tests/test_bili_account.py`:
  - the panel after login and after a follow;
  - Unfollow through `press`;
  - the unread marks untouched.
- `tests/test_desktop_window.py`:
  - the pane appears with a panel;
  - a button calls `press` on the runtime of the view shown (the window's own, or the
    life's);
  - confirm asks first.

Steps:
1. The contract and the runtime, with their tests.
2. The remote snapshot.
3. The Bilibili panel.
4. The desktop pane.
5. The docs.

Open questions for the user:
- Should a button press be told to the agent? For example, a notice at its next turn: "The
  user stopped following X from the panel." Recommended: no, as Claude Code's agent is not
  told of what the user does in a side view, but Kaden's life may wonder why a channel
  went.
- Sub-sessions' workers already show in the desktop sidebar (`SubSessions.listeners`).
  Should they get a panel too, for headless frontends, or stay as they are?

## Acceptance criteria

- A module's panel appears in the desktop side pane within a second of the module
  yielding it, and updates while Kaden is idle.
- A button runs its module's tool without a prompt or transcript entry, and the panel
  shows the result. The DENY policy, never-rules, a parent's ceiling, protected paths and
  hooks hold for it as for any call.
- A late-attaching frontend sees the panels in the snapshot, and so does a sub-session's
  RemoteRuntime.
- No frontend knows any tool or module by name.

## Risks

- **A panel can mislead.** A button's label is the module's own text over a call the user
  does not see. Modules are installed code, trusted like their tools, and MCP servers get
  no panels in this design. The desktop shows the call in the button's tooltip.
- **Calls that bypass the agent's view.** A button can change what the agent relies on,
  such as unfollowing a channel it watches. The module's state stays the truth, and the
  agent finds out the next time it looks.
- **A noisy module floods events.** The runtime could coalesce a module's yields to one
  per 0.5 s if that shows up in the logs. That is not built until it does.
- **Contract growth.** Five dataclasses, one method, one event and one command. Each is
  needed by the first user, but the parts list will be asked to grow (images for a QR
  code, trees for a fleet). Each part is added when a module needs it.
