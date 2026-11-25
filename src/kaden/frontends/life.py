"""Continuous: one Kaden that lives its own life, hosted by the app as sub-sessions are.
A headless Kaden process in its own data folder (<data dir>/life), on one conversation
whose duty is a life (DutyKind.LIFE). Its memory, conversations, usage and settings are
its own, so it never sees the user's conversations and they never see its life. The app
starts it with itself; it goes on after a restart on the same conversation. No UI code
here.

What it writes in a turn the user did not start is a message to the user
(`take_messages`); its replies to the user are not.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import tomli_w

from kaden.api import (
    DutyKind,
    EventEnvelope,
    MessageCommitted,
    Role,
    RuntimeSnapshot,
    SettingsUpdate,
    Text,
)
from kaden.frontends.remote import Channel, RemoteRuntime
from kaden.runtime.config import TOOL_FACTS

FOLDER = "life"
RECORD = "life.json"
HOME = "home"  # its own work folder, in its data folder
JOURNAL = "journal.md"  # in its folder: what the user reads of its days, in its view
NAME = "Kaden"  # until the user names it
OWN = ("You live on the user's computer: not their assistant, someone with a life of "
       "your own.")
UNSAID = "Who you are beyond that is yours to find out."  # when the user says no more
# Its base prompt, in place of the work chats' (an assistant's) and the user's
# instructions for them: who it is comes from its life (Life.who).
PROMPT = ("You have a life of your own on this computer, and tools to live it: its shell, "
          f"files, the web. The user can read {JOURNAL} in your folder, as your journal, "
          "where they talk with you.\n" + TOOL_FACTS)
# Its shortest and longest wait between turns. It goes on after the shortest unless it
# chose to rest until a later time (a due time in its plan), held to the longest.
PACE_S = (60.0, 86_400.0)
DAILY_USD = 1.0  # what its own time may cost a day, unless the user says otherwise
STOP_GRACE_S = 10.0


class Life:
    """Starts, keeps and listens to the process its life runs in. `listeners` are called on
    every change (its state, a message for the user)."""

    def __init__(self, data_dir: Path, *, daily_usd: float | None = DAILY_USD,
                 pace_s: tuple[float, float] = PACE_S,
                 command: Sequence[str] = (sys.executable, "-m", "kaden")) -> None:
        self.folder = data_dir / FOLDER
        self.parent_dir = data_dir
        self.daily_usd, self.pace_s, self.command = daily_usd, pace_s, command
        self.listeners: list[Callable[[], None]] = []
        self.replies: list[str] = []  # what it said answering the user, this session
        self._messages: list[str] = []  # what it said by itself, not yet taken
        self._asked = False  # the turn under way was started by the user
        self._live: RemoteRuntime | None = None
        self._starting: asyncio.Task[None] | None = None
        self._own_model: str | None = None  # the user's cheaper model, for its own time
        record = self._read()
        self.name: str = record.get("name", NAME)
        self.about: str = record.get("about", "")  # who it is, in the user's words
        self.conversation_id: str | None = record.get("conversation_id")
        # What its own time may cost a day, as the user set it (None: no limit).
        self.daily_usd: float | None = record.get("daily_usd", self.daily_usd)

    @property
    def runtime(self) -> RemoteRuntime:
        assert self._live is not None, "start() first"
        return self._live

    @property
    def who(self) -> str:
        """Its life's instruction: its name, that its life is its own, and who the user
        says it is."""
        return f"You are {self.name}. {OWN} {self.about or UNSAID}"

    @property
    def running(self) -> bool:
        return self._live is not None

    @property
    def has_lived(self) -> bool:
        """It was started before: the app starts it with itself from then on."""
        return self.conversation_id is not None

    @property
    def snapshot(self) -> RuntimeSnapshot | None:
        live = self._live
        return live.snapshot() if live is not None and live.attached else None

    async def read_journal(self) -> str:
        """Its journal as it is now; "" before it writes one."""
        path = self.folder / HOME / JOURNAL

        def read() -> str:
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return ""
        return await asyncio.to_thread(read)

    def take_messages(self) -> list[str]:
        """What it said to the user by itself since this was last called."""
        messages, self._messages = self._messages, []
        return messages

    async def start(self) -> None:
        """Start its process: a new life the first time, else the same one again. Called
        while it starts, it waits for that start; once it runs, it does nothing."""
        if self._starting is None or (self._starting.done() and self._live is None):
            self._starting = asyncio.create_task(self._start())
        await asyncio.shield(self._starting)

    async def _start(self) -> None:
        await asyncio.to_thread(self._prepare)
        command = [*self.command, "--headless", "--data-dir", str(self.folder)]
        if self.conversation_id is not None:
            command += ["--conversation", self.conversation_id]
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=self.folder / HOME, limit=16 * 1024 * 1024)
        live = self._live = RemoteRuntime(Channel(process), self._event)
        if self.conversation_id is None:
            info = await live.channel.request("start")
            self.conversation_id = info["id"]
            await asyncio.to_thread(self._write)
        # Its life as the app has it now (pace, model, who it is): a new one starts it.
        await live.channel.request("set_duty", duty=self._life())
        await live.attach()
        self._changed()

    async def set_identity(self, name: str, about: str) -> None:
        """Its name and who it is, in the user's words: kept, and its life's instruction
        from then on (at once, if it lives)."""
        self.name, self.about = name.strip() or NAME, about.strip()
        await asyncio.to_thread(self._write)
        if self._live is not None:
            await self._live.channel.request("set_duty", duty=self._life())

    async def set_daily_usd(self, usd: float | None) -> None:
        """What its own time may cost a day (None: no limit): kept, and at once if it
        lives."""
        self.daily_usd = usd
        await asyncio.to_thread(self._write)
        if self._live is not None:
            budget = (await self._live.settings()).budget
            await self._live.update_settings(SettingsUpdate(
                budget=replace(budget, max_cost_usd_per_day=usd)))

    async def restart(self) -> None:
        """Its process closed and started again, on the same life: what was saved in its
        folder (its tool settings) applies."""
        await self.aclose()
        await self.start()

    async def aclose(self) -> None:
        if self._starting is not None and not self._starting.done():
            self._starting.cancel()
        live, self._live = self._live, None
        if live is not None:
            live.detach()
            await live.channel.close(STOP_GRACE_S)

    def _life(self) -> dict[str, Any]:
        return {"instruction": self.who, "kind": DutyKind.LIFE.value,
                "min_interval_s": self.pace_s[0], "max_interval_s": self.pace_s[1],
                "model": self._own_model}

    def _event(self, envelope: EventEnvelope | None) -> None:
        if envelope is not None and isinstance(envelope.event, MessageCommitted):
            message = envelope.event.message
            text = "".join(b.text for b in message.blocks if isinstance(b, Text)).strip()
            if message.role is Role.USER and text:  # who started this turn
                self._asked = message.from_user
            elif message.role is Role.ASSISTANT and text:
                (self.replies if self._asked else self._messages).append(text)
        self._changed()

    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    # ── Its data folder ───────────────────────────────────────────────────

    def _prepare(self) -> None:
        """Its data folder: the app's configuration, its daily amount, its own home."""
        (self.folder / HOME).mkdir(parents=True, exist_ok=True)
        config = self.parent_dir / "config.toml"
        if config.exists():
            shutil.copyfile(config, self.folder / "config.toml")
        settings = _toml(self.parent_dir / "settings.toml")
        # Its tool settings are its own (saved in its folder), not the chats'.
        settings.pop("tool_settings", None)
        if own := _toml(self.folder / "settings.toml").get("tool_settings"):
            settings["tool_settings"] = own
        # Its own time on the model the user chose for upkeep: the cheaper one.
        self._own_model = settings.get("maintenance_model") or None
        settings["budget"] = ({} if self.daily_usd is None
                              else {"max_cost_usd_per_day": self.daily_usd})
        settings["work_folder"] = str(self.folder / HOME)
        settings["system_prompt"] = PROMPT
        (self.folder / "settings.toml").write_text(tomli_w.dumps(settings))

    def _read(self) -> dict[str, Any]:
        path = self.folder / RECORD
        return json.loads(path.read_text()) if path.exists() else {}

    def _write(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / RECORD).write_text(json.dumps(
            {"conversation_id": self.conversation_id, "name": self.name,
             "about": self.about, "daily_usd": self.daily_usd}))


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text()) if path.exists() else {}
