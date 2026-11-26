"""Voice conversation in the desktop app (frontends.desktop.voice): what is read aloud,
and who has the turn. No sound device and no speech model: the engine is scripted."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from kaden.frontends.desktop import theme
from kaden.frontends.desktop.voice.speech import Sentences, spoken
from kaden.frontends.desktop.voice.turns import State, Voice
from tests.conftest import eventually


def test_only_words_are_read_aloud():
    assert spoken("**Done!** I fixed `parse()` in [the parser](src/p.py).") == \
        "Done! I fixed parse in the parser."
    assert spoken("All set (ノ◕ヮ◕)ノ*:･ﾟ✧") == "All set"
    assert spoken("Nothing's left open (=^･ω･^=)") == "Nothing's left open"
    assert spoken("## Result\n- one\n- two") == "Result one two"
    assert spoken("See https://example.com/a?b=1 for more.") == "See for more."
    assert spoken("| a | b |\n|---|---|\n| 1 | 2 |") == ""
    assert spoken("Run this:\n```bash\nrm -rf /\n```\nThen wait.") == "Run this: Then wait."
    assert spoken("It costs $3.50 (about 20% more), café prices.") == \
        "It costs $3.50 (about 20% more), café prices."




def test_whispers_stock_phrases_alone_are_not_taken_as_speech():
    """On noise Whisper writes "Thank you." and the like with confidence; said by someone,
    they come with other words."""
    from kaden.frontends.desktop.voice.natural import stock_phrase

    for heard in ("Thank you.", "thank you!", "Thanks for watching!", "you", "Bye."):
        assert stock_phrase(heard), heard
    for heard in ("Thank you, that fixed it.", "Can you commit it?"):
        assert not stock_phrase(heard), heard

def test_a_reply_is_cut_into_sentences_while_it_streams():
    sentences = Sentences()
    assert sentences.feed("Hello there. I am ") == ["Hello there."]
    assert sentences.feed("reading 3.5 files") == []
    assert sentences.feed("!\nNext: ") == ["I am reading 3.5 files!"]
    assert sentences.feed("```py\nx = 1. y = 2\n") == []  # code is waited out, not read
    assert sentences.feed("```\nDone") == ["Next:"]
    assert sentences.flush() == ["Done"]
    assert sentences.flush() == []


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app, dark=False)
    return app


class ScriptedEngine:
    """Hears an utterance whenever a chunk holding a 1 arrives; says what `words` holds."""

    def __init__(self, *words: str) -> None:
        self.words = list(words)
        self.spoke: list[str] = []  # made into sound, whether played or not
        self.warmed: list[str] = []
        self.checked: list[bool] = []  # whether each was to be heard back
        self.resets = 0

    def hear(self, samples: np.ndarray) -> list[np.ndarray]:
        return [samples] if samples.any() else []

    def reset(self) -> None:
        self.resets += 1

    def transcribe(self, samples: np.ndarray) -> str:
        return self.words.pop(0)

    def speak(self, text: str, *, check: bool = True) -> tuple[np.ndarray, int]:
        self.spoke.append(text)
        self.checked.append(check)
        return np.frombuffer(text.encode(), dtype=np.uint8).astype(np.float32), 24000

    def warm(self) -> None:
        self.warmed.append(self.voice)  # type: ignore[attr-defined]


class Speaker:
    def __init__(self) -> None:
        self.played = 0
        self.said: list[str] = []  # what was played, as ScriptedEngine made it
        self.stopped = 0
        self.closed = False
        self.hold: asyncio.Event | None = None  # set to keep a sentence playing

    async def play(self, samples: np.ndarray, rate: int) -> None:
        self.played += 1
        self.said.append(bytes(samples.astype(np.uint8)).decode())
        if self.hold is not None:
            await self.hold.wait()

    def stop(self) -> None:
        self.stopped += 1
        if self.hold is not None:
            self.hold.set()

    def close(self) -> None:
        self.closed = True


def make(*words: str) -> tuple[Voice, ScriptedEngine, Speaker, list[str], list[State]]:
    engine, speaker, sent, states = ScriptedEngine(*words), Speaker(), [], []
    voice = Voice(engine, speaker, sent.append, lambda: states.append(voice.state))
    return voice, engine, speaker, sent, states


QUIET, TALK = np.zeros(8, dtype=np.float32), np.ones(8, dtype=np.float32)


def sha(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()


async def test_what_is_said_is_sent_and_the_reply_is_spoken_then_it_listens_again():
    voice, engine, speaker, sent, states = make("what time is it")
    assert voice.state is State.OFF
    voice.start()
    assert voice.state is State.LISTENING
    voice.heard(QUIET)
    assert sent == []
    voice.heard(TALK)
    await eventually(lambda: sent == ["what time is it"])
    assert voice.state is State.THINKING
    voice.heard(TALK)  # not its turn to listen
    voice.reply("It is noon. ")
    await eventually(lambda: engine.spoke == ["It is noon."])
    voice.reply("Lunch time (＾▽＾)")
    voice.reply_ended()
    voice.agent_idle()
    await eventually(lambda: voice.state is State.LISTENING)
    assert engine.spoke == ["It is noon.", "Lunch time"] and speaker.played == 2
    assert sent == ["what time is it"]
    assert states[:3] == [State.LISTENING, State.THINKING, State.SPEAKING]
    assert not speaker.closed  # one sound output for the whole conversation
    voice.stop()
    assert voice.state is State.OFF and speaker.closed


async def test_what_is_said_into_an_interrupted_reply_does_not_bring_it_back():
    """The reply that was interrupted still streams when the next thing is said: its
    rest stays unread, and the reply after it is read."""
    voice, _engine, speaker, sent, _states = make("tell me a story", "stop")
    speaker.hold = asyncio.Event()
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["tell me a story"])
    voice.reply("Once upon a time, in a wood. There was a fox. ")
    await eventually(lambda: voice.state is State.SPEAKING)
    voice.interrupt()
    speaker.hold = None
    voice.heard(TALK)
    await eventually(lambda: sent == ["tell me a story", "stop"])
    voice.reply("It lived in a den. ")  # still the interrupted reply
    voice.reply_ended()
    await asyncio.sleep(0.05)
    assert speaker.said == ["Once upon a time, in a wood."]
    voice.reply("All right. ")  # the answer to what was said
    voice.reply_ended()
    await eventually(lambda: speaker.said == ["Once upon a time, in a wood.", "All right."])
    voice.stop()


async def test_a_reply_after_an_interrupted_one_is_read_though_nothing_was_said():
    voice, _engine, speaker, sent, _states = make("tell me a story")
    speaker.hold = asyncio.Event()
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["tell me a story"])
    voice.reply("Once upon a time, in a wood. There was a fox. ")
    await eventually(lambda: voice.state is State.SPEAKING)
    voice.interrupt()
    speaker.hold = None
    voice.reply_ended()
    voice.agent_idle()
    voice.reply("The build is done. ")  # one the agent began by itself
    voice.reply_ended()
    await eventually(lambda: speaker.said[-1] == "The build is done.")
    voice.stop()


async def test_nothing_understood_sends_nothing():
    voice, _engine, _speaker, sent, _states = make("  ")
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: voice.state is State.LISTENING and not voice.busy)
    assert sent == []


async def test_interrupting_stops_the_speech_and_listens():
    voice, _engine, speaker, sent, _states = make("tell me a story", "stop")
    speaker.hold = asyncio.Event()
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["tell me a story"])
    voice.reply("Once upon a time, in a wood. There was a fox. ")
    await eventually(lambda: voice.state is State.SPEAKING)
    voice.interrupt()
    assert speaker.stopped == 1 and voice.state is State.LISTENING
    voice.reply("It lived in a den. ")  # the rest of that reply is not read
    voice.reply_ended()
    await asyncio.sleep(0.05)
    assert speaker.said == ["Once upon a time, in a wood."]
    speaker.hold = None
    voice.heard(TALK)
    await eventually(lambda: sent == ["tell me a story", "stop"])
    voice.reply("Okay. ")
    await eventually(lambda: speaker.said[-1] == "Okay.")


async def test_the_next_sentence_is_made_while_one_plays():
    """A slow voice makes a sentence in about the time it takes to play: made one after
    the other, each would wait for the one before to finish."""
    voice, engine, speaker, sent, _states = make("go on")
    speaker.hold = asyncio.Event()
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["go on"])
    voice.reply("This is the first one. Two. ")
    await eventually(lambda: engine.spoke == ["This is the first one.", "Two."])
    assert speaker.said == ["This is the first one."]  # still playing
    speaker.hold.set()
    speaker.hold = None
    voice.reply_ended()
    voice.agent_idle()
    await eventually(lambda: voice.state is State.LISTENING)
    assert speaker.said == ["This is the first one.", "Two."]
    voice.stop()


async def test_while_one_sentence_plays_the_next_few_are_made_together():
    """The first sentence is made alone, so that speech starts soon; behind it, short
    sentences are made together: each costs the voice about the same however short,
    and it sounds better with more to read."""
    voice, engine, speaker, sent, _states = make("go on")
    speaker.hold = asyncio.Event()
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["go on"])
    voice.reply("This is the first one. Two. Three. ")
    await eventually(lambda: engine.spoke == ["This is the first one.", "Two. Three."])
    # Heard back for repeats only behind the first: that one is waited for.
    assert engine.checked == [False, True]
    speaker.hold.set()
    speaker.hold = None
    voice.reply_ended()
    voice.agent_idle()
    await eventually(lambda: speaker.said == ["This is the first one.", "Two. Three."])
    voice.stop()

async def test_a_very_short_first_sentence_is_made_with_the_next():
    """"Okay." plays for about a second: too short to cover making what follows."""
    voice, engine, _speaker, sent, _states = make("go on")
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["go on"])
    voice.reply("Okay. I ran all of the tests just now. Done. ")
    voice.reply_ended()
    voice.agent_idle()
    await eventually(lambda: voice.state is State.LISTENING)
    assert engine.spoke[0] == "Okay. I ran all of the tests just now."
    voice.stop()

async def test_speech_that_cannot_be_made_is_reported():
    class Broken(ScriptedEngine):
        def speak(self, text: str, *, check: bool = True) -> tuple[np.ndarray, int]:
            raise RuntimeError("CUDA out of memory")

    engine, speaker, sent, failures = Broken("hi"), Speaker(), [], []
    voice = Voice(engine, speaker, sent.append, lambda: None, failed=failures.append)
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["hi"])
    voice.reply("Hello. ")
    await eventually(lambda: len(failures) == 1)
    assert str(failures[0]) == "CUDA out of memory" and speaker.said == []
    voice.stop()


async def test_turned_on_while_the_agent_works_it_waits_for_its_turn():
    voice, _engine, _speaker, sent, _states = make("hello")
    voice.start(agent_busy=True)
    assert voice.state is State.THINKING
    voice.heard(TALK)
    await asyncio.sleep(0.02)
    assert sent == []
    voice.agent_idle()
    assert voice.state is State.LISTENING


async def test_with_the_microphone_alone_what_is_said_is_sent_and_the_reply_not_read():
    engine, speaker, sent = ScriptedEngine("what time is it"), Speaker(), []
    voice = Voice(engine, speaker, sent.append, lambda: None, speaking=False)
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["what time is it"])
    voice.reply("It is noon. ")
    voice.reply_ended()
    voice.agent_idle()
    assert voice.state is State.LISTENING
    assert engine.spoke == [] and speaker.played == 0
    voice.stop()


async def test_with_the_speaker_alone_replies_are_read_and_nothing_is_heard():
    engine, speaker, sent = ScriptedEngine("hello"), Speaker(), []
    voice = Voice(engine, speaker, sent.append, lambda: None, listening=False)
    voice.start()
    assert voice.state is State.QUIET
    voice.heard(TALK)
    await asyncio.sleep(0.02)
    assert sent == []
    voice.reply("It is noon. ")  # the answer to a typed message
    voice.reply_ended()
    await eventually(lambda: speaker.said == ["It is noon."])
    await eventually(lambda: voice.state is State.QUIET)
    assert sent == []
    voice.stop()


async def test_esc_stops_the_rest_of_a_reply_to_a_typed_message():
    """The agent was not set going by something said, yet the reply interrupted is over
    only when it ends."""
    speaker = Speaker()
    voice = Voice(ScriptedEngine(), speaker, lambda _text: None, lambda: None,
                  listening=False)
    speaker.hold = asyncio.Event()
    voice.start()
    voice.reply("Once upon a time, in a wood. There was a fox. ")
    await eventually(lambda: voice.state is State.SPEAKING)
    voice.interrupt()
    assert voice.state is State.QUIET
    speaker.hold = None
    voice.reply("It lived in a den. ")
    voice.reply_ended()
    await asyncio.sleep(0.05)
    assert speaker.said == ["Once upon a time, in a wood."]
    voice.reply("The next one. ")
    voice.reply_ended()
    await eventually(lambda: speaker.said[-1] == "The next one.")
    voice.stop()


async def test_each_half_is_turned_off_and_on_while_the_other_goes_on():
    voice, _engine, speaker, sent, _states = make("tell me a story", "and then")
    speaker.hold = asyncio.Event()
    voice.start()
    voice.heard(TALK)
    await eventually(lambda: sent == ["tell me a story"])
    voice.reply("Once upon a time, in a wood. There was a fox. ")
    await eventually(lambda: voice.state is State.SPEAKING)
    voice.set_speaking(False)  # stops at once; the rest is not read
    assert speaker.stopped == 1 and voice.state is State.THINKING
    speaker.hold = None
    voice.reply("It lived in a den. ")
    voice.reply_ended()
    voice.agent_idle()
    assert voice.state is State.LISTENING
    assert speaker.said == ["Once upon a time, in a wood."]
    voice.set_listening(False)
    assert voice.state is State.QUIET
    voice.heard(TALK)
    await asyncio.sleep(0.02)
    assert sent == ["tell me a story"]
    voice.set_listening(True)
    assert voice.state is State.LISTENING
    voice.set_speaking(True)
    voice.heard(TALK)
    await eventually(lambda: sent == ["tell me a story", "and then"])
    voice.reply("A fox came by. ")
    await eventually(lambda: speaker.said[-1] == "A fox came by.")
    voice.stop()


async def test_the_speech_models_are_downloaded_whole_or_not_at_all(tmp_path):
    import io
    import tarfile

    import httpx2

    from kaden.frontends.desktop.voice.download import Model, fetch, megabytes, missing

    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:bz2") as archive:
        data = b"weights"
        info = tarfile.TarInfo("listener/model.onnx")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    served = {"/vad.onnx": b"vad", "/listener.tar.bz2": packed.getvalue()}
    models = [Model("vad.onnx", "https://models.example/vad.onnx", 3, sha(b"vad"),
                    archive=False),
              Model("listener", "https://models.example/listener.tar.bz2",
                    len(served["/listener.tar.bz2"]), sha(served["/listener.tar.bz2"]))]
    folder = tmp_path / "voice"
    assert missing(folder, models) == models and megabytes(models) == 0

    def serve(request: httpx2.Request) -> httpx2.Response:
        if request.url.path not in served:
            return httpx2.Response(404)
        return httpx2.Response(200, content=served[request.url.path])

    seen: list[float] = []
    await fetch(folder, models, seen.append, transport=httpx2.MockTransport(serve))
    assert missing(folder, models) == []
    assert (folder / "vad.onnx").read_bytes() == b"vad"
    assert (folder / "listener" / "model.onnx").read_bytes() == b"weights"
    assert seen[-1] == 1.0 and seen == sorted(seen)
    assert sorted(p.name for p in folder.iterdir()) == ["listener", "vad.onnx"]  # no leftovers

    deep = [Model("weights/one.bin", "https://models.example/vad.onnx", 3, sha(b"vad"),
                  archive=False)]
    await fetch(folder, deep, seen.append, transport=httpx2.MockTransport(serve))
    assert (folder / "weights" / "one.bin").read_bytes() == b"vad"

    gone = [Model("other", "https://models.example/other.tar.bz2", 10, sha(b""))]
    with pytest.raises(httpx2.HTTPError):
        await fetch(folder, gone, seen.append, transport=httpx2.MockTransport(serve))
    assert missing(folder, gone) == gone


def test_microphone_sound_is_made_mono_at_the_engines_rate():
    from PySide6.QtMultimedia import QAudioFormat

    from kaden.frontends.desktop.voice.audio import as_heard
    from kaden.frontends.desktop.voice.engine import RATE

    stereo = np.array([[16384, -16384]] * 4800 + [[16384, 16384]] * 4800, dtype=np.int16)
    heard = as_heard(stereo.tobytes() + b"\x01", 48000, 2, QAudioFormat.SampleFormat.Int16)
    assert heard.dtype == np.float32 and len(heard) == RATE // 5  # 0.2 s
    assert abs(heard[0]) < 1e-6 and abs(heard[-1] - 0.5) < 1e-6  # the channels' mean
    same = as_heard(np.full(160, 0.25, np.float32).tobytes(), RATE, 1,
                    QAudioFormat.SampleFormat.Float)
    assert len(same) == 160 and abs(same[0] - 0.25) < 1e-6


class FakeMicrophone:
    def __init__(self, heard) -> None:
        self.heard, self.open = heard, False

    def start(self) -> None:
        self.open = True

    def stop(self) -> None:
        self.open = False


async def test_the_voice_button_starts_a_spoken_conversation(
        make, qapp, tmp_path, monkeypatch):
    """The first use asks, downloads the models and listens; what is said is sent, the
    reply is read aloud, and the agent is told it is being talked to."""
    import httpx2

    from kaden.api import Role, Text
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.voice.download import Model
    from kaden.frontends.desktop.voice.mode import VoiceMode
    from kaden.frontends.desktop.window import MainWindow
    from kaden.runtime.config import VOICE_ON_NOTICE
    from tests.fakes.core import Step

    h = await make()
    h.core.add(Step(text="It is noon. Lunch time (＾▽＾)"))
    await h.start()
    controller = Controller(h.runtime, ConversationModel())
    engine, speaker, microphones, asked = ScriptedEngine("what time is it"), Speaker(), [], []

    async def ask(text: str, details: str) -> bool:
        asked.append(details)
        return len(asked) > 1  # declined the first time

    def microphone(heard) -> FakeMicrophone:
        microphones.append(FakeMicrophone(heard))
        return microphones[-1]

    release = asyncio.Event()  # holds the download partway
    monkeypatch.setattr("kaden.frontends.desktop.voice.download.CHUNK", 1)

    async def body():
        yield b"va"
        await release.wait()
        yield b"d"

    class Slow(httpx2.AsyncBaseTransport):  # MockTransport would read the body at once
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, content=body())

    served = Slow()
    folder = tmp_path / "voice"
    voice = VoiceMode(controller, folder, ask, engine=lambda _folder: engine,
                      microphone=microphone, speaker=lambda: speaker, transport=served,
                      models=(Model("vad.onnx", "https://models.example/vad.onnx", 3, sha(b"vad"),
                                    False),))
    window = MainWindow(h.runtime, controller, voice=voice)
    task = asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    assert window.microphone_button.toolTip() == "Talk to Kaden"
    assert window.speaker_button.toolTip() == "Read replies aloud"

    window.microphone_button.click()
    await eventually(lambda: asked and not voice.on)  # declined: off, nothing fetched
    assert "0 MB" in asked[0] and not folder.exists() and not microphones

    assert window.voice_progress.isHidden()
    window.microphone_button.click()
    await eventually(lambda: voice.progress is not None and voice.progress > 0.5)
    assert not window.voice_progress.isHidden()  # a bar and how far it is, while it runs
    assert window.voice_progress_bar.value() == 667
    assert window.voice_progress_label.text().startswith("Downloading speech models: ")
    window.speaker_button.click()  # while it downloads
    release.set()
    await eventually(lambda: voice.spoken)
    assert window.voice_progress.isHidden()
    assert (folder / "vad.onnx").read_bytes() == b"vad"
    assert window.input.placeholderText() == "Listening…"
    for button in (window.microphone_button, window.speaker_button):
        assert button.property("active") is True
    assert window.microphone_button.toolTip() == "Stop listening"
    assert window.speaker_button.toolTip() == "Stop reading aloud"
    [mic] = microphones
    assert mic.open
    mic.heard(TALK)
    await eventually(lambda: engine.spoke == ["It is noon.", "Lunch time"])
    await eventually(lambda: window.input.placeholderText() == "Listening…")
    said = [b.text for m in await h.transcript() if m.role is Role.USER
            for b in m.blocks if isinstance(b, Text)]
    assert said == ["what time is it", VOICE_ON_NOTICE]

    window.microphone_button.click()  # the speaker goes on
    assert voice.on and voice.spoken and not mic.open
    assert window.microphone_button.property("active") is False
    assert window.input.placeholderText() == "Message Kaden"
    window.speaker_button.click()
    assert not voice.on
    assert window.speaker_button.property("active") is False
    window.speaker_button.click()  # on again: no question, the models are there
    await eventually(lambda: voice.spoken)
    assert len(asked) == 2 and len(microphones) == 1
    window.close()
    assert not voice.on
    task.cancel()


async def test_the_microphone_alone_is_not_a_spoken_conversation(make, qapp, tmp_path):
    """What is said is sent, and the reply is read on screen: the agent writes as for
    reading."""
    from kaden.api import Role
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.voice.mode import VoiceMode
    from kaden.frontends.desktop.window import MainWindow
    from tests.fakes.core import Step

    h = await make()
    h.core.add(Step(text="It is noon."))
    await h.start()
    controller = Controller(h.runtime, ConversationModel())
    engine, speaker, microphones = ScriptedEngine("what time is it"), Speaker(), []

    def microphone(heard) -> FakeMicrophone:
        microphones.append(FakeMicrophone(heard))
        return microphones[-1]

    voice = VoiceMode(controller, tmp_path / "voice", yes, engine=lambda _folder: engine,
                      microphone=microphone, speaker=lambda: speaker, models=(),
                      natural=False)
    window = MainWindow(h.runtime, controller, voice=voice)
    task = asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.microphone_button.click()
    await eventually(lambda: microphones and voice.on)
    assert not voice.spoken
    microphones[0].heard(TALK)
    assert window.input.placeholderText() == "Thinking…"
    await eventually(lambda: window.input.placeholderText() == "Listening…")
    users = [m for m in await h.transcript() if m.role is Role.USER]
    assert len(users) == 1 and not users[0].spoken
    assert engine.spoke == []
    window.close()
    task.cancel()


async def test_the_speaker_alone_reads_the_reply_to_a_typed_message(make, qapp, tmp_path):
    from kaden.api import Role
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.voice.mode import VoiceMode
    from kaden.frontends.desktop.window import MainWindow
    from tests.fakes.core import Step

    h = await make()
    h.core.add(Step(text="It is noon."))
    await h.start()
    controller = Controller(h.runtime, ConversationModel())
    engine, speaker, microphones = ScriptedEngine(), Speaker(), []
    voice = VoiceMode(controller, tmp_path / "voice", yes, engine=lambda _folder: engine,
                      microphone=lambda heard: microphones.append(heard), speaker=lambda: speaker,
                      models=(), natural=False)
    window = MainWindow(h.runtime, controller, voice=voice)
    task = asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.speaker_button.click()
    await eventually(lambda: voice.spoken)
    assert window.input.placeholderText() == "Message Kaden"  # nothing listens
    window.input.setPlainText("what time is it")
    window.send_button.click()
    await eventually(lambda: speaker.said == ["It is noon."])
    users = [m for m in await h.transcript() if m.role is Role.USER and not m.hidden]
    assert users[0].spoken and microphones == []
    window.close()
    task.cancel()


def test_a_microphone_that_reports_no_error_is_open(qapp, monkeypatch):
    """Qt reports NoError in an enum that is not equal to QAudio's of the same name."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtMultimedia import QtAudio

    from kaden.frontends.desktop.voice import audio

    class Device:
        def isNull(self) -> bool:
            return False

        def isFormatSupported(self, _format: object) -> bool:
            return True

    class Source:
        def __init__(self, *_args: object) -> None:
            self.buffer = QBuffer()
            self.buffer.open(QIODevice.OpenModeFlag.ReadWrite)

        def start(self) -> QBuffer:
            return self.buffer

        def error(self) -> object:
            return QtAudio.Error.NoError

        def stop(self) -> None: ...

        def deleteLater(self) -> None: ...

    monkeypatch.setattr(audio.QMediaDevices, "defaultAudioInput", staticmethod(Device))
    monkeypatch.setattr(audio, "QAudioSource", Source)
    heard: list[np.ndarray] = []
    microphone = audio.Microphone(heard.append)
    microphone.start()  # does not raise NoMicrophone
    microphone.stop()


