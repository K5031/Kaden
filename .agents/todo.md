# To do

What is wanted and not started, for Kaden. A line each; when one is taken up it gets an
Agent Note (`notes/proposed/`) and leaves this list.

## From the user

- **A visual avatar for Kaden** (2026-11-21), not urgent. The user wants a desktop pet:
  a cute 2D anime chibi of their avatar (long orange hair, amber eyes, an orange flower
  with a black ribbon, a white capelet dress) living on the desktop. Art drawn in code was
  not good enough; it needs frames from an image model, which the user would make. Not the
  old mascot.
- **Spectator** (2026-11-23): Kaden keeps viewing the user's desktop as they use
  it and comments when it wants to, as someone looking over their shoulder would.
- **Desktop control** (2026-11-23): Kaden using the desktop itself.
- **Following more kinds of things** (2026-11-24), for Continuous. Built: standing
  watches, a Bilibili channel's new videos (a burst delivered as one wake) and Bilibili
  messages. Next, after a few days of logs: web pages (compare extracted content, not
  HTML), feeds, repositories; Spectator for the screen. Plain facts about the world (time,
  weather) may come with a wake; what the user is doing does not. Declined: a quota on its
  messages to the user, forced topic switches, drives and injected random memories (they
  steer what it thinks). Maybe: an ability to give one thought to the main model when it
  seems worth it.
- **Finding and installing tools** (2026-11-27), as Claude Desktop's directory of
  extensions and connectors: a Browse on Settings → Tool servers listing MCP servers (from
  the official MCP Registry, not a catalogue of Kaden's own), what each does and who
  published it; one-click install (asking for its keys, kept in the keychain as today) and
  remove. Open, for an Agent Note first: what "certified" means, since a server runs with
  the user's permissions (verified publishers only, or a short list Kaden vouches for).
- **macOS, the rest** (2026-11-25). Done: file tools, the Seatbelt sandbox, screenshots,
  a macOS test job. Left: at window startup, reading the saved voice name on a worker
  thread never returns on the Mac job (`test_the_zoom_and_theme_are_written_once_when_the_
  window_closes`); a first run on a real Mac; the app menu and single-window check; later
  a `.dmg` (Briefcase, built on GitHub's Mac runners; signing needs an Apple Developer
  account, put off). No GPU voice (NVIDIA only).
- **Homebrew** (2026-11-27), after macOS works on a real Mac: `brew install
  k5031/kaden/kaden` from a tap of the user's own (`K5031/homebrew-kaden`). First publish
  to PyPI (the name `kaden` is free), then a formula built from it with its Python
  dependencies listed (generated, updated each release). A cask for a `.dmg` later, once
  one is signed (the Apple Developer account is put off by the user's choice).
- **Windows** (2026-11-25), after macOS. Bigger: no POSIX shell (Git Bash, as Claude Code
  requires there), and stopping commands, the folder lock (`fcntl`), the sandbox (nothing
  like bubblewrap), screenshots and paths all need Windows versions. Packages: Briefcase's
  `.msi`, signed against SmartScreen warnings (e.g. Azure's signing service).
