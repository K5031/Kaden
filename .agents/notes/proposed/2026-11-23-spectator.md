# Agent Note: Spectator

Status: proposed — the life can keep watching the user's screen while they use it, and comment when it wants to, as someone looking over their shoulder would

Related: [seeing the screen](../implemented/2026-11-12-seeing-the-screen.md),
[standing watches](../implemented/2026-11-25-standing-watches.md),
[Continuous](../implemented/2026-11-23-continuous.md),
[desktop control](2026-11-23-desktop-control.md).

## Problem

The user wants the life to be there while they use the computer: see what they are doing
(a game, a video, code) and say something when it has something to say, as a friend
sitting beside them would. The `screenshot` tool looks only when the user asks, and the
life is woken only by messages, its follows, finished work and times it chose; nothing
it can see tells it what the user is doing.

Two earlier choices stand against this and have to hold:

- [Continuous](../implemented/2026-11-23-continuous.md) declined the life seeing the
  user's chats: it would feel watched.
- The todo's "following more kinds of things" says plain facts about the world may come
  with a wake, but what the user is doing does not.

Both are about the life learning what the user does without the user choosing it.
Spectator is the user choosing it: a switch they turn on, off by default, and while it is
off nothing about what they do reaches the life.

## Proposal

### A standing watch the built-in Tools module keeps

- A watch tool `ScreenWatch` (`ToolSpec.watch`, read-only, deferred) in
  `kaden.tools.shell_files.screen`, beside `screenshot`, offered where a capture is.
- While the user's setting **Watch my screen** (a `ToolSetting` of the built-in module,
  `spectator`, off by default) is on, the module names it among its standing watches
  (`Tools.standing_watches()`, as `BiliWatchMessages` is), not `as_user`: a firing is the
  life's own time, within its daily amount. Turning it off stops naming it; the runtime
  stops the watch.
- Only on the life: a module's standing watches are kept only on a conversation with a
  life, the one kind there is ([no duty mode](../implemented/2026-11-28-no-duty-mode.md)),
  so naming `ScreenWatch` among them is enough.
- The setting shows wherever the module's settings do, as `bilibili_wake` already does:
  in the life's settings (the gear), where it applies, and in the user's Settings, where
  no conversation is a life and it changes nothing.

### When it fires

The tool looks every `LOOK_S` (60 s) and finishes, firing, when the screen changed and
then settled:

1. Each capture is shrunk to a 64 × 36 greyscale thumbnail (Pillow) and compared with
   the thumbnail of the last picture delivered: the mean difference of pixels, 0–1.
2. Above `CHANGE` (0.08) the screen changed; the tool waits one more look and fires only
   if that one is close to the one before (a settled screen, not a video mid-scroll).
3. At most one firing per `GAP_S` (10 min); a change within the gap waits for its end.
4. Locked screen (the portal fails, or `org.freedesktop.ScreenSaver.GetActive` says so)
   and failed captures fire nothing; the usual watch back-off handles repeated failure.

Nothing fires because time passed: an unchanged screen costs captures, not requests.
"Delivered" follows the watch contract: the thumbnail kept for comparison moves only in
`ToolOutput.confirm`.

### What the life gets

- A notice `[Watch w4, ScreenWatch] The user's screen now:` with the picture attached,
  shrunk to at most 1568 px on the long edge (the size providers scale to anyway).
- The life's prompt is unchanged: what wakes it is told by the watch's own notices.
  What it says by itself shows in the chats view as a notice, as today; it may also say
  nothing and rest.

### Privacy

- Off by default; the setting's description says it sends a picture of the whole screen
  to the model provider every few minutes while the screen changes, Kaden's own window
  included.
- **Pause**: the setting in the life's settings, which already shows its tools'
  settings and restarts the life on a change (a few seconds). No toggle of its own:
  a frontend that knew this setting by name would know a tool; a one-click switch waits
  for [panels a Tools module declares](2026-11-27-panels-tools-modules-declare.md).
- No excluded apps: on Wayland the screenshot portal says nothing about which windows are
  on screen, so Kaden cannot leave one out (macOS could, with
  `CGWindowListCopyWindowInfo`; not built until Linux can too).

## Alternatives considered

