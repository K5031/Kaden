"""A project's instruction files, read as Claude Code reads them: CLAUDE.md,
.claude/CLAUDE.md and CLAUDE.local.md in the work folder and every folder above it (the
nearest last), AGENTS.md or .claude/AGENTS.md where there is none of those on the whole
path, and @imports four hops deep, each before the file that imports it.

Choices of Kaden's own: a file is cut at MAX_CHARS and all of them at TOTAL_CHARS; a binary
file is not read; an instruction file another user owns is not read (a shared folder above
the work); and a file, the target of its link and its imports are read only from inside the
folder the instruction file was found in (never /proc, /sys or /dev), checked on the path's
text before the filesystem is asked, so a cloned repository cannot bring the user's keys or
environment into every request, nor make it reach a host of its choosing."""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

CLAUDE_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
AGENTS_FILES = ("AGENTS.md", ".claude/AGENTS.md")
MAX_CHARS = 40_000
TOTAL_CHARS = 100_000
MAX_HOPS = 4
_SYSTEM = (Path("/proc"), Path("/sys"), Path("/dev"))
_CODE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.DOTALL)
_IMPORT = re.compile(r"(?<!\S)@((?:\\ |[^\s`'\"])+)")  # after a space or a line's start
_END = ".,;:!?)]"  # a sentence's end after a path


@dataclass(frozen=True)
class File:
    path: Path
    text: str


@dataclass
class _Reading:
    found: list[File] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    seen: set[Path] = field(default_factory=set)
    chars: int = 0


def read(folder: str | Path | None) -> tuple[list[File], list[str]]:
    """Blocking: the instruction files for work in `folder`, in the order they are read,
    and what was skipped, and why."""
    if folder is None:
        return [], []
    try:
        here = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as e:
        return [], [f"{_short(str(folder))}: {_short(str(e))}"]
    chain = [*reversed(here.parents), here]  # the top folder first

    def present(names: tuple[str, ...]) -> list[tuple[Path, Path]]:
        return [(d / name, d) for d in chain for name in names if _is_file(d / name)]

    reading = _Reading()
    for path, home in present(CLAUDE_FILES) or present(AGENTS_FILES):
        if not _owned(path):
            reading.skipped.append(f"{_short(str(path))}: another user's")
            continue
        _add(path, home, 0, reading)
    return reading.found, list(dict.fromkeys(reading.skipped))


def _owned(path: Path) -> bool:
    """The user's, or root's: not one another user put in a shared folder above the work."""
    if not hasattr(os, "getuid"):
        return True
    try:
        return path.stat().st_uid in (os.getuid(), 0)
    except (OSError, ValueError):
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def _add(path: Path, home: Path, hops: int, reading: _Reading) -> None:
    """`path`'s imports, then `path`: it, its link's target and its imports only from
    inside `home`, the folder its instruction file was found in."""
    named = _short(str(path))
    found, skipped, seen = reading.found, reading.skipped, reading.seen
    if not Path(os.path.normpath(path)).is_relative_to(home):  # before the filesystem is
        skipped.append(f"{named}: outside {home}")                # asked (a network mount)
        return
    if reading.chars >= TOTAL_CHARS:
        skipped.append(f"{named}: past {TOTAL_CHARS} characters for all of them")
        return
    try:
        path = path.resolve()
        if path in seen or not path.is_file():
            return
        if not path.is_relative_to(home) or any(path.is_relative_to(s) for s in _SYSTEM):
            skipped.append(f"{named}: outside {home}")
            return
        seen.add(path)
        with path.open("rb") as file:
            data = file.read(4 * MAX_CHARS + 4)  # enough bytes for MAX_CHARS characters
    except (OSError, RuntimeError, ValueError) as e:
        skipped.append(f"{named}: {_short(str(e))}")
        return
    if b"\0" in data:
        skipped.append(f"{named}: not text")
        return
    text = data.decode("utf-8", errors="replace")
    cut = len(text) > MAX_CHARS
    text = text[:MAX_CHARS]
    if hops < MAX_HOPS:
        for target in _IMPORT.findall(_CODE.sub(" ", text)):
            target = target.rstrip(_END).replace("\\ ", " ")
            try:
                imported = path.parent / Path(target).expanduser()
            except (RuntimeError, ValueError) as e:  # ~user that is no one
                skipped.append(f"@{_short(target)} in {path}: {_short(str(e))}")
                continue
            _add(imported, home, hops + 1, reading)
    room = max(0, TOTAL_CHARS - reading.chars)  # its imports came first
    if len(text) > room:
        text, cut = text[:room], True
    reading.chars += len(text)
    if cut:
        text += f"\n\n[Cut at {MAX_CHARS} characters: the rest is in {path}.]"
    found.append(File(path, text))


def _short(text: str) -> str:
    """A path or an error as the log keeps it: never a file's contents at length."""
    return text if len(text) <= 200 else text[:200] + "…"


def environment(folder: str | Path | None) -> str:
    """Blocking: the system prompt's section on where the work is, as Claude Code's: the
    work folder, whether git tracks it and on which branch, and the platform."""
    if folder is None:
        return ""
    here = Path(folder).expanduser()
    return "\n".join([
        "## Environment",
        f"- Work folder: {here}",
        f"- Git repository: {_git(here)}",
        f"- Platform: {platform.system()} {platform.release()}",
    ])


def _git(folder: Path) -> str:
    """"yes, on branch main", "yes, detached at 0123456", "yes" or "no", from the HEAD of
    the nearest .git at or above the folder (a worktree's or submodule's .git is a file
    naming its git folder); "yes" alone when HEAD says no more (a reftable repository)."""
    try:
        for d in [folder, *folder.resolve().parents]:
            dot = d / ".git"
            if not dot.exists():
                continue
            if dot.is_file():
                text = dot.read_text(encoding="utf-8", errors="replace").strip()
                if not text.startswith("gitdir:"):
                    return "yes"
                dot = (d / text.removeprefix("gitdir:").strip()).resolve()
            head = (dot / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
            branch = head.removeprefix("ref: refs/heads/")
            if head.startswith("ref: refs/heads/") and branch != ".invalid":
                return f"yes, on branch {branch}"
            if re.fullmatch(r"[0-9a-f]{40,64}", head):
                return f"yes, detached at {head[:7]}"
            return "yes"
    except (OSError, RuntimeError, ValueError):
        return "yes"
    return "no"


def prompt(files: list[File]) -> str:
    """The system prompt's section for them; "" without any."""
    if not files:
        return ""
    return "## Project instructions\n\n" + "\n\n".join(
        f"Contents of {f.path}:\n\n{f.text.strip()}" for f in files)
