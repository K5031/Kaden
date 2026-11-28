# Agent Note: A scripted README demo

Status: proposed — paused by the user (2026-11-27); a script in the repo records the real desktop app working through a scripted task, so the demo can be redone when the UI changes

## Problem

The README shows still screenshots. What sets Kaden apart (steps folding as it works, a
permission card, a summary at the end) only shows in motion. A screen recording made by
hand goes stale with every UI change, shows whatever is in the user's data folder, and
costs model calls each time it is redone.

## Proposal

`scripts/readme_demo.py` records a demo with no display, no model and none of the user's
data, and writes `docs/images/demo-light.gif`, `demo-dark.gif` and `demo.mp4`.

- **The real app, drawn offscreen.** `QT_QPA_PLATFORM=offscreen`, the app styled by
  `theme.apply`, and a real `MainWindow` with its `Controller`, set up in-process as
  `tests/test_desktop_window.py` does (`open_window`). The offscreen platform renders
  widgets and `QWidget.grab()` returns their pixels (checked here with PySide6 on this
  machine), so no Xvfb is needed. The window is 1280 × 800.
- **A stand-in model.** `tests.fakes.core.ScriptedCore` replays the scene's steps, with
  `Step.chunks` and `Step.delay` so replies stream at a readable pace. Its tool calls are
  real calls to the built-in Tools module (`shell_files`), in a temporary work folder, so
  steps show real output. Memory is the real SQLite module in its own temporary data
  folder. Nothing touches `~/.kaden` or a keychain entry.
- **The script drives the window as a person would:** it types into the composer and
  sends, waits for each card or step with the tests' `eventually`, and clicks the card's
  button by its text. A drawn pointer (a small arrow painted onto each frame) moves to a
  button before it is clicked, since an offscreen window has no cursor.
- **Frames.** A `QTimer` grabs the window 25 times a second (the grab has to run on the
  GUI thread); each frame is composited onto a plain backdrop (the window's rounded
  corners are transparent) and handed to a writer thread through a queue. The thread
  writes it as raw BGRA to ffmpeg's standard input (`-f rawvideo`), so no frame files
  pile up and a slow ffmpeg (1280 × 800 at 25 fps is about 100 MB/s) never blocks the
  event loop the scene's timing runs on. A frame that comes late repeats the last one,
  keeping the timing true.
- **Outputs.** The MP4 (H.264, `yuv420p`) at full size, for uploading where the README
  embeds video. GIFs at 960 px wide, 15 fps, encoded in ffmpeg's two passes
  (`palettegen`, then `paletteuse`, in one command with `split`), one per theme for the
  README's `<picture>` light/dark switch.
- **Honesty.** The README says, under the demo, that the replies are scripted and the
  steps are real.

### The scene (about 40 s)

1. The empty new chat. The user types "Make me a little lighthouse game in one HTML
   file" and sends.
2. Thinking streams, then steps run and fold as they finish: read the folder, write
   `lighthouse.html` (a short real game from the script), read it back to check it.
3. Writing the file opens a permission card; the pointer clicks **Always allow …** (the
   button reads "Always allow" and the rule; the script matches it by that prefix).
4. The steps fold into one line ("3 steps"); the summary reply streams.
5. Maybe: the Continuous switch flipped, showing the life's view (open question).

## Alternatives considered

- **A screen recording made by hand** (OBS, Spectacle). Shows the real desktop, but it
  is redone by hand at every UI change, may show the user's data, and costs model calls.
- **Xvfb and a screen recorder.** A real X display, so window decorations and the cursor
  come for free; but this machine has no Xvfb, and it records under X11, not the
  Wayland session users see. The app draws its own frame, so the offscreen grab loses
  nothing that matters.
- **A real model with a fixed prompt.** Real replies, but every take costs money and
  differs, and a replay of a script is what makes it redoable at all.
- **Frames saved as PNG files, joined afterwards.** Simple to debug, but ~1 000 large
  files per take; piping to ffmpeg does the same in one step. Kept as a `--frames` flag
  only if debugging needs it.
- **gifski for the GIF.** Higher quality on gradients, but another tool to install; the
  UI is flat colours, where ffmpeg's palette passes do well.

## Acceptance criteria

- `uv run python scripts/readme_demo.py` makes the three files on Linux with ffmpeg on
  `PATH`, in about the scene's length, touching nothing outside its temporary folders
  and `docs/images/`.
- Two runs give the same scene (the same frames up to timing jitter).
- The GIFs stay small enough for a README (aim: under 8 MB each; GitHub's exact limit for
  repository images is unverified).
- A change to the UI needs no change to the script unless a button's text or the flow
  changes; the script fails clearly (which step it waited for) when it does.

## Risks

- **Fonts offscreen.** The offscreen platform can miss fonts; the frames are checked
  against the app on screen, and the script sets the same font the app uses.
- **Timing.** The grab timer and the scripted streaming share the event loop; a slow
  machine drops frames, which repeat, so motion stutters rather than speeds up.
- **The script ages.** It relies on button texts (the "Always allow" prefix) and the composer;
  a flow change breaks it, loudly.
- **Embedding the MP4.** GitHub plays a video uploaded through its web editor in a
  README (unverified for the current GitHub); the GIF works everywhere.

## Implementation plan

The script is a tool, not a feature: one smoke test (`tests/test_readme_demo.py`) runs
the scene with ffmpeg replaced by a sink that counts frames, and checks it reaches the
summary and writes nothing outside its folders.

Steps:
1. `scripts/readme_demo.py`: set up the runtime (ScriptedCore, shell_files, SQLite
   memory, temporary folders, the `allow_read_only` policy, so reads pass and the write
   asks), the window, the grab timer and the ffmpeg pipe.
2. The scene as data at the top of the script: the user's text, the model's steps, the
   clicks.
3. The pointer overlay and the backdrop.
4. Run it in both themes; add the GIFs to the README with the scripted-replies line.
5. `docs/development.md`: how to redo the demo. `decisions.md`: sizes, fps, the scene.

### Open questions for the user

- Is the scene right, or should it show something else (Continuous, voice, memory)?
- Should the Continuous switch be in it (about 8 s more)?
- GIF in the README with the MP4 linked, or the MP4 embedded with the GIF as fallback?