def test_speech_is_played_on_both_channels():
    """Mono sent as one channel reaches only the left ear on some sound servers (PipeWire
    with Qt): each sample goes to both channels."""
    import numpy as np
    from PySide6.QtMultimedia import QAudioFormat

    from kaden.frontends.desktop.voice.audio import as_played

    sound, pcm = as_played(np.array([0.5, -0.25], np.float32), 22050)
    assert (sound.sampleRate(), sound.channelCount()) == (22050, 2)
    assert sound.sampleFormat() == QAudioFormat.SampleFormat.Int16
    assert np.frombuffer(pcm, np.int16).tolist() == [16383, 16383, -8191, -8191]


async def test_the_voice_is_chosen_heard_and_used(tmp_path):
    """As in Claude's voice mode: a few named voices; Play speaks a sample in one; voice
    mode speaks in the one chosen."""
    from kaden.frontends.desktop.voice.mode import DEFAULT_VOICE, KOKORO_VOICES, SAMPLE, VoiceMode

    engine, speaker = ScriptedEngine(), Speaker()
    mode = VoiceMode(Controller(), tmp_path, yes, engine=lambda _folder: engine,
                     microphone=lambda heard: None, speaker=lambda: speaker, models=(),
                     natural=False)
    assert mode.voice == DEFAULT_VOICE == "Heart" and KOKORO_VOICES["Heart"] == 3
    mode.set_voice("Michael")
    await mode.preview()
    assert engine.voice == "Michael" and engine.spoke == [SAMPLE] and speaker.closed
    # The voice is made ready when loaded and when chosen, not at its first sentence.
    mode.set_voice("Bella")
    await eventually(lambda: engine.warmed == ["Michael", "Bella"])
    assert speaker.played == 1
    mode.set_voice("Nobody")  # not a voice: the choice stays
    assert mode.voice == "Bella"


