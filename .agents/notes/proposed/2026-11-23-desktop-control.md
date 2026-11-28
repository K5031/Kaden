# Agent Note: Desktop control

Status: proposed — a `computer` tool lets the agent click, type and scroll on the user's desktop, with a screenshot after each action, on Linux (Wayland) and macOS

Related: [seeing the screen](../implemented/2026-11-12-seeing-the-screen.md),
[screenshots on each system](../implemented/2026-11-26-screenshots-on-each-system.md),
[protected paths](../implemented/2026-11-27-protected-paths.md),
[Spectator](2026-11-23-spectator.md).

## Problem

Kaden can see the screen but not act on it: it cannot fill a form in an app with no API,
click through a settings dialog, or try the game it just built. The user wants it to use
the desktop itself, as Claude does with computer use.

What Claude has (checked 2026-11-27 on platform.claude.com and code.claude.com):

- **The API's computer use** is a client toolset the application runs:
  `computer_toolset_20260801`, 17 member tools (`screenshot`, `zoom`, `left_click`,
  `right_click`, `middle_click`, `double_click`, `triple_click`, `left_click_drag`,
  `mouse_move`, `left_mouse_down`, `left_mouse_up`, `cursor_position`, `scroll`, `type`,
  `key`, `hold_key`, `wait`), coordinates in screenshot pixels; older models take
  `computer_20251124`. The application scales screenshots and coordinates itself.
  Anthropic scans what the tool returns for prompt injection.
- **Claude Code and Claude Desktop's computer use** (macOS; Desktop also Windows; not
  Linux): asks per app the first time in a session ("Allow for this session" or
  "Deny"), with view-only access for browsers and trading platforms, click-only for
  terminals and IDEs, full for the rest; hides other apps while it works; shows "Claude is
  using your computer · press Esc to stop"; one session at a time holds the computer.

## Proposal

### One generic tool, Claude's vocabulary

A tool `computer` in the built-in Tools module (`shell_files/control.py`), offered where
both a capture and an input method are found. One `action` argument from a subset of
Claude's member names, so a Claude model meets the words it knows and any provider can
call it as a plain JSON tool:

| action | arguments |
|---|---|
| `left_click`, `right_click`, `double_click` | `coordinate` [x, y] |
| `left_click_drag` | `start_coordinate`, `coordinate` |
| `mouse_move` | `coordinate` |
| `scroll` | `coordinate`, `scroll_direction`, `scroll_amount` |
| `type` | `text` |
| `key` | `text` (xdotool-style: `ctrl+s`, `Return`) |
| `wait` | `duration` (s, at most 10) |

Plus `step_summary` (the tool declares it; "Click Save"). Each action returns a fresh
screenshot, shrunk to at most 1568 px on the long edge; coordinates are in that picture's
pixels and the tool maps them back. `screenshot` stays the way to look first.

### Input on each system

- **Linux (Wayland): the RemoteDesktop portal** (`org.freedesktop.portal.RemoteDesktop`)
  over D-Bus with jeepney, as the screenshot portal is spoken to: a session with
  pointer and keyboard devices and the monitors selected as ScreenCast sources (absolute
  pointer motion is per stream), then `NotifyPointerMotionAbsolute`,
  `NotifyPointerButton`, `NotifyPointerAxisDiscrete`, `NotifyKeyboardKeysym`. With
  `persist_mode` 2 the desktop asks once; the restore token (single-use, a new one per
  start) is kept in the data folder. No libei: the D-Bus methods do what the tool needs.
- **macOS: CoreGraphics events** (`CGEventCreateMouseEvent`,
  `CGEventKeyboardSetUnicodeString` for text, `CGEventPost`) through ctypes, as
  `screen.py` already loads CoreGraphics. `key` combinations (`cmd+s`, `Return`) need a
  table from key names to macOS virtual key codes and modifier flags, since
  `CGEventCreateKeyboardEvent` takes key codes, not names; `AXIsProcessTrusted` is checked first and, when it says no,
  the tool says where the user turns on Accessibility.
- Text: macOS types any character; on Linux, characters without a keysym in the
  current layout (Chinese among them) go through the clipboard and `ctrl+v`.
- X11 sessions and Windows: not offered.

### Using it safely

- **Permission: the runtime's, unchanged.** `computer` is neither read-only nor
  confined, so it asks under every level but Auto; "Allow for this conversation" grants
  the rest of the conversation, as Claude's "Allow for this session" does for an app.
  Under Auto the review sees the call without its `step_summary` (`Runtime._bare`) and
  no screenshot: `left_click [812, 440]` says nothing of whether it is Save or Delete
  account, so the review asks. The first action of a conversation is one prompt, and
  "Allow for this conversation" ends the asking and the reviews for the rest of it: one
  prompt per conversation, as Claude asks once per app per session. No rule of its own
  for this tool. Kaden cannot ask per app: Wayland does not say which app is under a
  click.
- **One holder.** A lock (`fcntl`, `$XDG_RUNTIME_DIR/kaden-computer.lock`, the
  temporary folder on macOS) shared by every Kaden instance, the life and sub-sessions
  included: a second one is told the computer is in use. A Tools module is never told a
  turn ended, so the tool keeps the lock and the portal session from its first action
  until `IDLE_S` (60 s) after its last, and drops both at once when a call is cancelled
  (Stop) or the module closes.
