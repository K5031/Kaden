"""Git worktrees for subagents (`Agent` with `isolation: "worktree"`): `git` run as a
subprocess, never on the event loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed, or git is not installed; the message says why."""


@dataclass(frozen=True, slots=True)
class Worktree:
    """A subagent's worktree of `repo`, at `path` on `branch`, started at commit `base`.
    `folder` is its work folder inside it."""

    repo: str
    path: str
    branch: str
    base: str
    folder: str


async def git(folder: Path | str, *args: str) -> str:
    """Its output; GitError with git's own message when it fails."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git", "-C", str(folder), *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as e:
        raise GitError("git is not installed") from e
    out, err = await process.communicate()
    if process.returncode:
        raise GitError(err.decode(errors="replace").strip()
                       or f"git {args[0]} failed")
    return out.decode(errors="replace").strip()


async def plan(folder: Path, name: str) -> Worktree:
    """Where a worktree named `name` for work in `folder` goes, as Claude Code places
    them: <repository>/.claude/worktrees/<name>, on branch worktree-<name> from HEAD.
    GitError when `folder` is not in a git repository with a commit."""
    try:
        repo = Path(await git(folder, "rev-parse", "--show-toplevel"))
    except GitError as e:
        raise GitError(f"{folder} is not in a git repository") from e
    try:
        base = await git(repo, "rev-parse", "--verify", "HEAD")
    except GitError as e:
        raise GitError(f"the git repository {repo} has no commit yet") from e
    path = repo / ".claude" / "worktrees" / name
    inside = folder.resolve().relative_to(repo.resolve())
    return Worktree(str(repo), str(path), f"worktree-{name}", base, str(path / inside))


async def add(worktree: Worktree) -> None:
    """Add it, with its branch when the branch does not exist yet."""
    branch = await git(worktree.repo, "branch", "--list", worktree.branch)
    args = [worktree.branch] if branch else ["-b", worktree.branch, worktree.base]
    await git(worktree.repo, "worktree", "add", "--quiet", worktree.path, *args)


async def changed(worktree: Worktree) -> bool:
    """Whether it has anything git shows: a change, a new file or a commit since it
    started."""
    if await git(worktree.path, "status", "--porcelain"):
        return True
    return await git(worktree.path, "rev-list", "--count", f"{worktree.base}..HEAD") != "0"


async def remove(worktree: Worktree) -> None:
    """Remove it and its branch."""
    await git(worktree.repo, "worktree", "remove", "--force", worktree.path)
    await git(worktree.repo, "branch", "-D", worktree.branch)
