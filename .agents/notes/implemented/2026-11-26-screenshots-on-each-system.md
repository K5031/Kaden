# Agent Note: Screenshots on each system

Status: implemented — the `screenshot` tool captures through the desktop portal on Linux and `screencapture` on macOS

## Problem

[Seeing the screen](2026-11-12-seeing-the-screen.md) captured with KDE's Spectacle, so
the tool existed on KDE alone: not on GNOME or other Wayland desktops, and not on macOS,
which Kaden is meant to run on as well.

## Decision

`screen.find()` is called once when the built-in Tools module is built, off the event
loop, and returns this system's way to capture (a `Capture`: `capture() -> PNG bytes`) or
`None`, in which case the tool is not offered. `sys.platform` appears only there.

- **Linux: the desktop portal** (`org.freedesktop.portal.Screenshot`, xdg-desktop-portal),
  spoken to over the session bus with [jeepney](https://jeepney.readthedocs.io/), a small
  pure-Python D-Bus library that `keyring` already brought in. `Screenshot` is asked with
  `interactive: false`; the answer comes as a `Response` signal on a request object whose
  path is known from the token, so it is listened for before asking. The desktop writes
  the picture to a file and gives its URI; the file was made for this look and is removed
  once read. Stop or the 20 s limit withdraw the request (`Request.Close`). Found when
  the portal answers for its `version` property.
- **macOS: `screencapture -x -t png`** (no shutter sound) into a temporary folder, as
  Spectacle was run. Without the Screen Recording permission macOS returns the wallpaper
  alone, so CoreGraphics' `CGPreflightScreenCaptureAccess` is asked first and, when it
  says no, the tool says where the user turns it on (System Settings → Privacy &
  Security → Screen Recording, for Kaden or the terminal it was started from).

Permission, the result and the size limit are unchanged.

## Alternatives considered

- **Keep Spectacle and add macOS only.** No new dependency, and KDE captures unasked;
  but GNOME and the other desktops keep no tool.
- **Spectacle where it is, the portal elsewhere.** Two Linux paths to test and explain;
  the portal works on KDE as well.
- **A D-Bus binding with C parts (dbus-python, PyGObject).** Need system libraries and a
  compiler or distribution packages; jeepney is pure Python and already installed.
- **Interactive portal screenshots.** The desktop shows a dialog where the user picks an
  area each time, on top of Kaden's own permission.
- **Reading the screen in-process** (Qt, Pillow's ImageGrab). Not possible on Wayland,
  and on macOS it needs the same permission as `screencapture`.

## Consequences

- **The tool exists on GNOME, KDE and the other desktops with a portal, and on macOS.**
- **A desktop may ask.** Whether a non-interactive portal screenshot shows a dialog is
  the desktop's choice: one may ask once, or every time. Not checked on a real desktop
  yet; the portal is tested against a stand-in on a private bus (`tests/test_tools_screen.py`).
- **macOS: not run on a Mac by hand.** `find()` and the permission message are tested
  with fakes; `screencapture`'s arguments and CoreGraphics' check only on a real Mac.
- **One more direct dependency** (`jeepney`), already in every install through `keyring`.