- **The life takes screenshots when it likes** (the existing tool, allowed for the life).
  No new code, but it would have to wake on a timer to look, which Continuous declined
  (a nudge with nothing to do), and it could not know a change happened.
- **Fixed interval firings** (a picture every 10 minutes). Simpler, but pays a request
  for every unchanged screen and wakes it because time passed.
- **A screen stream through the ScreenCast portal** (PipeWire). The right way to watch
  continuously on Wayland, with one consent for the session; but it needs PipeWire or
  GStreamer bindings with C parts, for a look once a minute that the screenshot portal
  already gives.
- **Change detection by OCR or window titles.** Says what changed, not just that it
  did; window titles are not readable on Wayland, OCR is a heavy dependency.
- **Firings as the user's own messages** (`as_user`). Free of the daily amount, but the
  user watching a film all evening would cost without bound.
- **A toggle in the life's view** for the setting. One click to pause, but the view
  would know the module's setting by name.
- **Leaving Kaden's own window out** while it is in front. Would keep the user's chats
  from the life, but the window's focus is the desktop frontend's and the watch runs in
  the life's process; carrying it there needs a contract change for one case.

## Acceptance criteria

- With the setting off, no capture happens and the life's tools have no `ScreenWatch`.
- Turning it on starts the watch on the life without a turn spent on it; turning it off
  stops it.
- A changed, then settled screen wakes the life once with the picture; an unchanged
  screen or one still moving does not; two changes within `GAP_S` wake it once.
- A firing's picture reaches the model as an image, not as `[attached …]` text.
- A firing counts against the life's daily amount and is held once it is spent.

## Risks

- **Cost.** Each firing is a request with the life's whole context plus a picture
  (~1,500 tokens), and the pictures stay in context until compaction. At one firing per
  10 minutes over an 8-hour day, 48 requests and ~72k tokens of pictures; the daily
  amount caps it. Measure from the logs after a few days before tuning.
- **Everything on screen goes to the provider**, the user's chats with Kaden, a password
  field, a bank page among it.
- **The desktop may ask.** Whether a non-interactive portal screenshot shows a dialog or
  a flash once a minute is the desktop's choice; not checked on KDE or GNOME.
- **Thresholds are guesses**: a terminal scrolling text may fire often, a small chat
  message may not fire at all.

## Implementation plan

Contract changes (`api.py`, Spec 16.4):
- A watch firing keeps its output's attachments in the notice (today `_watch_loop` turns
  them into `attachment_text`). The notice becomes a message with text and images.

Files:
- `src/kaden/tools/shell_files/screen.py`: `ScreenWatch` spec and its loop; thumbnail
  and comparison helpers; downscaling for delivery.
- `src/kaden/tools/shell_files/__init__.py`: the `spectator` ToolSetting;
  `standing_watches()` names `ScreenWatch` while it is on and a capture
  exists.
- `src/kaden/runtime/orchestrator.py`: firing notices carry attachments.
- `.agents/notes/implemented/2026-11-23-continuous.md`: its declined "it sees the
  user's chats" stays, and gains a line that Spectator, the user's own choice, can show
  them when they are on screen; link both ways.
- `pyproject.toml`: Pillow as a direct dependency (already installed through
  python-pptx); whichever of this and [desktop control](2026-11-23-desktop-control.md)
  lands first adds it.
- `docs/concepts.md#continuous` and `docs/settings.md`: the setting and what it sends.

Tests first:
- `tests/test_tools_screen.py`: a fake `Capture` returning scripted PNGs: unchanged →
  no firing; change then settle → one firing; change then still moving → none until it
  settles; two changes within the gap → one; confirm moves the comparison base, a firing
  never confirmed fires again.
- `tests/test_watches.py`: a firing with an image attachment reaches the conversation
  with the image.
- `tests/test_tools_shell_files.py`: setting off → no standing watch, no spec; on →
  named.

Steps: attachments in firings; the watch and its
tests with fakes; the setting; docs and the Continuous note; then a real day on KDE, reading the logs for cost and firing count.

Open questions for the user:
- Is the life the only one who watches, or should a chat be able to watch too (for "tell
  me when the build window shows an error")?
- Is it all right that the life sees Kaden's own window, the chats on it included, when
  it is on screen?
- Ten minutes between comments at most: too often, too rarely?
