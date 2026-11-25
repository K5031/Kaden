"""Backups of the data folder: the user's chats and Continuous, whole and consistent while
Kaden writes, one a day, thinned to 7 daily, 4 weekly and 6 monthly."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from kaden.runtime import backup


def data_folder(tmp_path: Path) -> Path:
    data = tmp_path / "kaden"
    for path, text in {"settings.toml": "model = 'x'\n", "desktop.json": "{}",
                       "instance.lock": "123\n", "projects/-home-me/memory/MEMORY.md": "- x\n",
                       "life/life.json": '{"name": "Ahri"}', "life/instance.lock": "456\n",
                       "life/home/journal.md": "# Monday\n", "voice/whisper/model.bin": "big",
                       "settings.tmp": "half written"}.items():
        (data / path).parent.mkdir(parents=True, exist_ok=True)
        (data / path).write_text(text)
    return data


def database(path: Path) -> sqlite3.Connection:
    """A database in WAL mode, as Kaden's archives are, with its connection open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("pragma journal_mode=wal")
    db.execute("pragma wal_autocheckpoint=0")  # what is written stays in the -wal file
    db.execute("create table messages (text)")
    db.commit()
    return db


def test_a_backup_is_the_chats_and_the_life_without_what_can_be_had_again(tmp_path):
    data = data_folder(tmp_path)
    made = backup.back_up(data, date(2026, 11, 25))
    assert made == data / "backups" / "2026-11-25"
    copied = {p.relative_to(made).as_posix() for p in made.rglob("*") if p.is_file()}
    assert copied == {"settings.toml", "desktop.json", "projects/-home-me/memory/MEMORY.md",
                      "life/life.json", "life/home/journal.md"}
    assert (made / "life/home/journal.md").read_text() == "# Monday\n"


def test_a_database_is_copied_whole_while_it_is_written(tmp_path):
    """Rows still in the -wal file are in the backup, and the copy needs no -wal of its
    own: a plain file copy of the database would have neither."""
    data = data_folder(tmp_path)
    chats = database(data / "memory-files.db")
    life = database(data / "life" / "memory-files.db")
    chats.execute("insert into messages values ('hi')")
    chats.commit()
    life.execute("insert into messages values ('I found an old map')")
    life.commit()
    assert (data / "memory-files.db-wal").stat().st_size > 0  # not yet in the main file
    made = backup.back_up(data, date(2026, 11, 25))
    assert made is not None
    assert not list(made.rglob("*-wal")) and not list(made.rglob("*-shm"))
    for path, text in (("memory-files.db", "hi"), ("life/memory-files.db", "I found an old map")):
        copy = sqlite3.connect(made / path)
        assert copy.execute("select text from messages").fetchall() == [(text,)]
        assert copy.execute("pragma integrity_check").fetchone() == ("ok",)
        copy.close()
    chats.close()
    life.close()


def test_one_backup_a_day_and_only_whole_ones_count(tmp_path):
    data = data_folder(tmp_path)
    today = date(2026, 11, 25)
    (data / "backups" / ".partial-2026-11-25").mkdir(parents=True)  # one cut off
    assert backup.due(data, today)
    assert backup.back_up(data, today) is not None
    assert not backup.due(data, today)
    assert backup.back_up(data, today) is None  # already made today
    assert backup.due(data, today + timedelta(days=1))
    assert not (data / "backups" / ".partial-2026-11-25").exists()


def test_backups_are_thinned_to_7_daily_4_weekly_6_monthly():
    """As restic's --keep-daily 7 --keep-weekly 4 --keep-monthly 6: the newest of each of
    the last 7 days, 4 weeks and 6 months that have one; one may count for several."""
    days = [date(2026, 1, 1) + timedelta(days=n) for n in range(329)]  # to 2026-11-25
    kept = backup.to_keep(days)
    expected = {date(2026, 11, d) for d in range(19, 26)}  # the last 7 days
    expected |= {date(2026, 11, 15), date(2026, 11, 8)}  # weeks: 25th and 22nd counted already
    expected |= {date(2026, 10, 31), date(2026, 9, 30), date(2026, 8, 31),
                 date(2026, 7, 31), date(2026, 6, 30)}  # months: November's counted already
    assert kept == expected
    # Gaps: the newest of the days, weeks and months there are, however far back.
    sparse = [date(2025, 1, 10), date(2026, 3, 2), date(2026, 3, 3)]
    assert backup.to_keep(sparse) == set(sparse)


def test_thinning_deletes_the_backups_not_kept(tmp_path):
    data = data_folder(tmp_path)
    for n in range(20):
        backup.back_up(data, date(2026, 11, 1) + timedelta(days=n))
    names = sorted(p.name for p in (data / "backups").iterdir())
    assert names == ["2026-11-01", "2026-11-08", "2026-11-14", "2026-11-15", "2026-11-16",
                     "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]


async def test_the_app_keeps_it_backed_up_and_says_when_it_cannot(tmp_path, monkeypatch):
    data = data_folder(tmp_path)
    failures: list[str] = []
    task = asyncio.create_task(backup.keep_backed_up(data, every_s=0.01,
                                                     failed=failures.append))
    for _ in range(200):
        if (data / "backups").exists() and any((data / "backups").iterdir()):
            break
        await asyncio.sleep(0.01)
    assert len([p for p in (data / "backups").iterdir() if not p.name.startswith(".")]) == 1

    def broken(*_args: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(backup, "back_up", broken)
    for _ in range(200):
        if failures:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert failures and "No space left on device" in failures[0]
    await asyncio.sleep(0.05)
    assert len(failures) == 1  # said once, not every hour
