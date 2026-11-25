"""Backups of a data folder: everything in it but what can be had again, one a day,
thinned to 7 daily, 4 weekly and 6 monthly backups (grandfather-father-son, as restic's
--keep-daily 7 --keep-weekly 4 --keep-monthly 6). A database (`*.db`) is copied with
SQLite's online backup, so the copy is whole while it is being written. Reads no module's
format. Blocking, except keep_backed_up."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path

BACKUPS = "backups"  # in the data folder
PARTIAL = ".partial-"  # a backup being written; renamed to its date when whole
SKIPPED = {"voice", BACKUPS}  # in the data folder: the speech models, and the backups
SKIPPED_FILES = {"instance.lock"}
SKIPPED_ENDINGS = ("-wal", "-shm", "-journal", ".tmp")  # a database's own files; half-written
KEEP = (("daily", 7), ("weekly", 4), ("monthly", 6))
CHECK_S = 3600.0  # while the app runs: a new day gets its backup within this

logger = logging.getLogger(__name__)


def due(data_dir: Path, today: date | None = None) -> bool:
    """No backup of `today` yet."""
    return not (data_dir / BACKUPS / (today or date.today()).isoformat()).is_dir()


def back_up(data_dir: Path, today: date | None = None) -> Path | None:
    """Back the data folder up as of now, then thin the backups; the backup, or None when
    there already is one of `today`."""
    today = today or date.today()
    backups = data_dir / BACKUPS
    target = backups / today.isoformat()
    if target.is_dir():
        return None
    backups.mkdir(parents=True, exist_ok=True)
    for partial in backups.glob(PARTIAL + "*"):  # one that was cut off
        shutil.rmtree(partial, ignore_errors=True)
    partial = backups / (PARTIAL + today.isoformat())
    _copy(data_dir, partial, top=True)
    partial.rename(target)
    thin(backups)
    return target


def thin(backups: Path) -> None:
    """Delete the backups to_keep does not keep."""
    dated = {}
    for path in backups.iterdir():
        try:
            dated[date.fromisoformat(path.name)] = path
        except ValueError:
            continue  # not a backup
    kept = to_keep(dated)
    for day, path in dated.items():
        if day not in kept:
            shutil.rmtree(path)


def to_keep(days: Iterable[date]) -> set[date]:
    """The newest backup of each of the last 7 days, 4 ISO weeks and 6 months that have
    one; a backup may count for more than one of these."""
    newest_first = sorted(set(days), reverse=True)
    kept: set[date] = set()
    for kind, count in KEEP:
        seen: set[object] = set()
        for day in newest_first:
            key = (day if kind == "daily" else day.isocalendar()[:2] if kind == "weekly"
                   else (day.year, day.month))
            if key in seen:
                continue
            if len(seen) == count:
                break
            seen.add(key)
            kept.add(day)
    return kept


async def keep_backed_up(data_dir: Path, *, every_s: float = CHECK_S,
                         failed: Callable[[str], object] = lambda _why: None) -> None:
    """Back up now if today has no backup, then check again every `every_s` while it
    runs. A failure is told to `failed` once, until a backup succeeds again."""
    told = False
    while True:
        try:
            await asyncio.to_thread(back_up, data_dir)
            told = False
        except Exception as e:  # reported; the next check tries again
            logger.exception("backup of %s failed", data_dir)
            if not told:
                failed(f"Kaden could not back up its data: {e}")
                told = True
        await asyncio.sleep(every_s)


def _copy(source: Path, target: Path, *, top: bool = False) -> None:
    target.mkdir(parents=True)
    for path in sorted(source.iterdir()):
        if (top and path.name in SKIPPED) or path.name in SKIPPED_FILES \
                or path.name.endswith(SKIPPED_ENDINGS) or path.is_symlink():
            continue
        if path.is_dir():
            _copy(path, target / path.name)
        elif path.suffix == ".db":
            _copy_database(path, target / path.name)
        elif path.is_file():
            shutil.copy2(path, target / path.name)


def _copy_database(source: Path, target: Path) -> None:
    """A consistent copy of the database, what is still in its -wal file included; a file
    that is not a database is copied as it is."""
    try:
        db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            copy = sqlite3.connect(target)
            try:
                db.backup(copy)
            finally:
                copy.close()
        finally:
            db.close()
    except sqlite3.DatabaseError:
        target.unlink(missing_ok=True)
        shutil.copy2(source, target)
