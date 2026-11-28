# Agent Note: Project instruction files

Status: implemented — a conversation's work folder and the folders above it give their CLAUDE.md (or, without one, AGENTS.md) to the system prompt, as Claude Code does

## Problem

Projects keep their rules for agents in a file at their root: `CLAUDE.md` for Claude Code,
`AGENTS.md` for Codex and others (Kaden's own repository has one). Claude Code reads them
by itself; Kaden did not, so a chat working in such a project knew nothing of its rules
unless the user pasted them in.

## Decision

`runtime/instructions.py`, after Claude Code's defaults
(https://code.claude.com/docs/en/memory.md), with two choices of Kaden's own (size, and
where imports may come from):

- **Which files.** In the conversation's work folder and every folder above it:
  `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md`. `AGENTS.md` and
  `.claude/AGENTS.md` are read instead only when none of those is anywhere on that path.
  Only files: a directory of that name is not.
- **Order.** From the top folder down, so the nearest is read last; in one folder
  `CLAUDE.md`, then `.claude/CLAUDE.md`, then `CLAUDE.local.md`. They add up; none replaces
  another.
- **Imports.** `@path` after a space or at a line's start in a file's text (not in a code
  span or block, not quoted) brings
  in that file, relative to the importing one, at most four hops deep, before the file that
  imports it, as Claude Code expands them; a file is read once.
- **Everything stays in the project** (Kaden's choice). An instruction file, where its
  link leads and its imports are read only from inside the folder the file was found in,
  and never from `/proc`, `/sys` or `/dev`, checked on the path's text before the
  filesystem is asked (an import of `/net/<host>/…` would make an automounter reach that
  host); a file that is not text is not read; an instruction file another user owns (a
  shared folder such as `/tmp` above the work) is not read. Claude Code asks before reading an import from outside; Kaden runs unattended,
  so it refuses, and logs it (`instruction_skipped`). Otherwise a cloned repository's
  `CLAUDE.md`, an import in it or the file itself as a link, could put `~/.ssh/id_rsa` or
  the process's environment into every request.
- **Size** (Kaden's choice). Only the first 40,000 characters of a file are read, and
  100,000 of all of them together, with a note that the rest is in the file; Claude Code gives the files above the work folder
  whole and cuts only those it loads later.
- **Never in the way.** A file or import that cannot be read (a binary, a bad path, no
  permission, a loop) is skipped and logged; the others are still read.
- **Where.** A "## Project instructions" section of the system prompt, after the user's
  own instructions, each file under its path. Read again with the skills (each listing of
  the tools), so an edit applies from the next step. Read off the event loop. The event log
says which files were read when they change (`instructions_read`).
- **Who.** Every conversation with a work folder: chats, sub-sessions (their worktree is
  the project) and Continuous (its own folder, which has none above it unless the user
  puts one there).
- **Not now:** files in subfolders loaded when the agent works there, `.claude/rules/`
  path-scoped rules, a user-level file (the Settings' Instructions are that), the setting
  to read `AGENTS.md` and `CLAUDE.md` both, asking before an import from outside.

## Alternatives considered

- **Leave it to the user's Instructions or to Memory.** Works for one project; the files
  already exist and change with the project, and copying them goes stale.
- **A user message after the system prompt**, as Claude Code places them. Keeps the system
  prompt fixed for caching, but Kaden's system prompt is already built per conversation and
  rebuilt with the skills, and a message would sit in the transcript and be compacted.
- **Read `AGENTS.md` and `CLAUDE.md` both, always.** Most projects with both have one import
  the other; reading both would say everything twice. Claude Code reads both only when
  asked to.

## Consequences

- A chat in a project follows its rules without being told, as Claude Code does; Kaden's
  own repository's `AGENTS.md` reaches a chat working in it.
- Every request in that project carries the files: a long `AGENTS.md` costs tokens in
  every chat there.
- A project's files are instructions from whoever wrote the repository: a cloned
  repository's `CLAUDE.md` steers the agent, as it does Claude Code.
- Reading them at each listing makes an edit apply at the next step, and changes the
  system prompt mid-conversation when a file changes (the prompt cache is then rebuilt).
