# Agent Note: A desktop pet

Status: proposed — a small animated chibi of the user's avatar on the desktop, showing what Kaden is doing; not urgent, and waits on art the user makes

## Problem

The user wants Kaden to have a visible presence outside its window: a cute 2D anime chibi
of their avatar (long orange hair, amber eyes, an orange flower with a black ribbon, a
white capelet dress) living on the desktop, as a desktop pet does. Today Kaden is either
its window or a tray icon, and when the window is closed nothing says whether it is
thinking, working, waiting on the user or resting.

Two earlier attempts set the bounds. A mascot peeking on the new chat page was taken out
(2026-11-15) as unprofessional inside a work window, and art drawn in code was not good
enough. So the pet lives outside the main window, and its frames come from an image
model, which the user makes.

## Proposal

- **A window of its own**, `frontends/desktop/pet.py`: frameless, translucent
  (`WA_TranslucentBackground`, as the main window already is in `frame.py`), a tool
  window so it has no taskbar entry, with the stay-on-top hint. It shows one sprite at
  about 180 px tall (logical pixels), animated by a `QTimer` stepping through the frames
  of the current state.
- **Off by default.** A switch in Settings, "Show Kaden on the desktop", stored with the
  look (`save_look`). It shows whether the main window is open or closed, while the app
  runs.
- **Interaction, three things only.** Left click opens Kaden's window (what the tray's
  click does). Dragging moves the pet (`startSystemMove`, which works on Wayland, as the
  main window's frame uses it). Right click gives "Open Kaden" and "Hide" (which turns
  the switch off).
- **Clicks go through the empty parts.** The window's mask is the union of the current
  state's frames' opaque pixels (computed once when a sheet loads), so a click beside the
  hair reaches the window under it. On Wayland Qt applies a mask as the input region
  only, which is exactly what this needs, since translucency already does the visual side.
- **States follow events the desktop already gets.** A pure function maps them:

  | Pet state  | When |
  |------------|------|
  | `idle`     | `StateChanged(IDLE)` with nothing due soon, `STOPPED`, `FAILED`, `NOT_STARTED` |
  | `thinking` | `THINKING`, `FINISHING` |
  | `working`  | `RUNNING_TOOLS` |
  | `asking`   | `AWAITING_PERMISSION` (a card waits on the user) |
  | `talking`  | voice is reading a reply aloud (the speaker plays a sound) |
  | `resting`  | the life is `IDLE` and `DutyScheduled.wake_at` is set: it rests until then |

  `talking` wins over the rest while a sound plays; `asking` wins over `thinking` and
  `working`. The voice mode gains one signal, `playing(bool)`, around `Speaker.play`.
  No tool is known by name: `working` is any running tool.
- **Whose state.** It follows the life while Continuous runs (the one that lives on),
  otherwise the user's chats; a card waiting in either shows `asking`. See open questions.
- **Art is data.** One PNG sprite sheet per state in
  `frontends/desktop/assets/pet/<state>.png`. A missing sheet falls back to `idle`; a
  missing `idle` hides the switch. Frame count is the sheet's width over its height.

### Asset spec (for the user, made with an image model)

- **Format:** PNG, RGBA, transparent background, straight (not premultiplied) alpha. One
  horizontal strip per state, frames side by side, no gaps.
- **Frame size:** 512 × 512 px, square. Displayed at ~180 px, so this stays sharp up to
  2.5× display scaling, and the file stays small.
- **Registration:** the same scale, the character centered, feet on the same baseline
  (about 24 px from the bottom) in every frame of every state, so switching states does
  not make the pet jump.
- **States and frames** (fps is set in code, recorded in `decisions.md`):

  | Sheet           | Frames | fps | What it shows |
  |-----------------|--------|-----|---------------|
  | `idle.png`      | 8      | 6   | standing, breathing, one blink |
  | `thinking.png`  | 8      | 8   | hand on chin, eyes up, a small thought mark |
  | `working.png`   | 8      | 10  | typing on a tiny laptop, or writing |
  | `asking.png`    | 6      | 8   | waving or raising a hand, looking at the viewer |
  | `talking.png`   | 6      | 10  | mouth open and closed, small gestures |
  | `resting.png`   | 8      | 4   | sitting, eyes closed, a slow "z" |

