"""The natural voice on an NVIDIA GPU: Chatterbox (Resemble AI) speaks, in its own voice
or one copied from a recording, and Whisper (OpenAI) listens. Needs `kaden[voice-gpu]`;
torch, chatterbox and transformers are imported only when it loads."""

from __future__ import annotations

import io
import re
import threading
from importlib.util import find_spec
from itertools import pairwise
from pathlib import Path

import numpy as np

from kaden.frontends.desktop.voice.download import CHATTERBOX_DIR, WHISPER_DIR

NATURAL = "Natural"  # its own voice
RECORDINGS_DIR = "voices"  # recordings the user added, in the voice folder, by name
RECORDING_TYPES = (".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a")
# A clip of voice lines leaves silence between them, which Chatterbox would copy as the
# speaker's way of talking: a pause in a recording longer than this is cut to it.
PAUSE_S = 0.25
QUIET_DB = 35  # this far below the loudest of a recording is silence


def installed() -> bool:
    """`kaden[voice-gpu]` is installed. Does not import it."""
    return find_spec("chatterbox") is not None and find_spec("torch") is not None


def without_long_pauses(audio: np.ndarray, rate: int) -> np.ndarray:
    """`audio` with each pause longer than PAUSE_S cut to PAUSE_S, and none at its ends;
    empty if it is all silence. Judged in steps of 10 ms."""
    step = max(1, rate // 100)
    steps = len(audio) // step
    loudness = np.sqrt(np.mean(audio[: steps * step].reshape(steps, step) ** 2, axis=1))
    if not steps or not loudness.max():
        return audio[:0]
    loud = np.flatnonzero(loudness > loudness.max() * 10 ** (-QUIET_DB / 20))
    keep = np.zeros(steps, bool)
    keep[loud] = True
    longest = round(PAUSE_S * 100)
    for before, after in pairwise(loud):
        if after - before - 1 > longest:  # half the pause it may keep at each side
            keep[before + 1: before + 1 + longest // 2] = True
            keep[after - (longest - longest // 2): after] = True
        else:
            keep[before + 1: after] = True
    return audio[: steps * step].reshape(steps, step)[keep].ravel()


# What Whisper writes on noise, with confidence: said by someone, these come with more.
_STOCK = frozenset({"thank you", "thank you so much", "thank you very much", "thanks",
                    "thanks for watching", "thank you for watching", "you", "bye",
                    "bye bye"})
_MARKS = re.compile(r"[\s.,!?;:…'\"]+")


def stock_phrase(heard: str) -> bool:
    """`heard` is nothing but one of Whisper's habitual inventions on noise."""
    return _MARKS.sub(" ", heard.lower()).strip() in _STOCK


HEARD_S = 30  # of a recording, what it hears: its first half minute of talking
READ_S = 90  # read to find HEARD_S once pauses are out


def start_of(path: Path) -> tuple[np.ndarray, int]:
    """What the voice hears of the recording at `path`: its first HEARD_S of talking, long
    pauses out (Chatterbox takes the voice's detail from its first 10 s and its overall
    sound from all of it; minutes of a video take seconds to read and blend in whatever
    else they hold). Blocking; needs `kaden[voice-gpu]`."""
    import librosa

    audio, rate = librosa.load(path, sr=None, mono=True, duration=READ_S)
    return without_long_pauses(audio, rate)[: HEARD_S * rate], rate


def prepare_recording(source: Path, target: Path) -> None:
    """Keep at `target`, a WAV file, what the voice hears of the recording at `source`
    (start_of): done once, when the recording is added. Blocking."""
    import soundfile

    audio, rate = start_of(source)
    soundfile.write(target, audio, rate)


def recording(folder: Path, name: str) -> Path | None:
    """The recording a voice named `name` is copied from. Blocking."""
    found = (folder / RECORDINGS_DIR).iterdir() if (folder / RECORDINGS_DIR).is_dir() else ()
    return next((path for path in sorted(found)
                 if path.suffix.lower() in RECORDING_TYPES and path.stem == name), None)


class Chatterbox:
    """engine.Speech with Chatterbox, as NATURAL or a recording's name. Loading takes
    seconds and blocks; RuntimeError where there is no CUDA GPU. `speak` and `warm` may
    run on different threads at once: they take turns."""


    def __init__(self, models: Path) -> None:
        import torch
        from chatterbox.tts import ChatterboxTTS

        if not torch.cuda.is_available():
            raise RuntimeError("the natural voice needs an NVIDIA GPU, and none is usable")
        self.voice = NATURAL
        self._folder = models
        self._model = ChatterboxTTS.from_local(models / CHATTERBOX_DIR, "cuda")
        self._natural = self._model.conds
        # What each recording sounds like, by its file and when that last changed: a
        # voice removed and added again under the same name is a new one.
        self._voices: dict[tuple[Path, int], object] = {}
        self._torch = torch
        self._turn = threading.Lock()  # the model holds the voice it speaks in

    def speak(self, text: str) -> tuple[np.ndarray, int]:
        with self._turn:
            self._model.conds = self._sound_of(self.voice)
            with self._torch.inference_mode():
                wav = self._model.generate(text)
        return wav.squeeze(0).float().cpu().numpy(), self._model.sr

    def warm(self) -> None:
        """Read the chosen recording now, not at its first sentence (seconds for a long
        one)."""
        with self._turn:
            self._sound_of(self.voice)

    def _sound_of(self, name: str) -> object:
        path = None if name == NATURAL else recording(self._folder, name)
        if path is None:  # NATURAL, or removed since it was chosen
            return self._natural
        key = (path, path.stat().st_mtime_ns)
        if key not in self._voices:
            self._model.prepare_conditionals(self._start_of(path))
            self._voices[key] = self._model.conds
        return self._voices[key]

    def _start_of(self, path: Path) -> io.BytesIO:
        """What of the recording at `path` it hears (start_of), as a WAV file in memory."""
        import soundfile

        audio, rate = start_of(path)
        start = io.BytesIO()
        soundfile.write(start, audio, rate, format="WAV")
        start.seek(0)
        return start



class Whisper:
    """engine.Listener with Whisper large-v3-turbo on the GPU, in half precision, in
    English. Loading blocks; RuntimeError where there is no CUDA GPU."""

    RATE = 16000
    WINDOW_S = 30  # what Whisper hears at once; longer speech is read in windows
    # Whisper writes words even for a cough or a noise ("so", "Okay.", "you"), but
    # unsure of them: what it writes with less average confidence (log-probability per
    # word piece) than this was not speech. The turbo model's own no-speech rating stays
    # at zero, even for silence.
    SURE = -0.8

    def __init__(self, models: Path) -> None:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        if not torch.cuda.is_available():
            raise RuntimeError("the natural voice needs an NVIDIA GPU, and none is usable")
        weights = models / WHISPER_DIR
        self._turn = threading.Lock()  # what the user said, and speech heard back
        self._processor = WhisperProcessor.from_pretrained(weights)
        # Loaded at the default float32, then halved: loading at float16 sets torch's
        # default dtype for the whole process while it runs, and Chatterbox, loading on
        # another thread at the same time, would build layers in half precision.
        self._model = WhisperForConditionalGeneration.from_pretrained(
            weights, dtype=torch.float32).to("cuda", torch.float16).eval()
        self._torch = torch

    def transcribe(self, samples: np.ndarray, *, guarded: bool = True) -> str:
        long = len(samples) > self.WINDOW_S * self.RATE
        inputs = self._processor(
            samples, sampling_rate=self.RATE, return_tensors="pt",
            **({"truncation": False, "padding": "longest", "return_attention_mask": True}
               if long else {}))
        features = inputs.input_features.to("cuda", self._torch.float16)
        extra = {"attention_mask": inputs.attention_mask.to("cuda"),
                 "return_timestamps": True} if long else {}
        with self._turn, self._torch.inference_mode():
            if long:  # half a minute of noise does not get past the VAD
                ids = self._model.generate(features, language="en", task="transcribe",
                                           **extra)
            else:
                out = self._model.generate(features, language="en", task="transcribe",
                                           return_dict_in_generate=True, output_scores=True)
                ids = out.sequences
                sureness = self._model.compute_transition_scores(
                    ids, out.scores, normalize_logits=True)[0, :-1].float()  # not the end
                if guarded and (not len(sureness) or float(sureness.mean()) < self.SURE):
                    return ""
        text = self._processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        return "" if guarded and stock_phrase(text) else text
