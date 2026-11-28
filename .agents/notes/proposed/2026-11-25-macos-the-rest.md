# Agent Note: macOS, the rest

Status: proposed — a first run on a real Mac checks what CI cannot, and the app gets a macOS launcher and its single-window check

## Problem

Kaden's macOS code is written and tested on GitHub's Mac runners
([a sandbox on each system](../implemented/2026-11-26-a-sandbox-on-each-system.md),
[screenshots on each system](../implemented/2026-11-26-screenshots-on-each-system.md)),
but no one has run the app on a Mac. The runners cannot check what a person sees:
permission dialogs, the menu bar, the Dock, sound, the keychain prompt. The tests that open
windows skip there (`conftest.skip_windows_on_mac_ci`), so the desktop app has never run on
macOS at all.

## Proposal

### A first run on a real Mac

A checklist, run once from a source install (`uv sync`, `uv run kaden-desktop`) and once
from a built `.app` (`briefcase build macOS`, unsigned, opened with right-click → Open):

1. **Window and look.** The window opens, draws the frameless frame, moves, resizes and
   goes full screen. The theme follows the system's light or dark setting. The menu bar
   shows the app's name and Quit (⌘Q) works.
2. **Keychain.** Saving a provider key asks for the login keychain once and is read back
   after a restart (`keyring`'s macOS backend).
3. **Shell in the sandbox.** A command in the work folder runs unasked. `cat ~/.zshrc`,
   writing outside the folder and `open -a Calculator` are refused inside the sandbox.
   `git`, `python3`, `npm` and Homebrew's tools run. `sandbox.find()` returned Seatbelt
   (the log says so).
4. **Screenshot.** The first use names System Settings → Privacy & Security → Screen
   Recording. After the permission is given, the picture is the screen, not the wallpaper.
5. **Voice.** The microphone permission dialog appears (from Terminal, or for the `.app`
   once `NSMicrophoneUsageDescription` is in its Info.plist). Speech is heard and
   transcribed, and the reply is spoken. Changing the output device mid-reply moves the
   sound (`voice/audio.py`).
6. **Single window.** A second launch shows the first window instead of a second app
   (`single.py`, a socket under the temporary folder, since macOS has no
   `XDG_RUNTIME_DIR`). Quitting removes the socket, and a crash's leftover socket is
   replaced.
7. **Sessions and Continuous.** A sub-session starts in its own process and folder.
   Continuous wakes after a rest. A tool server (an MCP server started with `npx`) connects.
8. **Window tests.** `uv run pytest` passes in full on the Mac, with the window tests that
   skip on the runner.

What fails becomes a test where it can be one, and a fix.

### A launcher and the single-window check

- **Launcher.** A source install has no app in Launchpad. On Linux `app_menu` writes a
  desktop entry. macOS has no equivalent for a plain script, and the macOS launcher is the
  `.app`. So "Add to app menu" stays Linux only, and the docs say to use the `.app` on a
  Mac. No code.
- **Single window.** `single.py` works as it is on macOS (unix sockets exist). Item 6 above
  checks it; nothing is built for it. The [Windows note](2026-11-25-windows.md) moves
  `single.py` to Qt's `QLocalServer` on every system; if that lands after the first run,
  item 6 is run again on a Mac.
- **Dock icon.** The source install shows Python's rocket in the Dock. Setting the
  application icon (`QApplication.setWindowIcon`) at start is a one-line fix if the first
  run shows the rocket.

### The `.dmg`

The [release workflow](../implemented/2026-11-25-releases.md) builds Linux packages
only. A `.dmg` is a `macos-latest` job in it (`briefcase package macOS`), added once
it can be signed and notarized, which needs the Apple Developer account the user put
off. Unsigned, a download is blocked by Gatekeeper and must be opened by right-click →
Open. Until then the `.dmg` is not built.

## Alternatives considered

- **Drop the macOS job.** The user chose to keep it and skip only the window tests.
- **A rented Mac in the cloud for the first run** (e.g. MacStadium, AWS EC2 Mac). Runs
  everything but cannot show the permission dialogs as a person meets them, and AWS bills
  a Mac host for at least a day (unverified).
- **A macOS launcher for source installs** (an AppleScript applet, or Automator).
  Possible, but it is a second app bundle of Kaden's own beside Briefcase's.

## Acceptance criteria

- Every item of the checklist is ticked on a real Mac, or has an issue with what failed.
- The window tests pass on a real Mac.

## Risks

- The first run depends on the user having, or borrowing, a Mac.
- A Seatbelt profile that works on the runner's macOS version may fail on another. The
  probe makes that fail safe: commands ask.
- Microphone and Screen Recording permissions attach to the program that asks (Terminal
  for a source install), which can confuse a first test.

## Implementation plan

Files: what the first run finds; `docs/` only if it changes something user-facing.

1. The first run on a real Mac, with the checklist; one commit per fix found.

Open questions for the user:
- Do you have a Mac to run the checklist on, or someone who does?
- Should the `.dmg` wait for the Apple Developer account, or ship unsigned with a note on
  right-click → Open?
