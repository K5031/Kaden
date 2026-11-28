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
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from kaden.api import (
    PERMISSION_CEILING_ENV,
    Duty,
    DutyKind,
    EventEnvelope,
    MessageCommitted,
    Prompt,
    PromptOpened,
    Refusal,
    Role,
    RuntimeSnapshot,
    RuntimeStateError,
    SettingsUpdate,
    StartOptions,
    Text,
)
from kaden.frontends import child
from kaden.frontends.remote import RemoteRuntime
from kaden.runtime.config import TOOL_FACTS

FOLDER = "life"
RECORD = "life.json"
HOME = "home"  # its own work folder, in its data folder
# A life's conversation never ends, and each wake after the prompt cache has expired sends
# all of it again: what is sent is kept to this many tokens (about two days of it stay
# word for word).
COMPACT_WINDOW = 150_000
NAME = "Kaden"  # until the user names it
OWN = ("You live on the user's computer: not their assistant, someone with a life of "
       "your own.")
UNSAID = "Who you are beyond that is yours to find out."  # when the user says no more
# Its base prompt, in place of the work chats' (an assistant's) and the user's
# instructions for them: who it is comes from its life (Life.who).
PROMPT = ("You have a life of your own on this computer, and tools to live it: its shell, "
          "files, the web. The user can see the files in your folder.\n" + TOOL_FACTS)
# Its shortest wait between turns, and the furthest ahead a time it chooses to wake at may
# be (Rest, a due time in its plan). Nothing else of Kaden's wakes it.
PACE_S = (60.0, 86_400.0)
DAILY_USD = 1.0  # what its own time may cost a day, unless the user says otherwise
# Its settings that the app keeps when it writes the rest from the chats'.
OWN_SETTINGS = ("tool_settings", "always_allow", "never_allow")


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
        self._questions: list[Prompt] = []  # what it waits on the user for, not yet taken
        self._asked = False  # the turn under way was started by the user
        self._live: RemoteRuntime | None = None
        self._starting: asyncio.Task[None] | None = None
        self._closing: set[asyncio.Task[int]] = set()  # processes that ended by themselves
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

    @property
    def home(self) -> Path:
        """Its own folder, its work folder: what it keeps there, the user can see."""
        return self.folder / HOME

    def take_questions(self) -> list[Prompt]:
        """What it began to wait on the user for (a permission, a question) since this was
        last called."""
        questions, self._questions = self._questions, []
        return questions

    def take_messages(self) -> list[str]:
        """What it said to the user by itself since this was last called."""
        messages, self._messages = self._messages, []
        return messages

    async def start(self) -> None:
        """Start its process: a new life the first time, else the same one again. Called
        while it starts, it waits for that start; once it runs, it does nothing."""
        if self._starting is None or (self._starting.done() and self._live is None):
            self._starting = asyncio.create_task(self._start())
        starting = self._starting
        try:
            await asyncio.shield(starting)
        except asyncio.CancelledError:
            if not starting.cancelled():  # this caller was cancelled, not the start
                raise
            raise RuntimeStateError(f"{self.name} was closed while it started",
                                    Refusal.UNAVAILABLE) from None

    async def _start(self) -> None:
        await asyncio.to_thread(self._prepare)
        # Without the ceiling the app's runtime sets for what it starts: that is for a
        # Kaden an agent starts, whose saved answers an agent could have written. The life
        # is the user's, and so are its answers; its permission level follows theirs.
        env = {k: v for k, v in os.environ.items() if k != PERMISSION_CEILING_ENV}
        live = self._live = await child.spawn(
            self.command, self.folder, cwd=self.home,
            conversation_id=self.conversation_id, env=env, on_event=self._event)
        try:
            if self.conversation_id is None:
                self.conversation_id = (await live.start(StartOptions())).id
                await asyncio.to_thread(self._write)
            # Its life as the app has it now (pace, model, who it is): a new one starts it.
            await live.set_duty(self._life())
            await live.attach()
        except BaseException:  # failed or closed: its process goes, and it does not run
            if self._live is live:
                self._live = None
            live.detach()
            await asyncio.shield(live.channel.close(child.STOP_GRACE_S))
            raise
        self._changed()

    async def set_identity(self, name: str, about: str) -> None:
        """Its name and who it is, in the user's words: kept, and its life's instruction
        from then on (at once, if it lives)."""
        self.name, self.about = name.strip() or NAME, about.strip()
        await asyncio.to_thread(self._write)
        if self._live is not None:
            await self._live.set_duty(self._life())

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
        if (starting := self._starting) is not None and not starting.done():
            starting.cancel()  # it closes its own process
            await asyncio.gather(starting, return_exceptions=True)
        live, self._live = self._live, None
        if live is not None:
            live.detach()
            await live.channel.close(child.STOP_GRACE_S)
        if self._closing:
            await asyncio.wait(self._closing)

    def _life(self) -> Duty:
        return Duty(self.who, DutyKind.LIFE, self.pace_s[0], self.pace_s[1],
                    model=self._own_model)

    def _event(self, envelope: EventEnvelope | None) -> None:
        if envelope is None:  # its process ended by itself
            self._ended()
        if envelope is not None and isinstance(envelope.event, PromptOpened):
            self._questions.append(envelope.event.prompt)
        if envelope is not None and isinstance(envelope.event, MessageCommitted):
            message = envelope.event.message
            text = "".join(b.text for b in message.blocks if isinstance(b, Text)).strip()
            if message.role is Role.USER and text:  # who started this turn
                self._asked = message.from_user
            elif message.role is Role.ASSISTANT and text:
                (self.replies if self._asked else self._messages).append(text)
        self._changed()

    def _ended(self) -> None:
        live, self._live = self._live, None
        if live is None:
            return
        live.detach()
        task = asyncio.create_task(live.channel.close(child.STOP_GRACE_S))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    # ── Its data folder ───────────────────────────────────────────────────

    def _prepare(self) -> None:
        """Its data folder: the app's configuration, its daily amount, its own home."""
        self.home.mkdir(parents=True, exist_ok=True)
        settings = child.settings_of(self.parent_dir, self.folder)
        # What the user said in its own view is its own (saved in its folder), not the
        # chats': its tool settings, and what it may always or never do.
        mine = child.read_settings(self.folder / "settings.toml")
        for key in OWN_SETTINGS:
            settings.pop(key, None)
            if key in mine:
                settings[key] = mine[key]
        # Its own time on the model the user chose for upkeep: the cheaper one.
        self._own_model = settings.get("maintenance_model") or None
        settings["budget"] = ({} if self.daily_usd is None
                              else {"max_cost_usd_per_day": self.daily_usd})
        settings["work_folder"] = str(self.home)
        settings["system_prompt"] = PROMPT
        settings["compact_window"] = COMPACT_WINDOW
        child.write_settings(self.folder / "settings.toml", settings)

    def _read(self) -> dict[str, Any]:
        path = self.folder / RECORD
        return json.loads(path.read_text()) if path.exists() else {}

    def _write(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / RECORD).write_text(json.dumps(
            {"conversation_id": self.conversation_id, "name": self.name,
             "about": self.about, "daily_usd": self.daily_usd}))