class Controller:
    def __init__(self) -> None:
        self.listeners: list = []


async def yes(text: str, details: str) -> bool:
    return True


def prepared(source: Path, target: Path) -> None:
    """What preparing a recording does to it, without the audio libraries: marked."""
    target.write_bytes(b"prepared:" + source.read_bytes())


async def test_with_the_natural_voice_installed_it_speaks_as_itself_or_a_recording(tmp_path):
    """Chatterbox: its own voice, and any copied from a recording the user adds; the
    recording is kept in the voice folder and listed the next time."""
    from kaden.frontends.desktop.voice.mode import NATURAL, VoiceMode

    engine, speaker = ScriptedEngine(), Speaker()
    folder = tmp_path / "voice"

    def natural() -> VoiceMode:
        return VoiceMode(Controller(), folder, yes, engine=lambda _folder: engine,
                         microphone=lambda heard: None, speaker=lambda: speaker,
                         models=(), natural=True, prepare=prepared)

    mode = natural()
    assert mode.voices() == [NATURAL] and mode.voice == NATURAL
    recording = tmp_path / "Grandpa.wav"
    recording.write_bytes(b"RIFF")
    assert await mode.add_recording(recording) == "Grandpa"
    # Prepared once, as it is added: Kaden keeps the clean clip, a WAV.
    assert (folder / "voices" / "Grandpa.wav").read_bytes() == b"prepared:RIFF"
    assert mode.voices() == [NATURAL, "Grandpa"] and mode.voice == "Grandpa"
    await mode.preview()
    assert engine.voice == "Grandpa" and speaker.played == 1
    message = tmp_path / "Mei.OPUS"  # a voice message, as chat apps save them
    message.write_bytes(b"OggS")
    assert await mode.add_recording(message) == "Mei"
    assert (folder / "voices" / "Mei.wav").read_bytes() == b"prepared:OggS"
    phone = tmp_path / "Lin.m4a"  # as phones record
    phone.write_bytes(b"ftyp")
    assert await mode.add_recording(phone) == "Lin"

    again = natural()
    await again.restore("Grandpa")
    assert again.voices() == [NATURAL, "Grandpa", "Lin", "Mei"] and again.voice == "Grandpa"
    await again.remove_recording("Grandpa")  # its copy goes; the user's file stays
    assert again.voices() == [NATURAL, "Lin", "Mei"] and again.voice == NATURAL
    assert not (folder / "voices" / "Grandpa.wav").exists() and recording.exists()
    await again.remove_recording(NATURAL)  # its own voice cannot be removed
    assert again.voices() == [NATURAL, "Lin", "Mei"]
    await again.restore("Heart")  # a Kokoro voice: not one of these
    assert again.voice == NATURAL


