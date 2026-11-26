"""The speech models: which ones, whether they are there, and getting them. They are not
shipped with Kaden; the app asks before it downloads them. Each download is checked
against its checksum before it is put in place."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class Model:
    name: str  # the file, or the folder its archive unpacks to, in the voice folder
    url: str
    size: int  # of the download, in bytes: for the question and the progress
    # What the download must hash to: "sha256:<hex>", or "git-sha1:<hex>", the git blob
    # hash Hugging Face gives for a small file it does not keep in LFS.
    digest: str
    archive: bool = True  # a .tar.bz2 holding the folder `name`


VAD = Model("silero_vad.onnx", f"{_RELEASES}/asr-models/silero_vad.onnx", 643_854,
            "sha256:9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
            archive=False)  # hears when the user talks
PARAKEET = Model(
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
    f"{_RELEASES}/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
    482_440_461,
    "sha256:6fc4426b54aa549a0019b8a75b8dea59164e71790a37ac56a045ad57f915f714",
)  # what they say, in English
KOKORO = Model("kokoro-multi-lang-v1_0",
               f"{_RELEASES}/tts-models/kokoro-multi-lang-v1_0.tar.bz2", 349_886_476,
               "sha256:60703707bc05ccd48d60e1eb723f38b07d6e474795a0b6a588d7220400e0f7a4")
# The natural voice: Chatterbox's English model, at the revision Kaden was tried with.
_CHATTERBOX = ("https://huggingface.co/ResembleAI/chatterbox/resolve/"
               "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18")
CHATTERBOX_DIR = "chatterbox"
CHATTERBOX = tuple(
    Model(f"{CHATTERBOX_DIR}/{file}", f"{_CHATTERBOX}/{file}", size, digest, archive=False)
    for file, size, digest in (
        ("ve.safetensors", 5_695_784,
         "sha256:f0921cab452fa278bc25cd23ffd59d36f816d7dc5181dd1bef9751a7fb61f63c"),
        ("t3_cfg.safetensors", 2_129_653_744,
         "sha256:914cb1696f47527fe8852ca8f1fe1fa63cb34f76f9c715e84e067b744dd0da81"),
        ("s3gen.safetensors", 1_056_484_620,
         "sha256:2b78103c654207393955e4900aac14a12de8ef25f4b09424f1ef91941f161d4e"),
        ("tokenizer.json", 25_470, "git-sha1:abd07c710243ba89bf1b21780e7c37ddde92334e"),
        ("conds.pt", 107_374,
         "sha256:6552d70568833628ba019c6b03459e77fe71ca197d5c560cef9411bee9d87f4e")))

# What listens with the natural voice: Whisper large-v3-turbo, on the GPU.
WHISPER_DIR = "whisper"
_WHISPER = ("https://huggingface.co/openai/whisper-large-v3-turbo/resolve/"
            "41f01f3fe87f28c78e2fbf8b568835947dd65ed9")
WHISPER = tuple(
    Model(f"{WHISPER_DIR}/{file}", f"{_WHISPER}/{file}", size, digest, archive=False)
    for file, size, digest in (
        ("model.safetensors", 1_617_824_864,
         "sha256:542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1"),
        ("config.json", 1_256, "git-sha1:ad2f44ff1ed66e12765b2392dc041469db91a462"),
        ("generation_config.json", 3_772,
         "git-sha1:cbe752958dc3e4671b0e0220aa1c545423a6d5f5"),
        ("preprocessor_config.json", 340,
         "git-sha1:931c77a740890c46365c7ae0c9d350ba3cca908f"),
        ("tokenizer.json", 2_710_337, "git-sha1:17456db595adc78a973f97d69d8cb50bc87c0b1c"),
        ("tokenizer_config.json", 282_843,
         "git-sha1:06ffdc8308eae6bb7bd1fdd81e94b0a881a539ab"),
        ("vocab.json", 1_036_558, "git-sha1:0f3456460629e21d559c6daa23ab6ce3644e8271"),
        ("merges.txt", 493_869, "git-sha1:6038932a2a1f09a66991b1c2adae0d14066fa29e"),
        ("added_tokens.json", 34_648, "git-sha1:1b33526d33aaa60d79f78ae8651dae50b730185a"),
        ("special_tokens_map.json", 2_186,
         "git-sha1:312bc106291bb51bf2cc1648df070bef963a0639"),
        ("normalizer.json", 52_666, "git-sha1:dd6ae819ad738ac1a546e9f9282ef325c33b9ea0")))


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
    is in place only once it is whole and matches its checksum, so a download that was cut
    off or changed starts again. httpx2.HTTPError or OSError when it fails."""
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    total, done = sum(m.size for m in models) or 1, 0
    async with httpx2.AsyncClient(transport=transport, follow_redirects=True,
                                  timeout=httpx2.Timeout(30, read=120)) as client:
        for model in models:
            part = folder / f"{model.name}.part"
            await asyncio.to_thread(part.parent.mkdir, parents=True, exist_ok=True)
            digest = _hasher(model)
            async with client.stream("GET", model.url) as response:
                response.raise_for_status()
                with await asyncio.to_thread(part.open, "wb") as out:
                    async for chunk in response.aiter_bytes(CHUNK):
                        await asyncio.to_thread(out.write, chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        progress(min(done / total, 1.0))
            if digest.hexdigest() != model.digest.partition(":")[2]:
                await asyncio.to_thread(part.unlink)
                raise OSError(f"The download of {model.name} does not match its checksum; "
                              "try again.")
            await asyncio.to_thread(_put_in_place, part, folder, model)
    progress(1.0)


def _hasher(model: Model) -> Any:
    kind = model.digest.partition(":")[0]
    if kind == "git-sha1":  # git hashes a blob after a header with its size
        return hashlib.sha1(b"blob %d\x00" % model.size, usedforsecurity=False)
    if kind == "sha256":
        return hashlib.sha256()
    raise ValueError(f"unknown checksum {model.digest!r}")


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