- **Stopping.** The app's Stop stops the turn and cancels the running action. While the
  portal session is open the desktop shows its own remote-control indicator (where it
  has one), from which the user can end it. No "Kaden is using your computer"
  notification of Kaden's: the frontend would have to know this tool by name; one could
  come later from [panels a Tools module declares](2026-11-27-panels-tools-modules-declare.md).
- **On-screen text is not the user's.** The tool's description says so; the existing
  rule on untrusted tool output applies.

## Alternatives considered

- **Anthropic's own toolset, sent by the Anthropic Core.** Claude is trained on it; but
  the Core would have to know a Tools module's tool by name and translate its calls,
  which ties a module to a provider. Possible later through a declared standard on
  `ToolSpec`, if the plain tool proves clumsy for Claude.
- **Claude's 17 members as separate tools.** Matches the toolset exactly, but 17 entries
  in every request for one ability; one tool with `action` is how `computer_20251124`
  was shaped.
- **ydotool** (uinput). Works on any compositor, but needs a root daemon or a udev rule
  and has no absolute pointer position.
- **libei through `ConnectToEIS`.** The newer path, but GNOME has it and KDE's support
  is not confirmed, and it needs a C library binding.
- **xdotool / XTest.** X11 only; the user's desktop is Wayland.
- **Giving the review the latest screenshot**, so it could judge a click under Auto.
  Fewer prompts, but every action would cost a review request carrying a picture, a
  contract change for one tool, and a reviewer reading a screen is open to what the screen
  says.
- **Per-app approval and hiding other apps, as Claude does.** Needs to know the app
  under the pointer and to hide windows, which Wayland does not allow a client.
- **An MCP server for desktop control** (an existing one). No code in Kaden, but none
  found that speaks the portals; the user would install and trust a stranger's server
  with their whole desktop.

## Acceptance criteria

- With a fake input backend: each action sends the right events at coordinates mapped
  from screenshot pixels to the desktop (scaled, multi-monitor offsets), and returns a
  screenshot.
- Without an input method, the tool is not offered; `screenshot` still is.
- A second instance's call while one holds the lock fails with "in use".
- Stop ends the turn and releases the lock and the portal session; without Stop both are
  released `IDLE_S` after the last action.
- Under Auto, the first action of a conversation prompts, and after "Allow for this
  conversation" no later action prompts or is reviewed.
- On KDE (the user's desktop), the portal asks once and not again after a restart.

## Risks

- **Coordinate mapping across monitors and scaling** (fractional scaling, a portal
  screenshot's layout against the ScreenCast streams' positions) is the likeliest bug,
  only checked on a real desktop.
- **KDE's portal behaviour** (asking once with persistence, keysym typing) is from its
  bug tracker and source, not tried.
- **Cost and speed**: a screenshot per action stays in context (~1,500 tokens each).
- **Idle release is a guess**: a model that thinks longer than `IDLE_S` between actions
  opens a new portal session (asking again only where the desktop forgot the token).
- **The life can use it** while the user works and fight them for the mouse; it gets the
  same prompts, which close unanswered after 3 minutes.
- **A wrong click is real**: a delete or a send in another app; no protected paths can
  cover a click.

## Implementation plan

Contract changes: none. The lock and the portal session end by idle time and on
cancel, so no turn-end signal is needed; Spec 16.4 is unchanged.

Files:
- `src/kaden/tools/shell_files/control.py`: `Input` protocol, `find()` per system,
  `PortalInput`, `QuartzInput`, the `computer` tool, the lock.
- `src/kaden/tools/shell_files/screen.py`: shrinking and coordinate scale shared with
  the tool (Pillow; whichever of this and [Spectator](2026-11-23-spectator.md) lands
  first adds the dependency).
- `src/kaden/tools/shell_files/__init__.py`: offer it.
- `src/kaden/frontends/common.py`: its phrase when no `step_summary` ("Click", "Type").
- `docs/using-kaden.md`, `docs/settings.md`: what it can do, how to stop it, the
  portal's and macOS's permission.

Tests first (`tests/test_tools_control.py`): action dispatch to a fake `Input`;
coordinate mapping (scaling, two monitors); keysym against clipboard fallback for text;
the lock across two tool instances, its idle release and release on cancel; key names
to macOS key codes; the portal session against a stand-in portal on a
private bus, as `test_tools_screen.py` does for screenshots.

Steps: the fake-backed tool and mapping; the portal input with a stand-in; macOS input;
the lock, idle release and cancel; docs; then by hand on KDE (and later a Mac).

Open questions for the user:
- Should the life be able to use the computer, or only chats and sub-sessions?
- Is "Allow for this conversation" enough, or should desktop control also be a switch
  in Settings, off until turned on, as Claude's computer use is (`/mcp` in Claude Code)?
  That would be one ability setting, as Spectator's "Watch my screen" is, not the
  per-tool switches [rejected](../rejected/2026-11-22-tools-turned-on-and-off.md): the
  user can judge "use my computer", not a list of tool names. It adds one step before
  the first use; the per-conversation prompt is there either way.
