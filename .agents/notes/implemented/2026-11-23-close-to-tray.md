# Agent Note: Close to tray

Status: implemented — closing the window keeps Kaden running in the system tray; Quit quits

## Problem

Closing the desktop window quits Kaden, and with it everything that goes on by itself:
a conversation's duty and background work. The user wants them to go on with the
window closed.

## Decision

- Where the desktop has a system tray (`desktop/tray.py`), closing the window hides it:
  Kaden keeps running. A tray icon (Kaden's) brings it back with a click, and its menu
  has **Open Kaden Chat** and **Quit**.
- **Quit** (☰ and the profile menu, Ctrl+Q, the tray menu) quits, as closing did.
- The first close in a run says so once, as a tray message: Kaden is still running.
- Without a system tray, closing quits as before.

## Alternatives considered

- **Only while a duty runs.** Closing would mean two things depending on what runs
  elsewhere in the app; background work needs Kaden running as much.
- **A setting.** One more choice for something with a clear default; Quit is always there.
- **Minimize instead of hide.** The window stays in the taskbar, which is not "closed".

## Consequences

- Duties and background work go on with the window closed, which is what
  closing to the tray is for.
- A user who expects closing to quit finds Kaden still running: the one-time message and
  the icon say so.
- `MainWindow.close()` no longer means quitting where there is a tray: what ends Kaden
  calls `quit()`.
