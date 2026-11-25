"""The standing watches of a data folder's conversations (ToolSpec.watch), kept in
watches.json so they start again after a restart. Versioned: a file of another version is
refused. Blocking: run from a worker thread."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FILE = "watches.json"
VERSION = 1


@dataclass
class Watch:
    """`kept`: one a Tools module keeps (Tools.standing_watches), not one the conversation
    started; `as_user`: a kept one whose outcomes are the user's messages."""

    id: str  # w1, w2, ...: what TaskStop takes
    conversation_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    kept: bool = False
    as_user: bool = False


def load(data_dir: Path) -> list[Watch]:
    """The watches kept in `data_dir`; none when there is no file. ValueError for a file of
    another version or that cannot be read."""
    path = data_dir / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if data.get("version") != VERSION:
        raise ValueError(f"{path} is of version {data.get('version')}; Kaden reads {VERSION}")
    return [Watch(**w) for w in data["watches"]]


def save(data_dir: Path, watches: list[Watch]) -> None:
    """Write them whole: a crash leaves the file as it was or as it is now."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=data_dir, prefix=f".{FILE}.")
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump({"version": VERSION, "watches": [asdict(w) for w in watches]}, file)
    os.replace(tmp, data_dir / FILE)


def next_id(watches: list[Watch]) -> str:
    taken = [int(w.id[1:]) for w in watches if w.id[1:].isdigit()]
    return f"w{max(taken, default=0) + 1}"
