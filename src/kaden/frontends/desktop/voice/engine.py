"""Speech on this machine with sherpa-onnx: Silero VAD hears when the user talks,
Parakeet turns it into text, Kokoro speaks. All on the CPU; with the natural voice,
Whisper (natural.Whisper) and Chatterbox (natural.Chatterbox) take Parakeet's and
Kokoro's places, on the GPU."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Protocol

import numpy as np
import sherpa_onnx

from kaden.frontends.desktop.voice.download import PARAKEET
from kaden.frontends.desktop.voice.mode import KOKORO_VOICES

RATE = 16000  # what the microphone is heard at
VAD_FILE = "silero_vad.onnx"
SPEAK_DIR = "kokoro-multi-lang-v1_0"
END_OF_TURN_S = 0.9  # silence that ends what the user is saying
MIN_SPEECH_S = 0.25  # shorter sounds are not speech


def _threads() -> int:
    return max(1, min(8, (os.cpu_count() or 2) // 2))


class Speech(Protocol):
    voice: str  # the name it speaks as

    def speak(self, text: str) -> tuple[np.ndarray, int]: ...

    def warm(self) -> None: ...


_WORD = re.compile(r"[a-z']+|\d[\d,.]*")
# Sounds a speech model makes up around a sentence; any not in the sentence is a flaw.
_FILLERS = frozenset({"oh", "ah", "uh", "um", "uhm", "er", "erm", "em", "emm", "hm", "hmm",
                      "mm", "mhm", "huh"})


def flaws(meant: str, heard: str) -> int:
    """How badly `heard`, what a listener made of a sentence spoken, strays from
    `meant`, the sentence: 0 when it says it as meant. Digits are left out (a listener
    writes "1,200" for "twelve hundred"), and a word or two heard differently (a name)
    is not a flaw; a word or phrase said again, a sound like "hmm" that was not meant, or
    words that were not meant, are."""
    said, got = ([w for w in _WORD.findall(text.lower()) if not w[0].isdigit()]
                 for text in (meant, heard))
    if not said:
        return 0
    if not got:
        return len(said)
    doubled = sum(a == b for a, b in pairwise(got)) - sum(a == b for a, b in pairwise(said))
    pairs, meant_pairs = Counter(pairwise(got)), Counter(pairwise(said))
    again = sum(n - max(meant_pairs[pair], 1) for pair, n in pairs.items() if n > 1)
    added = Counter(got) - Counter(said)
    sounds = sum(n for word, n in added.items() if word in _FILLERS)
    extra = sum(added.values()) - sounds
    return (max(doubled, 0) + max(again, 0) + sounds
            + (extra if extra >= max(3, len(said) * 0.3) else 0))


def checked(speak: Callable[[str], tuple[np.ndarray, int]],
            hear: Callable[[np.ndarray, int], str], text: str, *,
            attempts: int = 3) -> tuple[np.ndarray, int]:
    """`speak(text)`, heard back with `hear` and made again while it has flaws, up to
    `attempts` times; the take with fewest flaws."""
    best: tuple[int, tuple[np.ndarray, int]] | None = None
    for _ in range(attempts):
        take = speak(text)
        score = flaws(text, hear(*take))
        if best is None or score < best[0]:
            best = (score, take)
        if not score:
            break
    assert best is not None
    return best[1]


class Listener(Protocol):
    def transcribe(self, samples: np.ndarray, *, guarded: bool = True) -> str:
        """What was said in `samples` (float32, mono, at RATE). `guarded`: "" for what may
        well not be speech, as from a microphone; not for speech it was made to hear."""


class Parakeet:
    """A Listener with Parakeet, in English. Loading blocks."""

    def __init__(self, models: Path) -> None:
        listen = models / PARAKEET.name
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(listen / "encoder.int8.onnx"), decoder=str(listen / "decoder.int8.onnx"),
            joiner=str(listen / "joiner.int8.onnx"), tokens=str(listen / "tokens.txt"),
            num_threads=_threads(), model_type="nemo_transducer")

    def transcribe(self, samples: np.ndarray, *, guarded: bool = True) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(RATE, samples)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()


class Kokoro:
    """Speech with Kokoro, as one of KOKORO_VOICES. Loading blocks."""

    def __init__(self, models: Path) -> None:
        self.voice = "Heart"
        speak = models / SPEAK_DIR
        tts = sherpa_onnx.OfflineTtsConfig()
        tts.model.kokoro.model = str(speak / "model.onnx")
        tts.model.kokoro.voices = str(speak / "voices.bin")
        tts.model.kokoro.tokens = str(speak / "tokens.txt")
        tts.model.kokoro.data_dir = str(speak / "espeak-ng-data")
        tts.model.kokoro.lexicon = str(speak / "lexicon-us-en.txt")
        tts.model.num_threads = _threads()
        self._tts = sherpa_onnx.OfflineTts(tts)

    def speak(self, text: str) -> tuple[np.ndarray, int]:
        audio = self._tts.generate(text, sid=KOKORO_VOICES[self.voice], speed=1.0)
        return np.asarray(audio.samples, dtype=np.float32), audio.sample_rate

    def warm(self) -> None:
        """Every voice is ready once loaded."""


class SherpaEngine:
    """turns.Engine over the models in `models` (see download.models): Silero hears when
    the user talks, `listener` what they say, `speech` speaks. `check`: each sentence
    spoken is heard back and made again when it came out with repeats (see `checked`),
    for a speech model that sometimes repeats itself. Loading takes a few seconds and
    blocks: build it on a thread."""

    def __init__(self, models: Path, speech: Speech, listener: Listener, *,
                 check: bool = False) -> None:
        self.speech, self.listener, self.check = speech, listener, check
        vad = sherpa_onnx.VadModelConfig()
        vad.silero_vad.model = str(models / VAD_FILE)
        vad.silero_vad.min_silence_duration = END_OF_TURN_S
        vad.silero_vad.min_speech_duration = MIN_SPEECH_S
        vad.sample_rate = RATE
        self._window = vad.silero_vad.window_size
        self._vad = sherpa_onnx.VoiceActivityDetector(vad, buffer_size_in_seconds=120)
        self._heard = np.zeros(0, dtype=np.float32)  # not yet a whole window

    def hear(self, samples: np.ndarray) -> list[np.ndarray]:
        self._heard = np.concatenate((self._heard, samples.astype(np.float32, copy=False)))
        while len(self._heard) >= self._window:
            self._vad.accept_waveform(self._heard[: self._window])
            self._heard = self._heard[self._window:]
        said = []
        while not self._vad.empty():
            said.append(np.array(self._vad.front.samples, dtype=np.float32))
            self._vad.pop()
        return said

    def reset(self) -> None:
        self._vad.reset()
        self._heard = np.zeros(0, dtype=np.float32)

    def transcribe(self, samples: np.ndarray) -> str:
        return self.listener.transcribe(samples)

    @property
    def voice(self) -> str:
        return self.speech.voice

    @voice.setter
    def voice(self, name: str) -> None:
        self.speech.voice = name

    def speak(self, text: str, *, check: bool = True) -> tuple[np.ndarray, int]:
        if not (self.check and check):
            return self.speech.speak(text)
        return checked(self.speech.speak, self._hear_back, text)

    def _hear_back(self, samples: np.ndarray, rate: int) -> str:
        at_rate = np.interp(np.arange(0, len(samples), rate / RATE), np.arange(len(samples)),
                            samples).astype(np.float32)
        return self.listener.transcribe(at_rate, guarded=False)

    def warm(self) -> None:
        self.speech.warm()