async def test_the_voice_saved_is_restored_when_it_is_one_of_the_voices(tmp_path):
    from kaden.frontends.desktop.voice.mode import DEFAULT_VOICE, VoiceMode

    mode = VoiceMode(Controller(), tmp_path, yes, models=(), natural=False)
    await mode.restore("Bella")
    assert mode.voice == "Bella"
    await mode.restore("Nobody")
    assert mode.voice == DEFAULT_VOICE


def test_only_the_speech_models_in_use_are_fetched():
    from kaden.frontends.desktop.voice import download

    natural = [m.name for m in download.models(natural=True)]
    kokoro = [m.name for m in download.models(natural=False)]
    assert kokoro == [download.VAD.name, download.PARAKEET.name, download.KOKORO.name]
    # With the natural voice, Whisper listens on the GPU and Chatterbox speaks.
    assert natural[0] == download.VAD.name
    assert {name.split("/")[0] for name in natural[1:]} == {"whisper", "chatterbox"}
    assert "whisper/model.safetensors" in natural


async def test_a_recording_is_found_by_its_whole_name_whatever_it_holds(tmp_path):
    """File names from the web hold brackets and the like; they are names, not patterns."""
    from kaden.frontends.desktop.voice.mode import VoiceMode
    from kaden.frontends.desktop.voice.natural import recording

    name = "Voice - Gwen [SUBBED] - English [DCbHTjR-jp4]"
    mode = VoiceMode(Controller(), tmp_path, yes, models=(), natural=True, prepare=prepared)
    for content in (b"first", b"second"):  # the same name again replaces it
        source = tmp_path / f"{name}.opus"
        source.write_bytes(content)
        assert await mode.add_recording(source) == name
    found = recording(tmp_path, name)
    assert found is not None and found.read_bytes() == b"prepared:second"
    assert recording(tmp_path, "Voice - Gwen S - English D") is None


