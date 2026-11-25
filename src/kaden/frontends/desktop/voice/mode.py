"""Voice in the window: gets the speech models, opens the microphone, and runs the
conversation (turns.Voice) on the open conversation's events. The microphone and the
speaker are switched on and off apart."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import numpy as np
from PySide6.QtCore import QObject, Signal

from kaden.api import MessageCommitted, Role, RuntimeEvent, StateChanged, Text, TextDelta
from kaden.frontends.desktop.viewmodel import Controller
from kaden.frontends.desktop.voice import download
from kaden.frontends.desktop.voice.natural import (
    NATURAL,
    RECORDING_TYPES,
    RECORDINGS_DIR,
    installed,
    prepare_recording,
    recording,
)
from kaden.frontends.desktop.voice.turns import Engine, Player, State, Voice

# The voices to choose from, as Claude's voice mode offers a few by name. With the
# natural voice: its own and those copied from recordings; otherwise Kokoro's best
# American English speakers, by their number in kokoro-multi-lang-v1_0.
KOKORO_VOICES = {"Heart": 3, "Bella": 2, "Nicole": 6, "Michael": 16, "Fenrir": 14}
DEFAULT_VOICE = "Heart"
SAMPLE = "Hi, I'm Kaden. This is how I sound when we talk."
STATUS = {State.LISTENING: "Listening…", State.THINKING: "Thinking…",
          State.SPEAKING: "Speaking… Esc to interrupt"}


def _kokoro(folder: Path) -> Engine:
    from kaden.frontends.desktop.voice.engine import Kokoro, Parakeet, SherpaEngine
    return SherpaEngine(folder, Kokoro(folder), Parakeet(folder))


def _chatterbox(folder: Path) -> Engine:
    from kaden.frontends.desktop.voice.engine import SherpaEngine
    from kaden.frontends.desktop.voice.natural import Chatterbox, Whisper
    with ThreadPoolExecutor(2) as pool:  # both load onto the GPU at once
        speech, listener = pool.submit(Chatterbox, folder), pool.submit(Whisper, folder)
        return SherpaEngine(folder, speech.result(), listener.result(), check=True)


def _microphone(heard: Callable[[np.ndarray], object]) -> object:
    from kaden.frontends.desktop.voice.audio import Microphone
    return Microphone(heard)


def _speaker() -> Player:
    from kaden.frontends.desktop.voice.audio import Speaker
    return Speaker()


class VoiceMode(QObject):
    """`listening`: the microphone is on; `speaking`: replies are read aloud. `changed`
    is emitted whenever either or `status` changes. `ask` puts a question to
    the user and answers whether they agreed. Nothing of speech is loaded until voice
    mode is first turned on. `natural`: speak with the natural voice (installed() when
    not given)."""

    changed = Signal()

    def __init__(self, controller: Controller, folder: Path | None,
                 ask: Callable[[str, str], Awaitable[bool]], *,
                 engine: Callable[[Path], Engine] | None = None,
                 microphone: Callable[[Callable[[np.ndarray], object]], object] = _microphone,
                 speaker: Callable[[], Player] = _speaker,
                 models: tuple[download.Model, ...] | None = None,
                 transport: httpx2.AsyncBaseTransport | None = None,
                 natural: bool | None = None,
                 prepare: Callable[[Path, Path], None] = prepare_recording) -> None:
        super().__init__()
        self.natural = installed() if natural is None else natural
        self.controller, self.folder, self._ask = controller, folder, ask
        self._make_engine = engine or (_chatterbox if self.natural else _kokoro)
        self._make_microphone, self._make_speaker = microphone, speaker
        self._given_models = models  # instead of those the voice needs
        self._prepare = prepare  # what Kaden keeps of a recording added
        self._transport = transport
        self.listening = self.speaking = False
        self._recordings: list[str] = []  # the names of the voices copied from recordings
        self.voice = self.voices()[0]
        self.status = ""
        self.progress: float | None = None  # of the model download, while it runs
        self._engine: Engine | None = None
        self._voice: Voice | None = None
        self._microphone: object | None = None
        self._starting: asyncio.Task[None] | None = None
        self._loading: asyncio.Task[None] | None = None  # the engine, while it loads
        self.load_at_start = False  # the user's choice: load the engine when Kaden starts
        self._streamed = False  # the reply under way came as deltas
        controller.listeners.append(self._event)

    @property
    def on(self) -> bool:
        return self.listening or self.speaking

    def toggle_microphone(self) -> None:
        self._switch(not self.listening, self.speaking)

    def toggle_speaker(self) -> None:
        self._switch(self.listening, not self.speaking)

    def _switch(self, listening: bool, speaking: bool) -> None:
        was_on = self.on
        self.listening, self.speaking = listening, speaking
        if not self.on:
            self.stop()
            return
        if not was_on:
            self._starting = asyncio.ensure_future(self._start())
        elif self._voice is not None:
            self._voice.set_speaking(speaking)
            self._microphone_on(listening)
            self._voice.set_listening(listening)
        self.changed.emit()

    def _microphone_on(self, on: bool) -> None:
        assert self._voice is not None
        if on and self._microphone is None:
            self._microphone = self._make_microphone(self._voice.heard)
            self._microphone.start()  # type: ignore[attr-defined]
        elif not on and self._microphone is not None:
            self._microphone.stop()  # type: ignore[attr-defined]
            self._microphone = None

    def stop(self) -> None:
        if self._starting is not None:
            self._starting.cancel()
        if self._voice is not None:
            self._voice.stop()
        if self._microphone is not None:
            self._microphone.stop()  # type: ignore[attr-defined]
        self._voice = self._microphone = self._starting = None
        self.listening = self.speaking = False
        self.progress = None
        self._say("")

    def voices(self) -> list[str]:
        if self.natural:
            return [NATURAL, *self._recordings]
        return list(KOKORO_VOICES)

    def models(self) -> tuple[download.Model, ...]:
        """The speech models voice mode needs, as it is set."""
        if self._given_models is not None:
            return self._given_models
        return download.models(natural=self.natural)

    def use(self, controller: Controller) -> None:
        """Talk with the conversation `controller` shows from now on; a voice conversation
        under way with another one ends."""
        if controller is self.controller:
            return
        if self.on:
            self.stop()
        self.controller.listeners.remove(self._event)
        self.controller = controller
        controller.listeners.append(self._event)

    def set_voice(self, name: str) -> None:
        """Speak as `name` (one of voices()) from now on; any other name changes nothing."""
        if name in self.voices():
            self.voice = name
            if self._engine is not None:
                self._engine.voice = name  # type: ignore[attr-defined]
                asyncio.ensure_future(self._warm())

    async def restore(self, name: str | None) -> None:
        """Find the recordings added before and speak as `name`, the voice chosen before,
        where it is still one of voices(); the first of them otherwise."""
        if self.natural and self.folder is not None:
            self._recordings = await asyncio.to_thread(self._recorded, self.folder)
        self.voice = self.voices()[0]
        if name is not None:
            self.set_voice(name)

    async def add_recording(self, path: Path) -> str:
        """Keep what the voice hears of the recording at `path` (natural.start_of: its
        first half minute of talking, long pauses out) in the voice folder, as a voice
        named after the file, and speak as it; its name. Prepared once, here, rather than
        whenever the voice is made ready. OSError, or the audio library's error, when it
        cannot be read or kept."""
        if self.folder is None:
            raise RuntimeError("Voice needs Kaden's data folder, and there is none.")
        folder = self.folder / RECORDINGS_DIR

        def copy() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            if (old := recording(self.folder, path.stem)) is not None:  # one per name
                old.unlink()
            self._prepare(path, folder / f"{path.stem}.wav")
        await asyncio.to_thread(copy)
        self._recordings = sorted({*self._recordings, path.stem} - {NATURAL})
        self.set_voice(path.stem)
        return path.stem

    async def remove_recording(self, name: str) -> None:
        """Delete Kaden's copy of the recording voice `name` was made from (not the file
        the user added it from); its own voice, NATURAL, stays. Speaks as NATURAL if it
        spoke as `name`. OSError when it cannot be deleted."""
        if name not in self._recordings or self.folder is None:
            return
        folder = self.folder

        def delete() -> None:
            if (path := recording(folder, name)) is not None:
                path.unlink()
        await asyncio.to_thread(delete)
        self._recordings.remove(name)
        if self.voice == name:
            self.set_voice(NATURAL)

    @staticmethod
    def _recorded(folder: Path) -> list[str]:
        found = {path.stem for path in (folder / RECORDINGS_DIR).glob("*")
                 if path.suffix.lower() in RECORDING_TYPES}
        return sorted(found - {NATURAL})

    async def preview(self) -> None:
        """Say SAMPLE in the chosen voice, getting the speech models first if need be."""
        if not await self._ready():
            return
        assert self._engine is not None
        samples, rate = await asyncio.to_thread(self._engine.speak, SAMPLE)
        speaker = self._make_speaker()
        try:
            await speaker.play(samples, rate)
        finally:
            speaker.close()

    async def _ready(self) -> bool:
        """The engine is loaded, its models fetched first with the user's yes; False when
        they said no."""
        if self._engine is not None:
            return True
        if self.folder is None:
            raise RuntimeError("Voice needs Kaden's data folder, and there is none.")
        self._say("Getting voice ready…")
        needed = await asyncio.to_thread(download.missing, self.folder, self.models())
        if needed and not await self._get(needed):
            self._say("")
            return False
        self._say("Getting voice ready…")
        await self._load()
        self._say("")
        return True

    async def preload(self) -> None:
        """Load the engine and make the voice ready in the background, as when the user
        first turns voice on, but with nothing asked or shown: models not yet downloaded
        are left for that first time, and a load that fails is tried again then."""
        if self._engine is not None or self.folder is None:
            return
        if await asyncio.to_thread(download.missing, self.folder, self.models()):
            return
        with contextlib.suppress(Exception):
            await self._load()

    async def _load(self) -> None:
        """Load the engine once: whoever asks while it loads waits for the same load."""
        if self._loading is None:
            self._loading = asyncio.ensure_future(self._make())
        try:
            await asyncio.shield(self._loading)
        except Exception:
            self._loading = None  # the next ask tries again
            raise

    async def _make(self) -> None:
        assert self.folder is not None
        engine = await asyncio.to_thread(self._make_engine, self.folder)
        engine.voice = self.voice  # type: ignore[attr-defined]
        self._engine = engine
        await self._warm()

    async def _warm(self) -> None:
        """Get the chosen voice ready; a recording that cannot be read shows up when it
        is spoken, so it is not reported here."""
        assert self._engine is not None
        try:
            await asyncio.to_thread(self._engine.warm)
        except Exception:  # noqa: BLE001, S110 - see the docstring
            pass

    def interrupt(self) -> bool:
        """Stop reading the reply and listen; False when nothing was being read."""
        if self._voice is None or self._voice.state is not State.SPEAKING:
            return False
        self._voice.interrupt()
        return True

    @property
    def spoken(self) -> bool:
        """Messages sent now are part of a spoken conversation: their replies are read
        aloud."""
        return self._voice is not None and self.speaking

    async def _start(self) -> None:
        try:
            if not await self._ready():
                self.stop()
                return
            assert self._engine is not None
            self._voice = Voice(self._engine, self._make_speaker(), self._said, self._moved,
                                failed=self._failed, listening=self.listening,
                                speaking=self.speaking)
            self._microphone_on(self.listening)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - shown; voice stays off
            self.stop()
            self.controller.model.notify(f"Voice could not start: {e}")
            return
        self._starting = None
        self._voice.start(agent_busy=self.controller.model.busy)

    async def _get(self, needed: list[download.Model]) -> bool:
        size = download.megabytes(needed)
        if not await self._ask(
                "Download the speech models?",
                f"Voice needs speech models ({size} MB). They run on this "
                "computer: what you say is not sent to a speech service."):
            return False
        assert self.folder is not None

        def progress(done: float) -> None:
            self.progress = done
            self._say(f"Downloading speech models: {round(done * size)} of {size} MB")

        progress(0.0)
        try:
            await download.fetch(self.folder, needed, progress, transport=self._transport)
        finally:
            self.progress = None
        return True

    def _failed(self, error: Exception) -> None:
        self.stop()
        self.controller.model.notify(f"Voice stopped: {error}")

    def _said(self, text: str) -> None:
        self.controller.send(text, spoken=self.speaking)

    def _moved(self) -> None:
        if self._voice is not None:
            self._say(STATUS.get(self._voice.state, ""))

    def _say(self, status: str) -> None:
        self.status = status
        self.changed.emit()

    def _event(self, event: RuntimeEvent) -> None:
        voice = self._voice
        if voice is None:
            return
        match event:
            case TextDelta(text=text):
                self._streamed = True
                voice.reply(text)
            case MessageCommitted(message=message) if message.role is Role.ASSISTANT:
                if not self._streamed:  # a Core that does not stream: read it now
                    voice.reply("".join(b.text for b in message.blocks if isinstance(b, Text)))
                self._streamed = False
                voice.reply_ended()
            case StateChanged():
                if not self.controller.model.busy:
                    voice.agent_idle()
