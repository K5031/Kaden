"""Git for subagents' worktrees."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psutil

from kaden.frontends import worktrees
from tests.conftest import eventually


async def test_a_cancelled_git_command_does_not_go_on_running(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    running = asyncio.create_task(worktrees.git(tmp_path, "status"))
    [child] = await eventually(lambda: psutil.Process().children(), timeout=10)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