async def test_voice_mode_ends_and_says_why_when_speech_fails(tmp_path):
    from types import SimpleNamespace

    from kaden.frontends.desktop.voice.mode import VoiceMode

    class Broken(ScriptedEngine):
        def speak(self, text: str, *, check: bool = True) -> tuple[np.ndarray, int]:
            raise RuntimeError("CUDA out of memory")

    notices: list[str] = []
    controller = SimpleNamespace(listeners=[], model=SimpleNamespace(
        busy=False, notify=notices.append))
    mode = VoiceMode(controller, tmp_path, yes, engine=lambda _folder: Broken(),
                     microphone=FakeMicrophone, speaker=Speaker, models=(), natural=False)
    mode.toggle_speaker()
    await eventually(lambda: mode.spoken)
    assert mode._voice is not None
    mode._voice.reply("Hello. ")
    await eventually(lambda: not mode.on)
    assert notices == ["Voice stopped: CUDA out of memory"]


def test_long_pauses_in_a_recording_are_shortened():
    """A clip of voice lines leaves silence between them; Chatterbox would copy it as
    the speaker's way of talking. Short pauses are part of speech and stay."""
    from kaden.frontends.desktop.voice.natural import PAUSE_S, without_long_pauses

    rate = 1000
    t = np.arange(rate) / rate

    def tone(seconds: float) -> np.ndarray:
        return (0.5 * np.sin(2 * np.pi * 50 * t[: int(seconds * rate)])).astype(np.float32)

    def quiet(seconds: float) -> np.ndarray:
        return np.zeros(int(seconds * rate), np.float32)

    clip = np.concatenate([quiet(1.0), tone(1), quiet(2.5), tone(1), quiet(0.15), tone(1),
                           quiet(3.0)])
    shortened = without_long_pauses(clip, rate)
    # The 2.5 s gap becomes a short pause, the 0.15 s one stays; the ends are trimmed.
    assert abs(len(shortened) / rate - (3 + PAUSE_S + 0.15)) < 0.05
    assert np.abs(shortened).max() == np.abs(clip).max()
    assert without_long_pauses(quiet(2), rate).size == 0


