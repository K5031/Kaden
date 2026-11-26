"""A headless Kaden child the app hosts in a data folder of its own: Continuous (life.py)
and sub-sessions (sessions.py). Its folder gets the parent's configuration; its process
is driven over its channel (kaden.frontends.remote)."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import tomli_w

from kaden.api import EventEnvelope
from kaden.frontends.remote import Channel, RemoteRuntime

STOP_GRACE_S = 10.0  # after stdin closes, before the child is killed
LINE_LIMIT = 16 * 1024 * 1024  # of one message on its channel


def settings_of(parent_dir: Path, folder: Path) -> dict[str, Any]:
    """Blocking: copy the parent's config.toml into the child's folder, and return the
    parent's settings for the child's (ValueError when they do not read)."""
    folder.mkdir(parents=True, exist_ok=True)
    config = parent_dir / "config.toml"
    if config.exists():
        shutil.copyfile(config, folder / "config.toml")
    return read_settings(parent_dir / "settings.toml")


def read_settings(path: Path) -> dict[str, Any]:
    """Blocking: a settings file, {} when there is none."""
    return tomllib.loads(path.read_text()) if path.exists() else {}


def write_settings(path: Path, settings: Mapping[str, Any]) -> None:
    """Blocking, and whole: never seen half written. Its own temporary file, as the child
    may be saving its settings at the same time."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(tomli_w.dumps(dict(settings)))
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


async def spawn(command: Sequence[str], data_dir: Path, *, cwd: Path,
                conversation_id: str | None = None, env: Mapping[str, str] | None = None,
                on_event: Callable[[EventEnvelope | None], None] | None = None
                ) -> RemoteRuntime:
    """Start `command` as a headless Kaden in `data_dir`, on `conversation_id` if given."""
    args = [*command, "--headless", "--data-dir", str(data_dir)]
    if conversation_id is not None:
        args += ["--conversation", conversation_id]
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=cwd, env=None if env is None else dict(env),
        limit=LINE_LIMIT)
    return RemoteRuntime(Channel(process), on_event)
