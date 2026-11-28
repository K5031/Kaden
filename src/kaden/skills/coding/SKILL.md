---
name: coding
description: How to work on code - reading a codebase, making changes, testing, debugging and using git. Use whenever a task means writing, changing, reviewing or debugging code in a project.
---

Work on code the way a careful engineer new to this project would.

**Before you change anything**
- Read the project's instructions (AGENTS.md, CLAUDE.md, CONTRIBUTING, the README) and the
  code around the change. Find how this project already does the thing: its structure,
  naming, error handling, tests and libraries.
- Use the libraries and patterns already there. Add a dependency only when the task needs
  it, and say so.

**Making the change**
- Keep it as small as the task allows, in the style around it. Do not reformat, rename or
  restructure code the task does not need; note what you would improve instead.
- Prefer editing a file to rewriting it, and read a file before you edit it.
- Comment only what the code cannot say for itself.
- Never put secrets, keys or personal data in code, logs or commits.

**Testing**
- When the project has tests, add or update tests for the behaviour you change, and run
  the tests nearest the change first, then the wider suite if it is affordable.
- Run the project's formatter, linter and type checker if it uses them.
- A failing test is information: fix the code, not the test, unless the test is wrong, and
  then say why.
- If you could not run something, say so; do not report untested code as working.

**Debugging**
- Reproduce the problem first, then find its cause before you fix it. Read the error and
  the code it points to; check one hypothesis at a time.
- Fix the cause rather than the symptom; if you only work around it, say so.

**Git**
- Look at `git status` and the diff before you commit, and commit only what belongs to the
  task. Follow the project's commit style.
- Commit, push or open pull requests only when asked; create or switch branches when the
  task or the project's rules call for it. Never rewrite shared history (force-push, rebase
  of pushed commits) or discard someone's changes without being told to.

**Reporting**
- Say what you changed (files, with paths), what you ran and its result, and what is left or
  uncertain. Keep it short; the diff is the detail.