def test_a_sentence_said_with_repeats_is_told_from_one_said_right():
    """Whisper writes numbers as digits and may spell a name differently: neither is a
    flaw. A word or phrase said again, or words that were not meant, are."""
    from kaden.frontends.desktop.voice.engine import flaws

    meant = "I ran the tests, and all twelve hundred of them pass."
    assert flaws(meant, "I ran the tests and all 1,200 of them pass.") == 0
    assert flaws("Hi, I'm Kaden.", "Hi, I'm Caden.") == 0
    assert flaws("Want me to commit it?", "Want me to commit, commit it?") > 0
    assert flaws("Want me to commit it?", "Want me to commit it? Want me to commit it?") > 0
    assert flaws("Okay.", "Okay. Mm, hmm, so yeah, anyway, okay.") > 0
    assert flaws("Want me to commit it?", "") > 0  # nothing heard at all
    assert flaws("Sure thing.", "Sure thing? Oh...") > 0  # a sound it made up
    assert flaws("Hmm, let me think.", "Hmm, let me think.") == 0  # one that was meant
    assert flaws("", "") == 0


def test_a_flawed_sentence_is_made_again_and_the_best_one_kept():
    from kaden.frontends.desktop.voice.engine import checked

    takes = iter(["hi hi there", "hi there", "never asked for"])
    made: list[str] = []

    def speak(text: str) -> tuple[np.ndarray, int]:
        made.append(next(takes))
        return np.array([len(made)], np.float32), 24000

    def hear(samples: np.ndarray, rate: int) -> str:
        return made[int(samples[0]) - 1]

    samples, _rate = checked(speak, hear, "Hi there.", attempts=3)
    assert samples[0] == 2 and made == ["hi hi there", "hi there"]  # the second was right

    takes, made[:] = iter(["hi hi there", "hi hi hi there", "hi hi hi hi there"]), []
    samples, _ = checked(speak, hear, "Hi there.", attempts=3)
    assert samples[0] == 1 and len(made) == 3  # none right: the least wrong


