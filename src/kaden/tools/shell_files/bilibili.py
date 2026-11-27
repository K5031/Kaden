"""Watching Bilibili as a visitor, for the built-in Tools: a video's details and subtitles,
its danmaku, its comments, and frames of it.

Bilibili's own web API, with no login: what a visitor in a browser is shown. Its
signing of requests (WBI) is done as its pages do; its check against automated visitors
is not passed, which is why there is no search (it answers a visitor's search with a
captcha). Frames are cut from the video's stream by the system's ffmpeg.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import re
import shutil
import tempfile
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2

from kaden.api import DESCRIPTION_ARG, DESCRIPTION_SCHEMA, Image, ToolOutput, ToolSpec
from kaden.tools.shell_files.attachments import MAX_IMAGE_BYTES

AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
         "Chrome/130.0 Safari/537.36")
HEADERS = {"User-Agent": AGENT, "Referer": "https://www.bilibili.com/"}
API = "https://api.bilibili.com"
TIMEOUT = httpx2.Timeout(20, read=30)
KEYS_FOR_S = 3600  # how long the signing keys are used before they are read again
MAX_FRAMES = 6
FRAME_HEIGHT = 480
CUT_TIMEOUT = 60.0
# The order Bilibili's pages mix the two signing keys in (WBI's "mixin key").
_MIXIN = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33,
          9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17,
          0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52)
_BV = re.compile(r"\bBV[0-9A-Za-z]{10}\b")
_SHORT = re.compile(r"https?://(?:www\.)?b23\.tv/\S+")
_DANMAKU = re.compile(r'<d p="([^"]*)">([^<]*)</d>')

_VIDEO = {"video": {"type": "string",
                    "description": "The video: its BV id, or a link to it (b23.tv too)"},
          "part": {"type": "integer", "minimum": 1,
                   "description": "Which part of a video in parts (default: the first)"},
          DESCRIPTION_ARG: DESCRIPTION_SCHEMA}
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="BiliVideo",
        description=(
            "A Bilibili video's details (title, uploader, date, length, views, likes, "
            "description, its parts) and what is said in it, from its subtitles where "
            "it has any open to visitors. Read as a visitor, without logging in."),
        parameters={"type": "object", "properties": _VIDEO, "required": ["video"],
                    "additionalProperties": False},
        read_only=True),
    ToolSpec(
        name="BiliDanmaku",
        description=(
            "A Bilibili video's danmaku: what viewers wrote, each at its moment in the "
            "video. from_s and to_s keep a stretch of it."),
        parameters={"type": "object", "properties": {
            **_VIDEO,
            "from_s": {"type": "number", "minimum": 0},
            "to_s": {"type": "number", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000,
                      "description": "At most this many, the earliest first (default 300)"}},
            "required": ["video"], "additionalProperties": False},
        read_only=True, deferred=True),
    ToolSpec(
        name="BiliComments",
        description="A Bilibili video's top comments, with their likes and top replies.",
        parameters={"type": "object", "properties": {
            **_VIDEO,
            "limit": {"type": "integer", "minimum": 1, "maximum": 20,
                      "description": "At most this many (default 10)"}},
            "required": ["video"], "additionalProperties": False},
        read_only=True, deferred=True),
    ToolSpec(
        name="BiliFrames",
        description=(
            "Pictures of a Bilibili video at the moments given, in seconds from its "
            f"start, to look at: up to {MAX_FRAMES} at once."),
        parameters={"type": "object", "properties": {
            **_VIDEO,
            "seconds": {"type": "array", "items": {"type": "number", "minimum": 0},
                        "minItems": 1, "maxItems": MAX_FRAMES}},
            "required": ["video", "seconds"], "additionalProperties": False},
        read_only=True, timeout=180.0, deferred=True),
)
NAMES = frozenset(spec.name for spec in SPECS)

type CutFn = Callable[[str, float, Path], Awaitable[None]]


class BiliError(Exception):
    """Expected failure, reported to the model as an error result."""


def clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


class Bilibili:
    """The Bilibili tools. `transport` and `cut` (the frame cutter, ffmpeg by default)
    are replaced in tests; `ffmpeg`: where it is, None where it is not installed."""

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None,
                 ffmpeg: str | None = shutil.which("ffmpeg"), cut: CutFn | None = None) -> None:
        self._transport = transport
        self._ffmpeg = ffmpeg
        self._cut = cut or self._ffmpeg_cut
        self._client: httpx2.AsyncClient | None = None
        self._starting = asyncio.Lock()
        self._mixin: tuple[str, float] | None = None  # the signing key, and when read

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(s for s in SPECS if s.name != "BiliFrames" or self._ffmpeg is not None)

    async def run(self, name: str, args: dict[str, Any]) -> str | ToolOutput:
        match name:
            case "BiliVideo":
                return await self.video(args)
            case "BiliDanmaku":
                return await self.danmaku(args)
            case "BiliComments":
                return await self.comments(args)
            case _:
                return await self.frames(args)

    # ── The tools ─────────────────────────────────────────────────────────

    async def video(self, args: dict[str, Any]) -> str:
        info = await self._view(args["video"])
        stat = info.get("stat", {})
        pages = info.get("pages") or []
        made = datetime.fromtimestamp(info.get("pubdate", 0)).date().isoformat()
        owner = info.get("owner", {})
        uploader = owner.get("name", "?") + (f" (uploader id {owner['mid']})"
                                             if owner.get("mid") else "")
        lines = [f"{info['title']} ({info['bvid']})",
                 (f"By {uploader}, {made}, "
                  f"{clock(info.get('duration', 0))} long"),
                 (f"{stat.get('view', 0):,} views, {stat.get('like', 0):,} likes, "
                  f"{stat.get('danmaku', 0):,} danmaku, {stat.get('reply', 0):,} comments")]
        if description := (info.get("desc") or "").strip():
            lines += ["", description]
        if len(pages) > 1:
            lines += ["", "Parts:"] + [f"{p['page']}. {p.get('part', '')} "
                                       f"({clock(p.get('duration', 0))})" for p in pages]
        lines += ["", await self._subtitles(info["bvid"], self._cid(info, args.get("part")))]
        return "\n".join(lines)

    async def danmaku(self, args: dict[str, Any]) -> str:
        info = await self._view(args["video"])
        response = await self._get(f"{API}/x/v1/dm/list.so",
                                   {"oid": self._cid(info, args.get("part"))})
        said = sorted((float(p.split(",", 1)[0]), html.unescape(text))
                      for p, text in _DANMAKU.findall(response.text))
        start, end = args.get("from_s", 0.0), args.get("to_s", float("inf"))
        kept = [(t, text) for t, text in said if start <= t <= end][: args.get("limit", 300)]
        head = f"{len(kept)} danmaku" + (f" (of {len(said)})" if len(kept) < len(said) else "")
        return "\n".join([head, *(f"[{clock(t)}] {text}" for t, text in kept)])

    async def comments(self, args: dict[str, Any]) -> str:
        info = await self._view(args["video"])
        data = await self._json(f"{API}/x/v2/reply/wbi/main",
                                await self._signed({"oid": info["aid"], "type": 1, "mode": 3}))
        replies = (data or {}).get("replies") or []
        if not replies:
            return "No comments."
        lines = []
        for reply in replies[: args.get("limit", 10)]:
            lines.append(self._comment(reply))
            lines += ["  ↳ " + self._comment(sub) for sub in (reply.get("replies") or [])[:3]]
        return "\n".join(lines)

    async def frames(self, args: dict[str, Any]) -> ToolOutput:
        info = await self._view(args["video"])
        cid = self._cid(info, args.get("part"))
        length = next((p.get("duration", 0) for p in info.get("pages") or []
                       if p.get("cid") == cid), info.get("duration", 0))
        seconds = list(args["seconds"])[:MAX_FRAMES]
        if too_late := [s for s in seconds if s >= length]:
            raise BiliError(f"The video is {clock(length)} long: nothing at "
                            + ", ".join(clock(s) for s in too_late) + ".")
        data = await self._json(f"{API}/x/player/wbi/playurl", await self._signed(
            {"bvid": info["bvid"], "cid": cid, "qn": 32, "fnval": 1}))
        streams = (data or {}).get("durl") or []
        if not streams:
            raise BiliError("Bilibili gave no stream of this video to a visitor.")
        folder = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="kaden-bili-"))
        try:
            images = []
            for n, second in enumerate(seconds):
                out = folder / f"{n}.jpg"
                await self._cut(streams[0]["url"], second, out)
                raw = await asyncio.to_thread(out.read_bytes)
                if len(raw) > MAX_IMAGE_BYTES:
                    raise BiliError("A frame came out too large to look at.")
                images.append(Image("image/jpeg", base64.b64encode(raw).decode(),
                                    f"{info['bvid']}-{clock(second)}.jpg"))
        finally:
            await asyncio.to_thread(shutil.rmtree, folder, True)
        return ToolOutput(f"[Frames of {info['title']} at "
                          + ", ".join(clock(s) for s in seconds) + "]",
                          attachments=tuple(images))

    # ── Bilibili's API ────────────────────────────────────────────────────

    async def _view(self, video: str) -> dict[str, Any]:
        bvid = await self._bvid(video)
        response = await self._get(f"{API}/x/web-interface/view", {"bvid": bvid})
        body = response.json()
        if body.get("code") != 0 or not body.get("data"):
            raise BiliError(f"Video {bvid} not found on Bilibili "
                            f"({body.get('message') or body.get('code')}).")
        return body["data"]

    async def _bvid(self, video: str) -> str:
        """The BV id in `video`, following a b23.tv short link to the video it is for."""
        if match := _BV.search(video):
            return match.group(0)
        if short := _SHORT.search(video):  # where it leads says which video, unloaded
            client = await self._session()
            response = await client.get(short.group(0), follow_redirects=False)
            if match := _BV.search(response.headers.get("location", "")):
                return match.group(0)
        raise BiliError("Give the video as its BV id or a link to it.")

    @staticmethod
    def _cid(info: dict[str, Any], part: int | None) -> int:
        pages = info.get("pages") or []
        if part is None or part == 1 or not pages:
            return info["cid"]
        if part > len(pages):
            raise BiliError(f"The video has {len(pages)} parts.")
        return pages[part - 1]["cid"]

    async def _subtitles(self, bvid: str, cid: int) -> str:
        data = await self._json(f"{API}/x/player/wbi/v2",
                                await self._signed({"bvid": bvid, "cid": cid}))
        tracks = [t for t in ((data or {}).get("subtitle") or {}).get("subtitles") or []
                  if t.get("subtitle_url")]
        if not tracks:
            return "No subtitles open to visitors."
        track = tracks[0]
        url = track["subtitle_url"]
        body = (await self._get("https:" + url if url.startswith("//") else url, {})).json()
        lines = [f"[{clock(line['from'])}] {line['content']}" for line in body.get("body", [])]
        return "\n".join([f"Subtitles ({track.get('lan_doc') or track.get('lan')}):", *lines])

    @staticmethod
    def _comment(reply: dict[str, Any]) -> str:
        return (f"{reply.get('member', {}).get('uname', '?')} "
                f"({reply.get('like', 0):,} likes): {reply.get('content', {}).get('message', '')}")

    async def _json(self, url: str, params: dict[str, Any]) -> Any:
        body = (await self._get(url, params)).json()
        if body.get("code") != 0:
            raise BiliError(f"Bilibili refused: {body.get('message') or body.get('code')}")
        return body.get("data")

    async def _get(self, url: str, params: dict[str, Any]) -> httpx2.Response:
        client = await self._session()
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response

    async def _session(self) -> httpx2.AsyncClient:
        """A visitor's session: the home page first, for the cookie it gives visitors."""
        async with self._starting:  # calls at once share the one that is starting
            if self._client is None:
                client = httpx2.AsyncClient(transport=self._transport, headers=HEADERS,
                                            follow_redirects=True, timeout=TIMEOUT)
                try:
                    (await client.get("https://www.bilibili.com/")).raise_for_status()
                except BaseException:
                    await client.aclose()
                    raise
                self._client = client
            return self._client

    async def _signed(self, params: dict[str, Any]) -> dict[str, Any]:
        """`params` signed as Bilibili's pages sign theirs (WBI)."""
        if self._mixin is None or time.time() - self._mixin[1] > KEYS_FOR_S:
            client = await self._session()
            images = (await client.get(f"{API}/x/web-interface/nav")).json()["data"]["wbi_img"]
            keys = "".join(url.rsplit("/", 1)[1].split(".")[0]
                           for url in (images["img_url"], images["sub_url"]))
            self._mixin = ("".join(keys[i] for i in _MIXIN)[:32], time.time())
        signed = {k: re.sub(r"[!'()*]", "", str(v))
                  for k, v in sorted({**params, "wts": int(time.time())}.items())}
        query = urllib.parse.urlencode(signed)
        signed["w_rid"] = hashlib.md5((query + self._mixin[0]).encode()).hexdigest()
        return signed

    async def _ffmpeg_cut(self, url: str, second: float, out: Path) -> None:
        assert self._ffmpeg is not None
        headers = "".join(f"{k}: {v}\r\n" for k, v in HEADERS.items())
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg, "-y", "-loglevel", "error", "-headers", headers,
            "-ss", f"{second:.3f}", "-i", url, "-frames:v", "1",
            "-vf", f"scale=-2:{FRAME_HEIGHT}", "-q:v", "3", str(out),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _out, err = await asyncio.wait_for(process.communicate(), CUT_TIMEOUT)
        except TimeoutError:
            raise BiliError(f"The frame at {clock(second)} took too long to cut.") from None
        finally:  # too slow, or stopped
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0 or not out.exists():
            raise BiliError(f"The frame at {clock(second)} could not be cut: "
                            + err.decode(errors="replace").strip()[-300:])

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
