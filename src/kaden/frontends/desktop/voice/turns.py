"""Who has the turn in a voice conversation: plain logic over an engine that hears and
speaks, a player for sound, and a function that sends what was said."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

import numpy as np

from kaden.frontends.desktop.voice.speech import Sentences


class State(StrEnum):
    OFF = "off"
    LISTENING = "listening"  # the microphone is heard
    THINKING = "thinking"  # what was said is being understood, or the agent works
    SPEAKING = "speaking"  # the reply is read aloud
    QUIET = "quiet"  # the microphone is off, and nothing is read


class Engine(Protocol):
    """Speech on this machine. `transcribe` and `speak` block and run on a thread."""

    def hear(self, samples: np.ndarray) -> list[np.ndarray]:
        """Take microphone sound (float32, mono, at the engine's rate); return each
        utterance that has ended: the user spoke, then fell silent."""

    def reset(self) -> None:
        """Forget sound heard so far: a new turn to listen begins."""

    def transcribe(self, samples: np.ndarray) -> str: ...

    def speak(self, text: str, *, check: bool = True) -> tuple[np.ndarray, int]:
        """`text` as sound: float32 mono samples and their rate. `check`: where the engine
        hears its speech back for flaws, do so; not when the sound is waited for."""

    def warm(self) -> None:
        """Get ready to speak in the voice chosen, so that its first sentence does not
        wait for it."""


class Player(Protocol):
    async def play(self, samples: np.ndarray, rate: int) -> None:
        """Returns when the sound has been heard or was stopped."""

    def stop(self) -> None: ...

    def close(self) -> None:
        """Nothing more will be played: let go of the sound output."""


TOGETHER = 120  # characters of sentences made into sound at once, behind the first
FIRST = 20  # a first sentence shorter than this ("Okay.") is made with the next


class Voice:
    """One voice conversation. The owner feeds it the microphone (`heard`), the agent's
    reply as it streams (`reply`, `reply_ended`) and when the agent stops (`agent_idle`).
    `failed` is told when what was said cannot be understood or a sentence cannot be
    made into sound; the conversation is then over. `listening`: what the microphone
    hears is taken in its turn; `speaking`: replies are read aloud. Either can change
    while it goes on (set_listening, set_speaking)."""

    def __init__(self, engine: Engine, player: Player, send: Callable[[str], object],
                 on_change: Callable[[], object], *,
                 failed: Callable[[Exception], object] = lambda _error: None,
                 listening: bool = True, speaking: bool = True) -> None:
        self.engine, self.player = engine, player
        self.listening, self.speaking = listening, speaking
        self._send, self._on_change, self._failed = send, on_change, failed
        self.state = State.OFF
        self._sentences = Sentences()
        self._to_say: asyncio.Queue[str] = asyncio.Queue()
        # Sounds made and not yet played: one, so that the next sentence is made while
        # one plays, and no further.
        self._made: asyncio.Queue[tuple[np.ndarray, int]] = asyncio.Queue(maxsize=1)
        self._making = False
        self._playing = False
        self._held: str | None = None  # taken from _to_say, too long to join the last
        self._epoch = 0  # counts speech dropped; a sound made before a drop is not played
        self._tasks: set[asyncio.Task[None]] = set()
        self._speaker: asyncio.Task[None] | None = None
        self._maker: asyncio.Task[None] | None = None
        self._agent_busy = False
        self._replying = False  # a reply streams and has not ended
        self._muted = False  # interrupted: the rest of this reply is not read

    @property
    def busy(self) -> bool:
        """Something said is still being understood."""
        return bool(self._tasks)

    def start(self, *, agent_busy: bool = False) -> None:
        self._agent_busy, self._muted = agent_busy, False
        self._maker = asyncio.ensure_future(self._make())
        self._speaker = asyncio.ensure_future(self._speak())
        self._listen() if not agent_busy else self._set(State.THINKING)

    def stop(self) -> None:
        for task in (*self._tasks, self._speaker, self._maker):
            if task is not None:
                task.cancel()
        self._tasks.clear()
        self._speaker = self._maker = None
        self._drop_speech()
        self.player.close()
        self._set(State.OFF)

    def heard(self, samples: np.ndarray) -> None:
        if self.state is not State.LISTENING:
            return
        for utterance in self.engine.hear(samples):
            self._set(State.THINKING)
            task = asyncio.ensure_future(self._said(utterance))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return  # one turn: what follows is heard when listening again

    def set_listening(self, on: bool) -> None:
        self.listening = on
        if self.state in (State.LISTENING, State.QUIET):
            self._listen()

    def set_speaking(self, on: bool) -> None:
        """Off: what is being read stops at once, and the rest of the reply stays unread."""
        self.speaking = on
        if not on and self.state is not State.OFF:
            self._drop_speech()
            if self.state is State.SPEAKING:
                self._done_speaking()

    def reply(self, text: str) -> None:
        self._replying = True
        if self.state is State.OFF or self._muted or not self.speaking:
            return
        for sentence in self._sentences.feed(text):
            self._to_say.put_nowait(sentence)

    def reply_ended(self) -> None:
        self._replying = False
        if self._muted:  # the reply that was interrupted is over: the next one is read
            self._muted = False
            return
        if self.state is State.OFF:
            return
        for sentence in self._sentences.flush():
            self._to_say.put_nowait(sentence)

    def agent_idle(self) -> None:
        self._agent_busy = False
        if self.state is State.THINKING and not self._speech_left and not self.busy:
            self._listen()

    def interrupt(self) -> None:
        """Stop reading and listen; what is left of the reply stays unread."""
        if self.state is State.OFF:
            return
        self._muted = self._replying
        self._drop_speech()
        self._listen()

    @property
    def _speech_left(self) -> bool:
        return (not self._to_say.empty() or self._held is not None or self._making
                or not self._made.empty())

    def _drop_speech(self) -> None:
        self._sentences = Sentences()
        self._held = None
        self._epoch += 1
        for queue in (self._to_say, self._made):
            while not queue.empty():
                queue.get_nowait()
        self.player.stop()

    def _listen(self) -> None:
        self.engine.reset()
        self._set(State.LISTENING if self.listening else State.QUIET)

    def _set(self, state: State) -> None:
        if state is not self.state:
            self.state = state
            self._on_change()

    async def _said(self, utterance: np.ndarray) -> None:
        try:
            text = (await asyncio.to_thread(self.engine.transcribe, utterance)).strip()
        except Exception as e:  # noqa: BLE001 - reported; the owner ends the conversation
            self._failed(e)
            return
        if self.state is not State.THINKING:
            return
        if not text:
            self._listen()
            return
        self._tasks.discard(asyncio.current_task())  # understood: the agent's turn now
        self._agent_busy = True
        self._send(text)

    async def _make(self) -> None:
        """Make each sentence into sound as soon as the one before is made. With nothing
        to play, one sentence alone (with the next if shorter than FIRST), so that speech
        starts soon; behind one playing, those waiting together, up to TOGETHER
        characters."""
        while True:
            sentence, self._held = self._held or await self._to_say.get(), None
            behind = self._playing or not self._made.empty()
            while (behind or len(sentence) < FIRST) and not self._to_say.empty():
                following = self._to_say.get_nowait()
                if len(sentence) + 1 + len(following) > TOGETHER:
                    self._held = following
                    break
                sentence += " " + following
            self._set(State.SPEAKING)
            self._making, epoch = True, self._epoch
            try:
                # The first sound is waited for, so it is not heard back for flaws.
                sound = await asyncio.to_thread(self.engine.speak, sentence, check=behind)
            except Exception as e:  # noqa: BLE001 - reported; the owner ends it
                self._making = False
                self._failed(e)
                return
            self._making = False
            if epoch == self._epoch:  # not dropped meanwhile
                await self._made.put(sound)
            elif not self._speech_left:
                self._done_speaking()

    async def _speak(self) -> None:
        while True:
            samples, rate = await self._made.get()
            if self.state is State.SPEAKING:  # not interrupted meanwhile
                self._playing = True
                try:
                    await self.player.play(samples, rate)
                finally:
                    self._playing = False
            if not self._speech_left:
                self._done_speaking()

    def _done_speaking(self) -> None:
        if self.state is State.SPEAKING:
            self._listen() if not self._agent_busy else self._set(State.THINKING)