async def test_talking_in_the_lifes_view_talks_with_the_life(make, qapp, tmp_path):
    """Voice follows the view: said to the life, and its reply read aloud."""
    from kaden.api import Role, Text
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.voice.mode import VoiceMode
    from kaden.frontends.desktop.window import MainWindow
    from tests.fakes.core import Step
    from tests.test_desktop_window import FakeLife

    h = await make()
    await h.start()
    them = await make()
    them.core.add(Step(text="Hi! I was just reading about maps."))
    await them.start()
    controller = Controller(h.runtime, ConversationModel())
    engine, speaker, microphones = ScriptedEngine("hello there"), Speaker(), []

    def microphone(heard) -> FakeMicrophone:
        microphones.append(FakeMicrophone(heard))
        return microphones[-1]

    voice = VoiceMode(controller, tmp_path / "voice", yes, engine=lambda _folder: engine,
                      microphone=microphone, speaker=lambda: speaker, models=(),
                      natural=False)
    window = MainWindow(h.runtime, controller, voice=voice, life=FakeLife(them.runtime))
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.continuous_switch.click()
    await eventually(lambda: window.showing_life)
    window.microphone_button.click()
    window.speaker_button.click()
    await eventually(lambda: voice.spoken and microphones)
    [mic] = microphones
    mic.heard(TALK)
    await eventually(lambda: "Hi!" in engine.spoke, timeout=5)
    said = [b.text for m in await them.transcript() if m.role is Role.USER
            for b in m.blocks if isinstance(b, Text)]
    assert said[0] == "hello there"  # it went to the life, not the user's chat


async def test_voice_can_be_loaded_at_start_without_a_word(tmp_path):
    """Loading at start: the models already downloaded are loaded and the voice made ready,
    with nothing asked or shown; a click meanwhile waits for that load, not a second."""
    import threading

    from kaden.frontends.desktop.voice.mode import VoiceMode

    engine, made, release = ScriptedEngine(), [], threading.Event()

    def make(folder: Path) -> ScriptedEngine:
        made.append(folder)
        release.wait(5)
        return engine

    asked: list[str] = []

    async def ask(text: str, details: str) -> bool:
        asked.append(text)
        return True

    mode = VoiceMode(Controller(), tmp_path, ask, engine=make, microphone=FakeMicrophone,
                     speaker=Speaker, models=(), natural=False)
    shown: list[str] = []
    mode.changed.connect(lambda: shown.append(mode.status))
    loading = asyncio.ensure_future(mode.preload())
    await eventually(lambda: made)
    assert shown == []  # in the background: the message box stays as it is
    mode.toggle_speaker()  # clicked while it loads
    release.set()
    await loading
    await eventually(lambda: mode.spoken)
    assert made == [tmp_path] and asked == [] and engine.warmed == ["Heart"]


