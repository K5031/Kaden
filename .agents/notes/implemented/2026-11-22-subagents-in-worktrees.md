# Agent Note: Subagents in worktrees

Status: implemented — `Agent` takes Claude Code's `isolation: "worktree"`, so a subagent works on its own branch in its own git worktree

## Problem

[Sub-sessions](2026-11-11-sub-sessions.md) all work in the conversation's folder. Two
subagents that edit code, or a subagent and its parent, then edit the same checkout: one's
half-done change breaks the other's tests, and their work cannot be reviewed or dropped
apart. Claude Code solves this with `isolation: "worktree"` on its `Agent` tool, and the
user wants Kadence to match Claude Code.

## Decision

`Agent(description, prompt, run_in_background, budget_usd, isolation)`. With
`isolation: "worktree"`:

- The conversation's folder must be inside a git repository with a commit. If it is not,
  or the conversation has no folder, the call is an error saying so, and no subagent
  starts.
- The manager adds a git worktree of the repository where Claude Code puts them:
  `<repository>/.claude/worktrees/agent-<conversation>-<id>`, on a new branch
  `worktree-agent-<conversation>-<id>` from the repository's `HEAD`. The subagent's work
  folder is the same place inside the worktree as the conversation's folder is inside the
  repository.
- When the subagent's process has quit (it is done, failed or stopped, and no view has it
  open), a worktree with no changes (nothing in `git status`, no commit past where it
  started) is removed with its branch. One with changes is kept.
- The call's result names the worktree's path and branch while it exists, or says it was
  removed; a call that ends waits for that tidying first.
- Starting a subagent's process again (opening, resuming or messaging it) adds a removed
  worktree again, on the same branch name from the same starting commit.
- Deleting a sub-session removes its worktree only when it has no changes: changed code
  is the user's, and is never erased with a session's record.

The git work is `git` run as a subprocess (`kadence.frontends.worktrees`), off the event
loop. Adding, removing and starting a child's process run one at a time per manager, so a
worktree is never removed under a process that is starting in it.

### EnterWorktree and ExitWorktree are not built

Claude Code's `EnterWorktree` moves its own session into a new worktree, and
`ExitWorktree` moves it back. In Kadence a conversation's folder is fixed for its life:
`ConversationInfo.folder` is set when it starts, the runtime hands it to the Tools modules
(`use_folder`) only on opening a conversation, its long-term memory notes and project
skills are found by it, and the desktop app lists conversations under it. Moving a
conversation would change the Memory contract (a folder that can be updated), the runtime
(switching folders between calls) and every frontend, and would split the conversation's
memory notes between two folders. A new conversation can be given a worktree's folder,
and `Agent` with `isolation` keeps parallel work apart.

## Alternatives considered

- **The worktree in the sub-session's data folder.** Kept out of the repository, and gone
  with the sub-session. But deleting a session would then erase the code it wrote, and it
  differs from Claude Code, whose worktrees the user already knows where to find.
- **Keep every worktree,** removing none. Simpler, but each subagent that only read code
  would leave a folder and a branch behind; Claude Code removes those.
- **Remove on `done` only.** A stopped or failed subagent that changed nothing would leave
  its worktree too, for no reason.
- **Build EnterWorktree and ExitWorktree** with a folder that a conversation can change.
  See above: it changes three contracts for what a new conversation already does.

## Consequences

- Subagents can change code in parallel, each on a branch the user can review, merge or
  drop, and the parent's checkout stays as it was.
- `.claude/worktrees/` shows as untracked in a repository that does not ignore it, as in
  Claude Code.
- A worktree starts from the last commit: uncommitted changes in the parent's checkout are
  not in it.
- Git must be installed; without it the call is an error.
- A subagent's result can take up to the child's stop grace (10 s) longer, while its
  process quits and its worktree is checked.
- Kadence's own conversation cannot move into a worktree, as Claude Code's session can.
