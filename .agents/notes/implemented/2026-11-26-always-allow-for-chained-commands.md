# Agent Note: Always allow for chained commands

Status: implemented — "Always allow" on a chained command saves one answer per part, and a chain runs without asking when every part is allowed, as Claude Code does

## Problem

"Always allow" on a shell command saves its program and subcommand (`git status`), so it
covers that kind of command from then on. A chained command (`cp a b && ls -la b`), or
one with a redirection or a substitution, is saved as its whole text: allowing "cp" would
let any chain that starts with cp run, whatever follows it. So the answer covers that
exact string only, which never comes again when the command holds a temporary file name
or a time. The user clicked "Always allow" on two such commands of Continuous's; both
saved answers are dead, and the button's label was the whole command, cut off.

## Decision

What Claude Code documents (code.claude.com/docs/en/permissions) is followed where
Kaden's saved answers overlap. Reading a command is strict: what cannot be read with
certainty is covered by no "always allow" and goes on to the permission level (the work
folder, the automatic check, a question).

### Reading a command

- **Not read at all** (no allow covers it): a command with an unquoted newline, a `#`,
  a backtick, `$(`, `<(`, `>(`, a parenthesis or a here-doc (`<<`) anywhere in its text
  (false positives inside quotes are accepted); one `shlex` cannot read (an unclosed
  quote); one with an operator other than those below.
- **Split** with `shlex` (posix, `punctuation_chars`, `whitespace_split`, no comment
  characters) on the separators `&&`, `||`, `;`, `|`, `|&`, `&`.
- **Redirections:** a part may write only to `/dev/null` (`>`, `>>`, `2>`, `&>`) or join
  its outputs (`2>&1`); any other redirection leaves it uncovered. The answer is about
  the command, not where it writes.
- **A part's program:** leading `time`, `nohup`, `command` (bare), `nice` (bare) and
  `timeout <seconds>` are skipped; a wrapper with other options leaves the part
  uncovered. A part that starts with a variable assignment (`FOO=1 rm …`) is uncovered.

### What an answer covers, and what is saved

- **An allow covers a chain only when it covers every part.** A rule covers a part when
  its words are the part's first words (compared as words, quotes removed).
- **A refusal covers a chain when it covers any part,** split as today (coarsely, on
  every joining character), so `echo $(rm x)` still meets a refusal of `rm`.
- **"Always allow" saves one answer per part not yet covered,** each as a single command
  is saved today: the program and its subcommand (`git status`); the whole part for a
  program that runs code given to it (`bash`, `python`, `eval`, …, now also `find`,
  `awk`, `sed`, `make`, `ssh`, `su`, `doas`, `watch`, `dash`, `fish`, `source`, `.`)
  or whose second word is an option (`git -C x status`). With more than 5 such parts, or
  a command that is not read at all, there is no "Always allow".
- **"Never allow"** saves the whole command for a chain, as today: refusing one part
  forever because of another would surprise.
- **The runtime chooses** the answers to save, once, when it asks: the question carries
  them (`Prompt.details`), the card's button names them ("Always allow cp, echo and
  ls", cut with "…" past 60 characters, all of it in the tooltip) and is not shown when
  there are none. They are saved together, in one settings update under the settings
  lock, each replacing any answer for the same command.
- **Unsandboxed calls:** an allow covers a command whether or not it runs in the
  sandbox, as Claude Code's rules do; a call that asks to leave the sandbox is still
  checked against the policy for leaving it.

Saved answers keep Kaden's format (`Bash: cp`).

## Alternatives considered

- **Not offering "Always allow" for chains.** Honest, but every chained command then
  asks again; Claude Code shows that per-part answers work.
- **`bashlex`** (a full bash parser, GPL-3.0, compatible with the AGPL) for splitting.
  More exact (nested structure, here-docs), but a dependency for what `shlex` with a
  refusal to read anything unusual already makes safe.
- **Allows that skip the sandbox's calls** (an allow from a sandboxed call not covering
  an unsandboxed one). Safer, but not how Claude Code's rules work, and the calls asked
  about are mostly the unsandboxed ones.
- **A built-in list of read-only commands that never ask** (Claude Code's `ls`, `cat`,
  `echo`, …). Kaden lets commands confined to the work folder run, and its automatic
  check judges the rest; a second list is not asked for.

## Consequences

- "Always allow" on a chained command keeps something that comes up again: `cp`, `ls`,
  `echo`, not one string with a temporary file name in it.
- A chain runs unasked only when every part is allowed; a refusal still refuses any part.
- Anything not read with certainty (a newline, a comment, a substitution, a subshell, a
  here-doc, a variable set in front, an unknown operator) is never covered: it is checked
  each time, and its card offers no "Always allow".
- Writing into a file with a redirection is checked each time, even for an allowed
  program; what a program writes through its own arguments (`cp` over any file) is
  allowed with it, as for a single command.
- A plain second word is a subcommand only for programs that have them: "Always allow" on
  `echo x` keeps `echo`, on `cp a b` keeps `cp`.
- `shlex` is not bash: unusual syntax may split differently from the shell; what it
  cannot read is never covered.