async def test_models_not_yet_downloaded_are_left_for_the_first_click(tmp_path):
    from kaden.frontends.desktop.voice.download import Model
    from kaden.frontends.desktop.voice.mode import VoiceMode

    made: list[Path] = []
    asked: list[str] = []

    async def ask(text: str, details: str) -> bool:
        asked.append(text)
        return False

    mode = VoiceMode(Controller(), tmp_path, ask, engine=lambda folder: made.append(folder),
                     microphone=FakeMicrophone, speaker=Speaker, natural=False,
                     models=(Model("vad.onnx", "https://models.example/vad.onnx", 3, sha(b"vad"),
                                    False),))
    await mode.preload()
    assert made == [] and asked == [] and not mode.on


async def test_a_download_that_does_not_match_its_checksum_is_not_kept(tmp_path):
    import hashlib

    import httpx2

    from kaden.frontends.desktop.voice.download import Model, fetch, missing

    def serve(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"tampered")

    folder = tmp_path / "voice"
    bad = [Model("vad.onnx", "https://models.example/vad.onnx", 8, sha(b"original"),
                 archive=False)]
    with pytest.raises(OSError, match="checksum"):
        await fetch(folder, bad, lambda _done: None, transport=httpx2.MockTransport(serve))
    assert missing(folder, bad) == bad and list(folder.iterdir()) == []
    # Small files on Hugging Face are checked by their git blob hash.
    blob = "git-sha1:" + hashlib.sha1(b"blob 8\x00tampered").hexdigest()
    good = [Model("config.json", "https://models.example/config.json", 8, blob,
                  archive=False)]
    await fetch(folder, good, lambda _done: None, transport=httpx2.MockTransport(serve))
    assert missing(folder, good) == []


def test_every_speech_model_has_a_checksum():
    from kaden.frontends.desktop.voice.download import models

    for model in (*models(natural=True), *models(natural=False)):
        kind, _, value = model.digest.partition(":")
        assert kind in ("sha256", "git-sha1") and len(value) in (64, 40), model.name


def _controller(refuse: str) -> object:
    """A controller whose send is refused: at once ("now"), or once tried ("later")."""
    from types import SimpleNamespace

    notices: list[str] = []

    def send(text: str, files=(), *, spoken: bool = False, refused=None) -> None:
        notices.append(text)
        if refused is not None:
            if refuse == "now":
                refused()
            else:
                asyncio.get_running_loop().call_soon(refused)

    return SimpleNamespace(listeners=[], send=send, sent=notices,
                           model=SimpleNamespace(busy=False, notify=notices.append))


@pytest.mark.parametrize("refuse", ["now", "later"])
async def test_a_message_that_is_not_taken_leaves_voice_listening(tmp_path, refuse):
    from kaden.frontends.desktop.voice.mode import VoiceMode

    controller = _controller(refuse)
    microphones: list[FakeMicrophone] = []

    def microphone(heard) -> FakeMicrophone:
        microphones.append(FakeMicrophone(heard))
        return microphones[-1]

    mode = VoiceMode(controller, tmp_path, yes, engine=lambda _folder: ScriptedEngine("hi"),
                     microphone=microphone, speaker=Speaker, models=(), natural=False)
    mode.toggle_microphone()
    await eventually(lambda: mode._voice is not None and mode._voice.state is State.LISTENING)
    microphones[0].heard(TALK)
    await eventually(lambda: controller.sent == ["hi"])
    await eventually(lambda: mode._voice.state is State.LISTENING)
    mode.stop()


async def test_no_microphone_while_voice_reads_aloud_is_said_and_leaves_it_off(tmp_path):
    from types import SimpleNamespace

    from kaden.frontends.desktop.voice.audio import NoMicrophone
    from kaden.frontends.desktop.voice.mode import VoiceMode

    notices: list[str] = []
    controller = SimpleNamespace(listeners=[], model=SimpleNamespace(
        busy=False, notify=notices.append))

    class Missing(FakeMicrophone):
        def start(self) -> None:
            raise NoMicrophone("No microphone was found.")

    mode = VoiceMode(controller, tmp_path, yes, engine=lambda _folder: ScriptedEngine(),
                     microphone=Missing, speaker=Speaker, models=(), natural=False)
    mode.toggle_speaker()
    await eventually(lambda: mode.spoken)
    mode.toggle_microphone()  # nothing escapes the button's slot
    assert not mode.listening and mode.spoken
    assert notices == ["Voice could not listen: No microphone was found."]
    mode.stop()


async def test_a_second_preview_stops_the_first(tmp_path):
    from types import SimpleNamespace

    from kaden.frontends.desktop.voice.mode import VoiceMode

    controller = SimpleNamespace(listeners=[], model=SimpleNamespace(busy=False))
    speakers: list[Speaker] = []

    def speaker() -> Speaker:
        speakers.append(Speaker())
        speakers[-1].hold = asyncio.Event()
        return speakers[-1]

    mode = VoiceMode(controller, tmp_path, yes, engine=lambda _folder: ScriptedEngine(),
                     microphone=FakeMicrophone, speaker=speaker, models=(), natural=False)
    first = asyncio.create_task(mode.preview())
    await eventually(lambda: speakers and speakers[0].played)
    second = asyncio.create_task(mode.preview())
    await eventually(lambda: len(speakers) == 2 and speakers[1].played)
    assert speakers[0].stopped and speakers[0].closed  # never two at once
    speakers[1].stop()
    await asyncio.gather(first, second)
