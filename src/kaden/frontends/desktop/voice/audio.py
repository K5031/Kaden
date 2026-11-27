"""Sound in and out through Qt Multimedia: the microphone as the engine hears it, and a
speaker that plays what the engine says."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QTimer
from PySide6.QtMultimedia import QAudioDevice, QAudioFormat, QAudioSink, QAudioSource, QMediaDevices

from kaden.frontends.desktop.voice.engine import RATE

# Qt's audio enums are compared by name: PySide6 has them twice (QAudio and QtAudio), and
# a value of one never equals the other's, so a device's NoError would look like an error.
_DTYPES = {QAudioFormat.SampleFormat.Int16: (np.int16, 32768.0),
           QAudioFormat.SampleFormat.Int32: (np.int32, 2147483648.0),
           QAudioFormat.SampleFormat.Float: (np.float32, 1.0),
           QAudioFormat.SampleFormat.UInt8: (np.uint8, None)}


def as_heard(data: bytes, rate: int, channels: int,
             sample_format: QAudioFormat.SampleFormat) -> np.ndarray:
    """Raw microphone sound as the engine takes it: float32, mono, at engine.RATE."""
    dtype, scale = _DTYPES[sample_format]
    frame = np.dtype(dtype).itemsize * channels
    raw = np.frombuffer(data[: len(data) - len(data) % frame], dtype=dtype)
    samples = ((raw.astype(np.float32) - 128.0) / 128.0 if scale is None
               else raw.astype(np.float32) / scale)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(samples):
        at = np.arange(0, len(samples), rate / RATE)
        samples = np.interp(at, np.arange(len(samples)), samples).astype(np.float32)
    return samples


class NoMicrophone(Exception):
    """There is no microphone to listen to."""


class Microphone(QObject):
    """Hands what the default microphone hears to `heard`, while started."""

    def __init__(self, heard: Callable[[np.ndarray], object]) -> None:
        super().__init__()
        self._heard = heard
        self._source: QAudioSource | None = None
        self._io: QIODevice | None = None
        self._format = QAudioFormat()

    def start(self) -> None:
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise NoMicrophone("No microphone was found.")
        wanted = QAudioFormat()
        wanted.setSampleRate(RATE)
        wanted.setChannelCount(1)
        wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._format = wanted if device.isFormatSupported(wanted) else device.preferredFormat()
        if self._format.sampleFormat() not in _DTYPES:
            raise NoMicrophone("The microphone's sound format is not supported.")
        self._source = QAudioSource(device, self._format, self)
        self._io = self._source.start()
        if self._io is None or self._source.error().name != "NoError":
            self.stop()
            raise NoMicrophone("The microphone could not be opened.")
        self._io.readyRead.connect(self._read)

    def stop(self) -> None:
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
        self._source = self._io = None

    def _read(self) -> None:
        if self._io is None:
            return
        data = self._io.readAll().data()
        if data:
            self._heard(as_heard(data, self._format.sampleRate(), self._format.channelCount(),
                                 self._format.sampleFormat()))


def as_played(samples: np.ndarray, rate: int) -> tuple[QAudioFormat, bytes]:
    """Mono speech as the sound output gets it: 16-bit stereo, each sample in both
    channels. Sent as one channel, it reaches only the left ear on some sound servers
    (PipeWire with Qt's output)."""
    sound = QAudioFormat()
    sound.setSampleRate(rate)
    sound.setChannelCount(2)
    sound.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    mono = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    return sound, np.repeat(mono, 2).tobytes()


class Speaker(QObject):
    """turns.Player on the default sound output: one stream, open from the first sound
    until `close`, with silence between sounds. A stream opened for each sound would
    start each one cold, and the sound chip, asleep after a pause, loses its first
    syllable while it wakes. A stream stays on the device it was opened on, so a sound
    that starts after the system's default output changed opens a new one there."""

    TICK_MS = 10
    AHEAD_S = 0.05  # silence kept queued so the stream never runs dry

    def __init__(self) -> None:
        super().__init__()
        self._sink: QAudioSink | None = None
        self._out: QIODevice | None = None
        self._format: QAudioFormat | None = None
        self._device: QAudioDevice | None = None
        self._pending = bytearray()  # of the sound being played, not yet handed to Qt
        self._written = 0  # bytes handed to Qt since the stream (re)started
        self._ends_at = 0  # where the sound being played ends, in those bytes
        self._done: asyncio.Future[None] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._feed)

    async def play(self, samples: np.ndarray, rate: int) -> None:
        self.stop()
        if not len(samples):
            return
        sound, pcm = as_played(samples, rate)
        self._open(sound)
        self._pending += pcm
        self._ends_at = self._written + len(self._pending)
        done = self._done = asyncio.get_running_loop().create_future()
        self._feed()
        await done

    def stop(self) -> None:
        """Stop the sound being played at once; the stream stays open."""
        self._pending.clear()
        if self._sink is not None and self._done is not None and not self._done.done():
            self._sink.reset()  # drops what Qt still holds of it
            self._out, self._written = self._sink.start(), 0
        self._finish()

    def close(self) -> None:
        self._pending.clear()
        self._finish()
        self._timer.stop()
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
        self._sink = self._out = self._format = self._device = None

    def _open(self, sound: QAudioFormat) -> None:
        device = QMediaDevices.defaultAudioOutput()
        if self._sink is not None and self._format == sound and self._device == device:
            return
        self.close()
        self._format, self._device = sound, device
        self._sink = QAudioSink(device, sound, self)
        self._out, self._written = self._sink.start(), 0
        self._timer.start()

    def _feed(self) -> None:
        sink, out, sound = self._sink, self._out, self._format
        if sink is None or out is None or sound is None:
            return
        if sink.state().name == "StoppedState" and sink.error().name != "NoError":
            self.close()  # the device failed: nothing more is heard
            return
        frame = sound.bytesPerFrame()
        if self._pending:
            room = sink.bytesFree() - sink.bytesFree() % frame
            chunk = bytes(self._pending[:room])
            if chunk:
                written = max(out.write(chunk), 0)
                del self._pending[:written]
                self._written += written
        else:
            queued = self._written - sound.bytesForDuration(sink.processedUSecs())
            silence = sound.bytesForDuration(int(self.AHEAD_S * 1e6)) - queued
            silence -= silence % frame
            if silence > 0 and sink.bytesFree() >= silence:
                self._written += max(out.write(bytes(silence)), 0)
        if self._done is not None and not self._pending and \
                sound.bytesForDuration(sink.processedUSecs()) >= self._ends_at:
            self._finish()

    def _finish(self) -> None:
        if self._done is not None and not self._done.done():
            self._done.set_result(None)
        self._done = None