- **Getting frames that match:** image models drift between frames. Generate one
  reference pose first, then each frame from it (image-to-image or a character
  reference), keep the frames that match, and cut the background with rembg's anime
  model, which gave clean edges for the old mascot. Six states, 44 frames in all; fewer
  frames per state are fine (a 4-frame idle loops too).

## Alternatives considered

- **Inside the main window**, as the old mascot was. It was taken out as unprofessional
  in a work window, and it says nothing while the window is closed, which is when the pet
  matters.
- **Animated GIFs played by `QMovie`.** No sprite code at all, but GIF transparency is
  one bit per pixel, so anime hair gets jagged, haloed edges on any background, the
  problem the old mascot needed three cut-outs to fix.
- **A rigged 2D model (Live2D, Spine).** Smooth, interactive motion from one drawing,
  but it needs a rigged model the user does not have and a runtime Kaden does not ship.
  Frames from an image model are what the user can make.
- **Layer shell on Wayland** (`layer-shell-qt`), which pins a surface above windows and
  places it. KDE's library has no Python bindings, so it would need a C++ shim.
- **An animated tray icon.** No new window, but the tray is a few pixels; it is not a pet.

## Acceptance criteria

- With the switch on, the pet shows on the desktop with the window open or closed, and
  is gone with the switch off; the setting survives a restart.
- Its state changes within a frame of each `StateChanged`, `DutyScheduled` and voice
  playback change, by the table above.
- A click on it opens Kaden's window; a drag moves it; a click on its transparent parts
  reaches the window under it (checked on KDE Wayland, X11 and macOS by hand).
- Missing sheets degrade as stated; a sheet whose width is not a multiple of its height
  is refused with a log line, not a crash.

## Risks

- **Wayland ignores the stay-on-top hint and window positions.** KWin decides stacking
  and placement; a client cannot keep itself above or put itself at a spot. The pet opens
  where KWin puts it, and the docs show a KWin window rule ("Keep above", "Remember
  position") matched on its title. On X11 and macOS the hint works.
- **macOS:** reports describe tool windows that hide when the app loses focus, and a gray
  background with frameless translucent tool windows in some Qt 6 versions (Qt bug
  reports and forum threads). Unverified on the current PySide6; checked on a real Mac before it ships
  there.
- **The art.** Consistency between frames is the hard part, and it is the user's work.
  The code is small; most of the effort is frames.
- **Distraction.** An always-moving figure is noise while working: idle animates slowly,
  and `Hide` is one right click.

## Implementation plan

Tests first (`tests/test_desktop_pet.py`, offscreen):
1. `pet_state(...)` returns each row of the table, and the precedence of `talking` and
   `asking`.
2. A sheet of 4 × 512 by 512 loads as 4 frames; a 1000 × 512 sheet is refused; missing
   sheets fall back as stated (sheets made in the test with `QImage`).
3. The mask covers the opaque pixels and nothing else.
4. Clicking the pet calls the window's open; the switch shows and hides it and is saved.

Steps:
1. `pet.py`: `Sheet` (slice, mask), `pet_state`, `Pet(QWidget)`.
2. `voice/mode.py`, `voice/audio.py`: the `playing` signal around `Speaker.play`.
3. `window.py`: create the pet when the switch is on; feed it the chats' and the life's
   state, `DutyScheduled` and `playing`; `save_look` gains `pet`.
4. `settings.py`: the switch, shown only when `idle.png` exists.
5. `docs/using-kaden.md`: the pet, and the KWin rule for Wayland. `decisions.md`: sizes,
   fps, precedence.

Placeholder sheets (flat silhouettes) let the code land before the art; the switch
stays hidden until real sheets are in.

### Open questions for the user

- Should it follow the life only (it is the one living on), the chats only, or both as
  proposed?
- Should the art go in the public repository, or stay in your data folder (`~/.kaden/pet/`)
  as your own?
- Is 180 px about right on your screen, or should its size be a setting?
- Any state to add or drop (e.g. a `sad` one on a failure; left out as not asked)?
