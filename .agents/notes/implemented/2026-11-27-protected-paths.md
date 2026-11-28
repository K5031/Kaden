# Agent Note: Protected paths, as Claude Code has them

Status: implemented — writes to Kaden's own data, shell startup files and git's own files take no shortcut past the review or a prompt; reading secrets is no rule of Kaden's own

Replaces `always_ask` in [automatic permission handling](2026-11-10-auto-permissions.md).

## Problem

A rule that makes a call ask, without a review, whenever any of its arguments mentions a
credential file or Kaden's data folder (`always_ask`) is checked before a saved "always"
and before the automatic review, but not before a conversation grant, the read-only level
or the work folder's shortcut, though the docs say such calls "always" ask
([P9](../../contract-issues.md)). Checking it on those paths too does not work: it matches
words anywhere in the arguments, file contents and search text included, and
the data folder by its bare name, so Continuous (data folder `life`) would ask before
writing "my life" in a file of its own.

The user's rule for Kaden's permissions is that the less it asks, the better, and that
Kaden should do as Claude Code does. Claude Code has no built-in rule for reading secrets
(reading `.env` or `~/.ssh` runs unasked unless the user adds a deny rule), and protects
writes to a fixed list of paths: its own config (`.claude`), shell startup files and git
config. Those writes prompt even when an allow rule matches; in its automatic mode the
classifier decides them.

## Decision

- `review.protected(call, read_only, data_dir, work_folder)` answers why a call that can
  change something touches a protected path, or None.
  - Only calls of tools that are not read-only: a read is never protected.
  - Only what a call names as a place: the arguments whose name holds a word that says
    so (`path`, `file`, `filename`, `dir`, `directory`, `folder`, `cwd`, `target`,
    `destination`, `dest`, `dst`, `src`, `uri`, `to`, `location`), or a command (`command`,
    `cmd`), nested ones too, each string on its own. File contents (`content`,
    `new_source`), queries and messages are not looked at.
  - Protected: Kaden's data folders, its own and those of the instances it was started
    under (Continuous's and a sub-session's sit in the main Kaden's, whose settings,
    spending limit and hooks they must not change), passed down in
    `PROTECTED_FOLDERS_ENV` as the permission ceiling is; each by its paths and by its
    path below the home folder as segments (`.kaden`, `.kaden/life`), never by a plain
    word such as `life`, and without a work folder inside it (Continuous's home);
    shell startup files (`.bashrc`, `.bash_profile`, `.bash_login`, `.bash_aliases`,
    `.bash_logout`, `.profile`, `.zshrc`, `.zshenv`, `.zprofile`, `.zlogin`, `.zlogout`,
    `.envrc`, fish's `config.fish`, `environment.d`); git's own files: for a path, anything
    in a `.git` folder; in a command, what runs commands later (`.git/config`,
    `.git/hooks`), since `ls .git` or `--exclude-dir=.git` only read; and `.gitconfig`,
    `.gitmodules` and `~/.config/git`.
- In `Runtime._gate`, a protected call takes none of the shortcuts: not a saved "always",
  a conversation grant, or the work folder's. Under the automatic level it goes to the
  review, as Claude Code's classifier decides such writes; under the others it asks, with
  the reason on the prompt. A saved "never" still refuses it. The review has no rule of its
  own that asks without reviewing.
- Reading credentials is decided as any other call: a read of `.env`, `~/.ssh` or the
  keychain goes through the work folder's shortcut, the read-only level, or the review,
  whose prompt tells it to ask for keys. The user keeps them out with a "never allow".

## Alternatives considered

- **Check `always_ask` on every path.** What the docs promise; it matches words anywhere
  in the arguments, and it adds prompts the user does not want.
- **Keep secrets asking, outside the work folder only.** Stricter than Claude Code; the
  user chose to match it and to ask less.
- **Protect reads of Kaden's data folder too.** Claude Code does not protect reads of its
  own config; the review still asks for them under the automatic level.

## Consequences

- Fewer prompts: a read of `.env` or `~/.ssh` is never asked about without a check, and
  Continuous's writing about its "life" is never mistaken for Kaden's data.
- A secret the agent reads goes to the AI provider unless the user said "never allow";
  under the automatic level, the review asks for keys outside the work folder.
- A write to `.git`, a startup file or Kaden's data folder inside the work folder is
  reviewed or asked about.
- A command that changes a protected file without naming it (`git commit` writing
  `.git`, `git config --global`) is not caught, as with Claude Code's own rule.
- SPEC.md 16.4, `docs/settings.md` and `.agents/decisions.md` say the same.
