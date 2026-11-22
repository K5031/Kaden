# Agent Note: Tools turned on and off

Status: rejected — a switch per tool asks the user to manage what they cannot judge; built, then taken out (the user, 2026-11-22)

## Problem

Every tool a module offers goes to the model. The user asked for a way to select and
deselect tools, so that one never wanted (the shell, a tool server's delete, a paid
search) is not offered.

## Decision

Not built. A Tools page in Settings with a switch per tool was built and taken out again
before it shipped: the user should not have to worry about which tools the agent has.

- The list is of internal names (`read_around`, `forget_note`, a tool server's own
  names) that only a developer can judge.
- What the user wants to prevent is already covered: permissions make the agent ask
  before anything that reaches outside the work folder, and a tool server can be removed
  whole.
- An unused tool costs its description in each request, which caching makes cents.
- A tool turned off quietly breaks what relies on it, with no reason the user can see.

## Alternatives considered

- **A switch per tool in Settings** (`tools_off`, `Settings.tools`, a Tools page): the
  version that was built, and is rejected above.
- **The same setting with no page**, in `settings.toml` only: a hidden setting for the
  same list of names, with the same problem.
- **A permission per tool** (always ask, never): finer than the levels, and the same list
  to manage.

## Consequences

- Should one tool turn out to be expensive or dangerous for everyone, the fix is aimed at
  that tool (a permission, or the module not offering it), not a general list of switches.
