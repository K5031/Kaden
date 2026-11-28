# Agent Note: Custom themes

Status: implemented — a theme is a JSON file in the data folder's `themes/`, a base (light or dark) and colours over it, as Claude Code's custom themes

## Problem

The desktop app had two looks, light and dark (`theme.LIGHT`, `theme.DARK`), and the code
in replies a set of colours for each (`syntax.colors`). The user wants other looks, their
own or ones others made, without editing Kaden's code.

## Decision

As Claude Code does it (`~/.claude/themes/<slug>.json`):

- **A theme is a file**, `<data dir>/themes/<slug>.json` (`theme.THEMES_DIR`); the file
  name without `.json` is its slug. Fields, all optional: `name` (the slug when left out),
  `base` (`"light"` or `"dark"`, default `"dark"`) and `overrides`, colours by name. A
  name left out keeps the base's.
- **The names** are the app's own: each colour field of `theme.Palette` (`theme.COLOURS`,
  21), and the code colours as `code.<kind>` for each kind in `syntax.colors`. `dark` is
  not a colour: it comes from `base`, and tells icons, Qt's palette and the code colours
  which way the theme leans.
- **Colours** are `#rrggbb` or `#rgb`, kept as `#rrggbb`. `theme.read_themes` skips an
  unknown name, a colour that does not parse, a `base` it does not know or a file that is
  not a JSON object, and lists each in `CustomTheme.problems`, which the theme's menu item
  shows as its tooltip: one typo does not cost the rest.
- **Choosing one**: View → Theme lists System, Light, Dark, then the custom themes by name.
  The choice is saved in `desktop.json` as `custom:<slug>` (`theme.CUSTOM`); a saved slug
  whose file is gone is System. `theme.code_colours()` is the theme in use's code colours
  over the base's; zooming restyles with the theme in use (`theme._style`).
- **Live**: the desktop frontend makes `themes/` and reads it before the saved theme is
  chosen (`window.load_themes`); the window watches the folder and each theme file read
  (`QFileSystemWatcher`). A burst of changes (one save can change a file and the folder)
  starts one read 150 ms on, off the event loop; a read that finishes after a newer one
  is dropped. The new files are then watched too, and the app restyles if the chosen theme
  changed. A `themes/` folder deleted and made again is watched from the next start.
- **Sharing** is the file. `docs/desktop-app.md` lists the names with what each colours.
- Continuous's view uses the window's theme.

## Alternatives considered

- **Qt style sheets (`.qss`) as themes.** Full control, but a theme would depend on Kaden's
  widget names and break when they change, and the custom-painted widgets read the palette,
  not the style sheet.
- **A theme editor in Settings** (Claude Code has "New custom theme…"). Friendlier, but a
  colour picker per name is a feature of its own; a file and live reload come first.
- **VS Code theme files.** Many exist, but their names describe an editor (sidebar,
  activity bar, tokens by TextMate scope); a mapping onto Kaden's names would be guesswork
  for most of them.
- **Themes as an installable kind of module** (a plugin). Kaden's modules are runtime
  parts; a theme is a frontend's look and needs no code.

## Consequences

- Any look is a file away, and themes others make can be shared as files.
- Every colour name is something users rely on: renaming a `Palette` field breaks their
  themes, so the names change rarely and the docs list them.
- A theme with poor contrast makes text unreadable; Kaden does not check contrast.
