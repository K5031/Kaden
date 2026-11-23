"""The speech models: which ones, whether they are there, and getting them. They are not
shipped with Kaden; the app asks before it downloads them."""

from __future__ import annotations

import asyncio
import shutil
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class Model:
    name: str  # the file, or the folder its archive unpacks to, in the voice folder
    url: str
    size: int  # of the download, in bytes: for the question and the progress
    archive: bool = True  # a .tar.bz2 holding the folder `name`


VAD = Model("silero_vad.onnx", f"{_RELEASES}/asr-models/silero_vad.onnx", 643_854,
            archive=False)  # hears when the user talks
PARAKEET = Model("sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
                 f"{_RELEASES}/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
                 482_440_461)  # what they say, in English
KOKORO = Model("kokoro-multi-lang-v1_0",
               f"{_RELEASES}/tts-models/kokoro-multi-lang-v1_0.tar.bz2", 349_000_000)
# The natural voice: Chatterbox's English model, at the revision Kaden was tried with.
CHATTERBOX_DIR = "chatterbox"
_CHATTERBOX = ("https://huggingface.co/ResembleAI/chatterbox/resolve/"
               "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18")
CHATTERBOX = tuple(
    Model(f"{CHATTERBOX_DIR}/{file}", f"{_CHATTERBOX}/{file}", size, archive=False)
    for file, size in (("ve.safetensors", 5_695_784), ("t3_cfg.safetensors", 2_129_653_744),
                       ("s3gen.safetensors", 1_056_484_620), ("tokenizer.json", 25_470),
                       ("conds.pt", 107_374)))


# What listens with the natural voice: Whisper large-v3-turbo, on the GPU.
WHISPER_DIR = "whisper"
_WHISPER = ("https://huggingface.co/openai/whisper-large-v3-turbo/resolve/"
            "41f01f3fe87f28c78e2fbf8b568835947dd65ed9")
WHISPER = tuple(
    Model(f"{WHISPER_DIR}/{file}", f"{_WHISPER}/{file}", size, archive=False)
    for file, size in (("model.safetensors", 1_617_824_864), ("config.json", 1_256),
                       ("generation_config.json", 3_772), ("preprocessor_config.json", 340),
                       ("tokenizer.json", 2_710_337), ("tokenizer_config.json", 282_843),
                       ("vocab.json", 1_036_558), ("merges.txt", 493_869),
                       ("added_tokens.json", 34_648), ("special_tokens_map.json", 2_186),
                       ("normalizer.json", 52_666)))


def models(*, natural: bool) -> tuple[Model, ...]:
    """What voice mode needs: with the natural voice, Whisper listens and Chatterbox
    speaks, on the GPU; otherwise Parakeet and Kokoro, on the CPU."""
    return (VAD, *WHISPER, *CHATTERBOX) if natural else (VAD, PARAKEET, KOKORO)


def missing(folder: Path, models: Sequence[Model]) -> list[Model]:
    """The models not in `folder` yet. Blocking."""
    return [m for m in models if not (folder / m.name).exists()]


def megabytes(models: Sequence[Model]) -> int:
    return round(sum(m.size for m in models) / 1e6)


async def fetch(folder: Path, models: Sequence[Model], progress: Callable[[float], object],
                *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
    """Download `models` into `folder`, calling `progress` with the fraction done. A model
    is in place only once it is whole, so a download that was cut off starts again.
    httpx2.HTTPError or OSError when it fails."""
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    total, done = sum(m.size for m in models) or 1, 0
    async with httpx2.AsyncClient(transport=transport, follow_redirects=True,
                                  timeout=httpx2.Timeout(30, read=120)) as client:
        for model in models:
            part = folder / f"{model.name}.part"
            await asyncio.to_thread(part.parent.mkdir, parents=True, exist_ok=True)
            async with client.stream("GET", model.url) as response:
                response.raise_for_status()
                with await asyncio.to_thread(part.open, "wb") as out:
                    async for chunk in response.aiter_bytes(CHUNK):
                        await asyncio.to_thread(out.write, chunk)
                        done += len(chunk)
                        progress(min(done / total, 1.0))
            await asyncio.to_thread(_put_in_place, part, folder, model)
    progress(1.0)


def _put_in_place(part: Path, folder: Path, model: Model) -> None:
    if not model.archive:
        part.replace(folder / model.name)
        return
    unpacking = folder / f"{model.name}.unpacking"
    shutil.rmtree(unpacking, ignore_errors=True)
    with tarfile.open(part, "r:bz2") as archive:
        archive.extractall(unpacking, filter="data")
    (unpacking / model.name).replace(folder / model.name)
    shutil.rmtree(unpacking, ignore_errors=True)
    part.unlink()
